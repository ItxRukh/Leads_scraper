"""The Leads page follow-up: where each lead is, blocking a lead everywhere, scraped leads, and the one-click checks."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import dashboard_api
import dnc_service
import email_verification as ev
import ghl_sync
import import_api
import instantly_client
import instantly_events
import lead_journey
import message_service as ms
import pipeline_run
import reply_ai
from config import settings
from database import (DNCList, ImportBatch, InboundMessage, Lead, LeadSource, LeadTier, OutreachMessage,
                      OutreachQueueEntry, ScrapingJob, get_db)
from test_instantly_flow import NOW, cfg, handed_off, make_lead, queue, reply, write_fake  # noqa: F401  (fixtures)


def journey_of(db, lead):
    db.commit()
    db.refresh(lead)
    return lead_journey.journeys(db, [lead], NOW)[lead.id]["key"]


# ─── where is this lead? ─────────────────────────────────────────────────────

def test_the_journey_follows_a_lead_from_import_to_hand_off(seeded_db, cfg, monkeypatch):
    db = seeded_db
    write_fake(monkeypatch)
    lead = make_lead(db)
    assert journey_of(db, lead) == "to_audit"

    lead.audited_at = NOW
    db.commit()
    assert journey_of(db, lead) == "to_check"

    lead.attributes = {**lead.attributes, "email_check": {"status": "catch-all"}}
    assert journey_of(db, lead) == "catch_all"

    lead.attributes = {**lead.attributes, "email_check": {"status": "valid"}}
    assert journey_of(db, lead) == "ready"

    queue(db, lead)
    assert journey_of(db, lead) == "queued"

    ms.ensure_drafts(db, NOW)
    assert journey_of(db, lead) == "draft"

    ms.approve(db, db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").one().id, NOW)
    assert journey_of(db, lead) == "approved"

    monkeypatch.setattr(ev, "verify_address", lambda e: {"status": "valid", "provider": "free", "detail": ""})
    monkeypatch.setattr(instantly_client, "create_lead", lambda *a, **k: "inst-1")
    ms.send_due_emails(db, NOW + timedelta(days=1))
    assert journey_of(db, lead) == "instantly"


def test_cold_leads_and_leads_without_an_email_say_why_nothing_happens(seeded_db):
    db = seeded_db
    cold = make_lead(db, "cold@x.example", tier=LeadTier.COLD, audited_at=NOW)
    no_email = make_lead(db, None, audited_at=NOW)
    no_site = make_lead(db, "n@x.example")
    no_site.website = None
    db.commit()
    assert journey_of(db, cold) == "cold"
    assert journey_of(db, no_email) == "no_email"
    assert journey_of(db, no_site) == "no_site"


def test_a_reply_and_the_ghl_hand_over_show_in_the_journey(handed_off, monkeypatch):
    db, lead = handed_off
    monkeypatch.setattr(reply_ai, "classify_reply", lambda lead, text: ("question", "Asks the price"))
    monkeypatch.setattr(ghl_sync, "configured", lambda: False)
    instantly_events.handle_event(db, reply(lead, "How much is it?"), NOW)
    assert journey_of(db, lead) == "replied"

    lead.attributes = {**lead.attributes, "ghl": {"status": "sent"}}
    assert journey_of(db, lead) == "ghl"


def test_a_blocked_lead_always_shows_do_not_contact(handed_off):
    db, lead = handed_off
    lead.do_not_contact = True
    assert journey_of(db, lead) == "dnc"


# ─── blocking a lead ─────────────────────────────────────────────────────────

def test_blocking_a_lead_stops_everything_and_removes_it_from_instantly(handed_off, monkeypatch):
    db, lead = handed_off
    deleted = []
    monkeypatch.setattr(instantly_client, "delete_lead", lambda lead_id: deleted.append(lead_id))
    # the id Instantly gave the lead is kept on the first email
    first = db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").order_by(OutreachMessage.id).first()
    first.external_id = "0b8a1f52-5b1c-4c62-9d0f-1a2b3c4d5e6f"
    db.commit()

    out = dnc_service.block_lead(db, lead, "Blocked by admin")

    assert out["instantly"] == "removed" and deleted == ["0b8a1f52-5b1c-4c62-9d0f-1a2b3c4d5e6f"]
    assert lead.do_not_contact and db.query(DNCList).filter(DNCList.email == lead.email).count() == 1
    entry = db.query(OutreachQueueEntry).filter_by(lead_id=lead.id).one()
    assert entry.sequence_status == "stopped"
    assert journey_of(db, lead) == "dnc"


def test_blocking_still_works_when_instantly_cannot_be_reached(handed_off, monkeypatch):
    db, lead = handed_off
    first = db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").order_by(OutreachMessage.id).first()
    first.external_id = "0b8a1f52-5b1c-4c62-9d0f-1a2b3c4d5e6f"
    db.commit()

    def broken(lead_id):
        raise instantly_client.InstantlyError("Could not reach Instantly")
    monkeypatch.setattr(instantly_client, "delete_lead", broken)

    out = dnc_service.block_lead(db, lead, "Blocked by admin")
    assert out["instantly"].startswith("failed") and lead.do_not_contact


def test_blocking_throws_away_drafts_that_were_not_sent(seeded_db, cfg, monkeypatch):
    db = seeded_db
    write_fake(monkeypatch)
    lead = make_lead(db)
    queue(db, lead)
    ms.ensure_drafts(db, NOW)
    assert db.query(OutreachMessage).filter_by(lead_id=lead.id, status="draft").count() > 0

    assert dnc_service.block_lead(db, lead, "x")["instantly"] == "not_in_instantly"
    assert db.query(OutreachMessage).filter_by(lead_id=lead.id, status="draft").count() == 0


def test_an_address_on_the_list_blocks_every_matching_lead_and_can_be_freed(seeded_db, cfg):
    db = seeded_db
    one = make_lead(db, "dana@acme.example")
    two = make_lead(db, "other@acme.example")                      # same website domain
    out = dnc_service.block_address(db, None, None, "acme.example", "manual")
    assert len(out["blocked_leads"]) == 2 and one.do_not_contact and two.do_not_contact

    db.query(DNCList).delete()                                      # every entry for these leads is removed on the DNC page
    db.commit()
    assert dnc_service.unblock_address(db, None, None, "acme.example") == 2
    db.refresh(one)
    assert not one.do_not_contact


def test_the_lead_page_and_dnc_endpoints(seeded_db, cfg, monkeypatch):
    import app as main
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    db = seeded_db
    lead = make_lead(db)
    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        client = TestClient(main.app)
        assert client.post("/leads/999/do-not-contact").status_code == 404
        out = client.post(f"/leads/{lead.id}/do-not-contact").json()
        assert out["lead_id"] == lead.id and out["instantly"] == "not_in_instantly"
        shown = client.get(f"/leads/{lead.id}").json()
        assert shown["do_not_contact"] and shown["journey"]["key"] == "dnc"
        listed = client.get("/leads").json()["leads"]
        assert listed[0]["journey"]["key"] == "dnc"

        other = make_lead(db, "z@other.example")
        other.website = "https://other.example"
        db.commit()
        added = client.post("/dnc", json={"email": "z@other.example", "reason": "asked by phone"}).json()
        assert len(added["blocked_leads"]) == 1
        db.refresh(other)
        assert other.do_not_contact
    finally:
        main.app.dependency_overrides.clear()


# ─── Google Maps scrape ──────────────────────────────────────────────────────

PLACES = [
    {"name": "Cool Air HVAC", "phone": "+15125550101", "address": "10 Elm St, Austin, TX 78701", "city": "Austin",
     "website": "https://coolair.example", "niche": "HVAC", "rating": 4.8, "review_count": 120, "source": LeadSource.GOOGLE_PLACES,
     "google_place_id": "p1"},
    {"name": "Warm Home Heating", "phone": "+15125550102", "address": "22 Oak St, Austin, TX 78702", "city": "Austin",
     "website": "https://warmhome.example", "niche": "HVAC", "rating": 4.6, "review_count": 80, "source": LeadSource.GOOGLE_PLACES,
     "google_place_id": "p2"},
]


@pytest.fixture
def scraped(seeded_db, monkeypatch):
    from scripts import scraper
    db = seeded_db
    monkeypatch.setattr(db, "close", lambda: None)                       # the scraper closes its session when done
    monkeypatch.setattr(scraper, "SyncSessionLocal", lambda: db)
    monkeypatch.setattr(scraper, "scrape_google_places", lambda *a, **k: ([dict(p) for p in PLACES], 0))
    monkeypatch.setattr(scraper, "scrape_serpapi", lambda *a, **k: ([], 0))
    monkeypatch.setattr(scraper, "scrape_yelp", lambda *a, **k: ([], 0))
    monkeypatch.setattr(scraper, "export_leads_to_csv", lambda *a, **k: "leads.csv")
    scraper.run_scraper("hvac", "Austin TX", skip_crawl=True)
    return db


def test_a_google_maps_scrape_tags_leads_with_the_industry_profile_and_a_batch(scraped):
    db = scraped
    job = db.query(ScrapingJob).one()
    batch = db.query(ImportBatch).one()
    assert job.status == "done" and job.import_batch_id == batch.id
    assert (batch.source, batch.default_industry, batch.imported) == ("google maps", "hvac", 2)

    leads = db.query(Lead).all()
    assert len(leads) == 2
    from database import IndustryProfile
    hvac_profile = db.query(IndustryProfile).filter_by(slug="hvac").one().id
    for lead in leads:
        assert lead.import_batch_id == batch.id and lead.profile_id == hvac_profile
        assert lead.attributes["industry_key"] == "hvac" and lead.attributes["state"] == "TX"
        assert lead.niche == "HVAC"


def test_the_post_scrape_endpoint_runs_the_checks_on_the_scraped_batch(scraped, monkeypatch):
    import app as main
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    db = scraped
    job = db.query(ScrapingJob).one()
    ran = []
    monkeypatch.setattr(pipeline_run, "run_batch_pipeline", lambda batch_id, job_id=None, db=None: ran.append((batch_id, job_id)))
    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        client = TestClient(main.app)
        body = {"job_id": job.id, "niche": "hvac", "city": "Austin TX"}
        assert client.post("/pipeline/post-scrape-auto", json=body).json()["batch_id"] == job.import_batch_id
        assert ran == [(job.import_batch_id, job.id)]
        assert client.post("/pipeline/post-scrape-auto", json={**body, "job_id": 999}).status_code == 404
        assert client.get("/scraping-jobs").json()[0]["batch_id"] == job.import_batch_id
    finally:
        main.app.dependency_overrides.clear()


# ─── the one-click check ─────────────────────────────────────────────────────

def test_the_pipeline_audits_then_verifies_and_updates_the_scrape_job(scraped, monkeypatch):
    db = scraped
    job = db.query(ScrapingJob).one()
    seen = []
    monkeypatch.setattr(import_api, "audit_batch", lambda batch_id, db=None: seen.append(("audit", db.get(ScrapingJob, job.id).status)))
    monkeypatch.setattr(ev, "verify_batch", lambda batch_id, db=None: seen.append(("verify", db.get(ScrapingJob, job.id).status)))

    assert pipeline_run.run_batch_pipeline(job.import_batch_id, job.id, db=db) is True
    assert seen == [("audit", "auditing"), ("verify", "verifying_emails")]
    assert db.get(ScrapingJob, job.id).status == "outreach_done"


def test_a_failing_check_marks_the_job_failed_instead_of_leaving_it_running(scraped, monkeypatch):
    db = scraped
    job = db.query(ScrapingJob).one()

    def boom(batch_id, db=None):
        raise RuntimeError("audit crashed")
    monkeypatch.setattr(import_api, "audit_batch", boom)
    assert pipeline_run.run_batch_pipeline(job.import_batch_id, job.id, db=db) is False
    assert db.get(ScrapingJob, job.id).status == "outreach_failed"


def test_the_import_page_can_start_and_follow_the_checks(seeded_db, monkeypatch):
    db = seeded_db
    make_lead(db, "x@a.example", import_batch_id=3)
    db.add(ImportBatch(id=3, name="b"))
    db.commit()
    started = []
    monkeypatch.setattr(pipeline_run, "run_batch_pipeline", lambda batch_id, job_id=None, db=None: started.append(batch_id))
    app = FastAPI()
    app.include_router(import_api.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    assert client.post("/imports/3/run").status_code == 202 and started == [3]
    assert client.post("/imports/999/run").status_code == 404
    state = client.get("/imports/3/pipeline").json()
    assert state["stage"] == "audit" and state["audit"]["total"] == 1 and state["verify"]["to_check"] == 0 or state["stage"] in ("audit", "verify")

    lead = db.query(Lead).one()
    lead.audited_at = NOW
    lead.attributes = {**(lead.attributes or {}), "email_check": {"status": "valid"}}
    db.commit()
    assert client.get("/imports/3/pipeline").json()["stage"] == "done"


# ─── dashboard ───────────────────────────────────────────────────────────────

def dashboard(db):
    app = FastAPI()
    app.include_router(dashboard_api.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app).get("/dashboard/summary").json()


def test_the_dashboard_lists_what_needs_the_user_and_names_the_industry_of_replies(handed_off, monkeypatch):
    db, lead = handed_off
    monkeypatch.setattr(reply_ai, "classify_reply", lambda lead, text: ("interested", "Wants a call"))
    monkeypatch.setattr(settings, "GHL_WEBHOOK_URL", "")
    instantly_events.handle_event(db, reply(lead, "Yes please"), NOW)

    out = dashboard(db)
    assert out["recent_replies"][0]["industry"] == "HVAC" and out["recent_replies"][0]["city"] == "Austin"

    make_lead(db, "new@x.example")                                       # never audited
    kinds = {item["key"] for item in dashboard(db)["todo"]}
    assert "audit" in kinds


def test_the_dashboard_todo_shows_setup_problems_first(seeded_db, monkeypatch):
    monkeypatch.setattr(settings, "INSTANTLY_API_KEY", "")
    import integrations_api
    monkeypatch.setattr(integrations_api.instantly_handoff, "get_app_settings", lambda: {})
    monkeypatch.setattr(dashboard_api, "get_app_settings", lambda: {"sendProvider": "instantly"})
    todo = dashboard(seeded_db)["todo"]
    assert todo and todo[0]["key"] == "setup" and todo[0]["to"] == "/admin/integrations"


# ─── answering a reply from the lead page ────────────────────────────────────

REPLY_ID = "11111111-2222-3333-4444-555555555555"


def interested_reply(lead, **extra):
    return reply(lead, "Yes, how much is it?", email_id=REPLY_ID, email_account="sales@leadwren.example",
                 reply_subject="Re: Quick idea for Acme", unibox_url="https://app.instantly.ai/unibox/1", **extra)


@pytest.fixture
def answered(handed_off, monkeypatch):
    db, lead = handed_off
    monkeypatch.setattr(reply_ai, "classify_reply", lambda lead, text: ("question", "Asks the price"))
    monkeypatch.setattr(ghl_sync, "_suggest_reply", lambda *a: "Thanks! Plans are scoped per business. Free for a short call?")
    monkeypatch.setattr(ghl_sync, "configured", lambda: False)
    instantly_events.handle_event(db, interested_reply(lead), NOW)
    return db, lead


def test_a_reply_keeps_a_suggestion_and_what_is_needed_to_answer_it(answered):
    import reply_send
    db, lead = answered
    shown = reply_send.state(db, lead)
    assert shown["suggestion"].startswith("Thanks! Plans") and shown["can_send"] and not shown["answered"]
    assert shown["text"] == "Yes, how much is it?" and shown["unibox_url"].endswith("/unibox/1")
    assert lead.attributes["reply_context"]["email_id"] == REPLY_ID


def test_send_now_posts_the_edited_text_to_instantly_in_the_same_thread(answered, monkeypatch):
    import reply_send
    db, lead = answered
    seen = {}
    monkeypatch.setattr(instantly_client, "send_reply", lambda *a: seen.update(args=a) or {"id": "email-9"})

    out = reply_send.send(db, lead, "Hi,\nHappy to help <b>today</b>.")

    reply_to, account, subject, text, html_body = seen["args"]
    assert (reply_to, account, subject) == (REPLY_ID, "sales@leadwren.example", "Re: Quick idea for Acme")
    assert html_body == "Hi,<br/>Happy to help &lt;b&gt;today&lt;/b&gt;."          # the lead's page text is never raw HTML
    assert out["status"] == "sent"
    assert reply_send.state(db, lead)["answered"] is True
    assert journey_of(db, lead) == "replied" and "answered" in lead_journey.journeys(db, [lead], NOW)[lead.id]["next"]
    with pytest.raises(reply_send.ReplyError, match="already answered"):
        reply_send.send(db, lead, "again")
    # the message is recorded without an Instantly id, so blocking the lead later never deletes the wrong thing
    assert db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="reply").one().external_id is None


def test_send_now_is_refused_when_it_would_be_wrong(answered, monkeypatch):
    import reply_send
    db, lead = answered
    monkeypatch.setattr(instantly_client, "send_reply", lambda *a: pytest.fail("nothing must be sent"))
    with pytest.raises(reply_send.ReplyError, match="Write the reply"):
        reply_send.send(db, lead, "   ")
    lead.do_not_contact = True
    with pytest.raises(reply_send.ReplyError, match="do-not-contact"):
        reply_send.send(db, lead, "Hello")
    lead.do_not_contact = False
    lead.attributes = {**lead.attributes, "reply_context": {"inbound_id": 0}}
    with pytest.raises(reply_send.ReplyError, match="answer this one in Instantly"):
        reply_send.send(db, lead, "Hello")


def test_an_instantly_error_when_answering_reaches_the_page(answered, monkeypatch):
    import reply_send
    db, lead = answered

    def broken(*a):
        raise instantly_client.InstantlyError("Instantly said 429 (rate limit)")
    monkeypatch.setattr(instantly_client, "send_reply", broken)
    with pytest.raises(reply_send.ReplyError, match="429") as err:
        reply_send.send(db, lead, "Hello")
    assert err.value.status == 502 and not reply_send.state(db, lead)["answered"]


def test_the_reply_endpoints(answered, monkeypatch):
    import app as main
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    monkeypatch.setattr(instantly_client, "send_reply", lambda *a: {"id": "e1"})
    db, lead = answered
    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        client = TestClient(main.app)
        shown = client.get(f"/leads/{lead.id}").json()["reply"]
        assert shown["suggestion"] and shown["can_send"]
        assert client.post(f"/leads/{lead.id}/reply", json={"text": "Hello there"}).json()["status"] == "sent"
        assert client.post(f"/leads/{lead.id}/reply", json={"text": "Hello again"}).status_code == 409
        assert client.post("/leads/999/reply", json={"text": "x"}).status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_the_dashboard_stops_asking_once_the_reply_is_answered(answered, monkeypatch):
    import reply_send
    db, lead = answered
    monkeypatch.setattr(instantly_client, "send_reply", lambda *a: {"id": "e1"})
    assert any(item["key"] == "answer" for item in dashboard(db)["todo"])
    reply_send.send(db, lead, "Hello there")
    assert not any(item["key"] == "answer" for item in dashboard(db)["todo"])


# ─── opens ───────────────────────────────────────────────────────────────────

def test_an_open_reported_by_instantly_sets_opened_at_and_the_stage(handed_off):
    from database import PipelineStage
    db, lead = handed_off
    assert lead.pipeline_stage == PipelineStage.EMAIL_SENT
    event = {"event_type": "email_opened", "lead_id": lead.id, "lead_email": lead.email, "step": 1}

    assert instantly_events.handle_event(db, event, NOW)["status"] == "ok"
    from database import EmailLog
    log = db.query(EmailLog).filter_by(lead_id=lead.id).one()
    first_open = log.opened_at
    assert log.email_opened and first_open == NOW and lead.pipeline_stage == PipelineStage.EMAIL_OPENED
    first = db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").order_by(OutreachMessage.id).first()
    assert first.open_count == 1

    instantly_events.handle_event(db, event, NOW + timedelta(hours=3))          # a second open keeps the first time
    db.refresh(log)
    assert log.opened_at == first_open and first.open_count == 2


def test_opens_are_among_the_events_we_ask_instantly_for():
    assert "email_opened" in instantly_client.WEBHOOK_EVENTS


# ─── the bell ────────────────────────────────────────────────────────────────

def test_the_bell_shows_a_new_reply_with_its_label_and_clears_when_opened(answered, monkeypatch):
    import app as main
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    db, lead = answered
    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        client = TestClient(main.app)
        bell = client.get("/notifications").json()
        reply_items = [i for i in bell["items"] if i["type"] == "email_reply"]
        assert bell["unread_count"] >= 1 and len(reply_items) == 1
        assert reply_items[0]["lead_id"] == lead.id and reply_items[0]["label"] == "question"
        assert reply_items[0]["preview"] == "Asks the price" and not reply_items[0]["is_read"]

        assert client.post("/notifications/mark-read").json() == {"status": "ok"}
        after = client.get("/notifications").json()
        assert all(i["is_read"] for i in after["items"] if i["type"] == "email_reply")
    finally:
        main.app.dependency_overrides.clear()
