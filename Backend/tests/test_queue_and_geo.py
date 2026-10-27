import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import queue_service as q
from database import Lead, LeadTier, PipelineStage
from geo import ensure_geo, infer_geo
from profile_service import build_query
from scripts.auditor import audit_lead


# ─── Geo ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("city,address,country,tz", [
    ("Miami FL", "123 Brickell Ave, Miami, FL 33131, USA", "United States", "America/New_York"),
    ("Austin", "500 Congress Ave, Austin, TX 78701, USA", "United States", "America/Chicago"),
    ("Phoenix AZ", None, "United States", "America/Phoenix"),
    ("Denver, CO", "", "United States", "America/Denver"),
    ("Lahore", "Mall Road, Lahore, Pakistan", "Pakistan", "Asia/Karachi"),
    ("Somewhere", None, None, None),
])
def test_infer_geo(city, address, country, tz):
    assert infer_geo(city, address) == (country, tz)


def test_ensure_geo_never_overwrites():
    lead = Lead(name="x", city="Miami FL", timezone="America/Los_Angeles")
    ensure_geo(lead)
    assert lead.timezone == "America/Los_Angeles"
    assert lead.country == "United States"


def test_build_query():
    assert build_query("{niche} wholesale supplier {city}", "toys", "Miami FL") == "toys wholesale supplier Miami FL"
    assert build_query("HVAC contractor {city}", "ignored", "Austin TX") == "HVAC contractor Austin TX"
    assert build_query("{niche} {city}", "plumbers", "") == "plumbers"


# ─── Queue ────────────────────────────────────────────────────────────────────

def add_lead(db, **kw):
    base = dict(name="Lead", email="a@b.com", city="Miami FL", tier=LeadTier.HOT, final_score=90)
    base.update(kw)
    lead = Lead(**base)
    db.add(lead)
    db.commit()
    return lead


def test_only_hot_and_warm_with_a_contact_are_queued(db):
    hot = add_lead(db, name="Hot")
    warm = add_lead(db, name="Warm", tier=LeadTier.WARM, final_score=50)
    cold = add_lead(db, name="Cold", tier=LeadTier.COLD, final_score=10)
    nocontact = add_lead(db, name="Nobody", email=None, phone=None)
    dnc = add_lead(db, name="Dnc", do_not_contact=True)

    assert q.enqueue_if_eligible(db, hot) is not None
    assert q.enqueue_if_eligible(db, warm) is not None
    assert q.enqueue_if_eligible(db, cold) is None
    assert q.enqueue_if_eligible(db, nocontact) is None
    assert q.enqueue_if_eligible(db, dnc) is None
    assert hot.timezone == "America/New_York"          # filled in when queued


def test_hot_only_setting(db, monkeypatch):
    monkeypatch.setattr(q, "get_app_settings", lambda: {"queueMinTier": "hot"})
    assert q.enqueue_if_eligible(db, add_lead(db, tier=LeadTier.WARM)) is None
    assert q.enqueue_if_eligible(db, add_lead(db, name="H2")) is not None


def test_not_added_twice_and_removed_leads_stay_removed(db):
    lead = add_lead(db)
    assert q.enqueue_if_eligible(db, lead) is not None
    assert q.enqueue_if_eligible(db, lead) is None
    q.set_status(db, lead.id, q.REMOVED)
    assert q.sync_queue(db) == 0                        # sweep respects the manual removal
    assert q.add_manually(db, lead.id).status == q.QUEUED


def test_sync_queues_everything_qualifying(db):
    for i in range(3):
        add_lead(db, name=f"L{i}", email=f"l{i}@b.com")
    add_lead(db, name="cold", tier=LeadTier.COLD)
    assert q.sync_queue(db) == 3
    assert q.sync_queue(db) == 0


def test_add_manually_rejects_unreachable_leads(db):
    lead = add_lead(db, email=None, phone=None)
    with pytest.raises(ValueError):
        q.add_manually(db, lead.id)
    with pytest.raises(LookupError):
        q.add_manually(db, 9999)


def test_list_queue_orders_by_score_and_counts(db):
    a = add_lead(db, name="Low", final_score=50, tier=LeadTier.WARM, email="l@b.com")
    b = add_lead(db, name="High", final_score=95, email="h@b.com")
    for lead in (a, b):
        q.enqueue_if_eligible(db, lead)
    q.set_status(db, a.id, q.PAUSED)
    out = q.list_queue(db)
    assert [i["name"] for i in out["items"]] == ["High"]
    assert out["counts"] == {"queued": 1, "paused": 1, "removed": 0}
    assert out["items"][0]["timezone"] == "America/New_York"
    assert [i["name"] for i in q.list_queue(db, status="paused")["items"]] == ["Low"]
    assert q.list_queue(db, search="hig")["total"] == 1


# ─── Auditing a lead queues it ────────────────────────────────────────────────

def test_audit_lead_scores_and_queues(seeded_db):
    lead = Lead(name="Dropper", email="d@x.com", website="https://x.com", website_crawled=True,
                crawled_text="we dropship", rating=4.8, review_count=100, address="1 A St, Miami, FL 33101, USA",
                city="Miami FL", dropship_mentioned=True, processing_fast=True, delivery_fast=True)
    seeded_db.add(lead)
    seeded_db.commit()

    audit_lead(lead, seeded_db, use_claude=False)

    assert lead.final_score == 100 and lead.tier == LeadTier.HOT
    assert lead.profile_id is not None
    assert lead.score_breakdown["rules"]
    out = q.list_queue(seeded_db)
    assert out["total"] == 1 and out["items"][0]["timezone"] == "America/New_York"


# ─── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db):
    import queue_api
    from database import get_db

    app = FastAPI()
    app.include_router(queue_api.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_queue_api(client, db):
    lead = add_lead(db)
    assert client.post("/outreach-queue/sync").json() == {"added": 1}
    body = client.get("/outreach-queue").json()
    assert body["total"] == 1 and body["items"][0]["lead_id"] == lead.id

    assert client.post(f"/outreach-queue/{lead.id}/pause").json()["status"] == "paused"
    assert client.get("/outreach-queue").json()["total"] == 0
    assert client.get("/outreach-queue?status=paused").json()["total"] == 1
    assert client.post(f"/outreach-queue/{lead.id}/resume").json()["status"] == "queued"
    assert client.post(f"/outreach-queue/{lead.id}/remove").json()["status"] == "removed"

    assert client.post("/outreach-queue/9999/pause").status_code == 404
    assert client.post("/outreach-queue/9999/add").status_code == 404
    assert client.get("/outreach-queue?status=bogus").status_code == 422
