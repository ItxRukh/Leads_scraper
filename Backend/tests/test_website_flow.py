"""The prepare-and-approve flow: weakness column, weakness check, first email, approval, progress, Leads page."""
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import email_verification as ev
import first_email
import import_api
import lead_import
import lead_journey
import message_service as ms
import message_writer
import pipeline_run
import weakness_check as wc
from database import ImportBatch, Lead, LeadSource, OutreachMessage, get_db
from test_instantly_flow import NOW, make_lead


@pytest.fixture
def ghl(monkeypatch):
    """The 'ghl' sending mode: emails are prepared here, GoHighLevel sends."""
    store = {"sendProvider": "ghl"}
    monkeypatch.setattr(ms, "get_app_settings", lambda: dict(store))
    return store


def audited_lead(db, weakness="no online booking, no chat", email="dana@acme.example", batch=None, **facts):
    """A lead whose website was audited: the facts the check works from are on it."""
    site = {"site_checked": True, "site_has_online_booking": False, "site_has_chat": False,
            "site_has_click_to_call": True, "site_has_contact_form": True, "site_mentions_after_hours": False,
            "site_has_reviews": True, "site_mobile_friendly": True, "site_https": True, **facts}
    lead = make_lead(db, email, audited_at=NOW, import_batch_id=batch)
    lead.crawled_text = "We fix heating and air conditioning in Austin. Call us any time. " * 10
    lead.attributes = {**lead.attributes, **site, "weakness": weakness, "weakness_original": weakness,
                       "top_gaps": [{"key": "booking", "label": "No online booking", "points": 20, "fix": "Online Booking"},
                                    {"key": "after_hours", "label": "No after-hours mention", "points": 15, "fix": "After-Hours Call Coverage"}]}
    db.commit()
    return lead


# ─── the weakness column in the import ───────────────────────────────────────

def test_the_import_reads_a_weakness_column_and_keeps_the_original(seeded_db):
    table = [["business_name", "email", "website", "industry", "city", "state", "issues"],
             ["Acme Heating", "dana@acme.example", "acme.example", "HVAC", "Austin", "TX", "no online booking, no chat"],
             ["Beta Dental", "office@beta.example", "beta.example", "Dental", "Round Rock", "TX", ""]]
    preview = lead_import.build_preview(seeded_db, table, use_ai=False)
    by_name = {r["data"]["business_name"]: r["data"] for r in preview["rows"]}
    assert by_name["Acme Heating"]["weakness"] == "no online booking, no chat"
    assert by_name["Beta Dental"]["weakness"] is None
    assert "weakness" in lead_import.sample_csv().splitlines()[0]


# ─── checking the weakness ───────────────────────────────────────────────────

def test_points_that_match_the_site_stay_exactly_as_written(seeded_db):
    lead = audited_lead(seeded_db)
    result = wc.check(lead)
    assert result["weakness"] == "no online booking, no chat" and result["edited"] is False and result["note"] == ""


def test_a_point_the_site_disproves_is_removed_and_the_rest_stays(seeded_db):
    lead = audited_lead(seeded_db, site_has_chat=True, site_chat_tool="Intercom")
    result = wc.check(lead)
    assert result["weakness"] == "no online booking" and result["edited"] is True
    assert "no chat" in result["note"]


def test_when_every_point_is_wrong_the_real_gaps_are_written(seeded_db):
    lead = audited_lead(seeded_db, site_has_online_booking=True, site_has_chat=True)
    result = wc.check(lead)
    assert result["weakness"] == "No online booking; No after-hours mention" and result["edited"] is True


def test_an_empty_weakness_is_filled_from_the_check(seeded_db):
    lead = audited_lead(seeded_db, weakness="")
    result = wc.check(lead)
    assert result["weakness"] == "No online booking; No after-hours mention"
    assert "taken from the website check" in result["note"]


def test_nothing_is_changed_when_the_site_could_not_be_checked(seeded_db):
    lead = audited_lead(seeded_db, site_checked=False, site_has_online_booking=True)
    assert wc.check(lead)["weakness"] == "no online booking, no chat"


def test_a_vague_claim_about_style_is_never_called_wrong_by_the_facts(seeded_db):
    lead = audited_lead(seeded_db, weakness="looks outdated", site_looks_outdated=False)
    assert wc.check(lead)["weakness"] == "looks outdated"          # a recent copyright year does not prove the design is modern


def test_an_unlisted_claim_is_dropped_only_when_the_ai_quotes_the_site(seeded_db, monkeypatch):
    lead = audited_lead(seeded_db, weakness="no online booking, no financing options")
    lead.crawled_text = "We fix heating. Financing options are available for every repair, ask us how it works. " * 6
    monkeypatch.setattr(wc, "_judge_other_points", wc._judge_other_points)       # the real function, with a fake AI below
    from utils import groq_helper

    monkeypatch.setattr(groq_helper, "_call_groq", lambda *a, **k: (
        '{"results": [{"claim": "no financing options", "present": true, '
        '"quote": "Financing options are available for every repair"}]}'))
    assert wc.check(lead)["weakness"] == "no online booking"

    monkeypatch.setattr(groq_helper, "_call_groq", lambda *a, **k: (          # a quote that is not on the page is ignored
        '{"results": [{"claim": "no financing options", "present": true, "quote": "We offer zero percent loans always"}]}'))
    assert wc.check(lead)["weakness"] == "no online booking, no financing options"

    monkeypatch.setattr(groq_helper, "_call_groq", lambda *a, **k: None)      # the AI is down: nothing is changed
    assert wc.check(lead)["weakness"] == "no online booking, no financing options"


def test_applying_the_check_keeps_the_original_and_marks_the_edit(seeded_db):
    lead = audited_lead(seeded_db, site_has_chat=True)
    wc.apply(lead)
    seeded_db.commit()
    assert lead.attributes["weakness"] == "no online booking" and lead.attributes["weakness_original"] == "no online booking, no chat"
    assert lead.attributes["weakness_edited"] is True and lead.attributes["weakness_checked"] is True


# ─── the first email ─────────────────────────────────────────────────────────

def ready_lead(db, email="dana@acme.example", status="valid", batch=None, **facts):
    lead = audited_lead(db, email=email, batch=batch, **facts)
    lead.attributes = {**lead.attributes, "email_check": {"status": status, "provider": "free", "detail": ""}}
    db.commit()
    return lead


@pytest.fixture
def writer(monkeypatch):
    seen = []

    def fake(lead, step, steps, config, previous):
        seen.append(message_writer._observations(lead))
        return message_writer.Draft("Quick idea for Acme", "Hi Dana,\n\nI couldn't find a way to book online.")
    monkeypatch.setattr(first_email, "write_message", fake)
    return seen


def test_the_writer_builds_on_the_checked_weakness(seeded_db):
    lead = audited_lead(seeded_db, weakness="no online booking; no after-hours info")
    notes = message_writer._observations(lead)
    assert notes[0].startswith("no online booking") and len(notes) == 2


def test_only_ready_leads_get_a_first_email_and_only_one(seeded_db, ghl, writer):
    db = seeded_db
    good = ready_lead(db)
    ready_lead(db, "bad@x.example", status="invalid")
    audited_lead(db, email="unchecked@x.example")                            # address not checked yet
    blocked = ready_lead(db, "dnc@x.example")
    blocked.do_not_contact = True
    db.commit()

    assert first_email.write_batch(db) == 1
    assert first_email.write_batch(db) == 0                                  # never written twice
    msg = db.query(OutreachMessage).filter_by(lead_id=good.id).one()
    assert (msg.status, msg.kind, msg.channel) == ("draft", "step", "email") and msg.subject == "Quick idea for Acme"
    assert writer[0][0].startswith("no online booking")


def test_a_rewrite_keeps_the_old_draft_until_the_new_one_exists(seeded_db, ghl, monkeypatch, writer):
    db = seeded_db
    lead = ready_lead(db)
    first_email.write_batch(db)
    old = db.query(OutreachMessage).filter_by(lead_id=lead.id).one()

    monkeypatch.setattr(first_email, "write_message", lambda *a: None)       # the AI is down
    with pytest.raises(ValueError, match="old one was kept"):
        first_email.rewrite(db, old)
    assert old.status == "draft"

    monkeypatch.setattr(first_email, "write_message", lambda *a: message_writer.Draft("New subject", "New body"))
    new = first_email.rewrite(db, old)
    assert old.status == "discarded" and new.status == "draft" and new.subject == "New subject"


def test_in_ghl_mode_nothing_is_sent_or_released_from_here(seeded_db, ghl, writer, monkeypatch):
    db = seeded_db
    lead = ready_lead(db)
    first_email.write_batch(db)
    msg = db.query(OutreachMessage).filter_by(lead_id=lead.id).one()

    ms.approve(db, msg.id, NOW)
    assert msg.status == "approved" and msg.release_at is None               # released to GoHighLevel later, by the GHL step
    assert ms.ensure_drafts(db, NOW) == 0
    monkeypatch.setattr("email_sender.send_email", lambda *a, **k: pytest.fail("nothing must be sent from here"))
    assert ms.send_due_emails(db, NOW) == {"sent": 0, "failed": 0}


# ─── progress of a whole import ──────────────────────────────────────────────

def test_the_checks_run_in_order_and_the_status_follows_them(seeded_db, ghl, writer, monkeypatch):
    db = seeded_db
    db.add(ImportBatch(id=5, name="b"))
    lead = ready_lead(db, batch=5)
    lead.attributes = {**lead.attributes, "email_check": None, "weakness_checked": False}
    db.commit()
    order = []
    monkeypatch.setattr(import_api, "audit_batch", lambda batch_id, db=None: order.append("audit"))
    monkeypatch.setattr(wc, "check_batch", lambda batch_id, db: order.append("weakness"))

    def verify(batch_id, db=None):
        order.append("verify")
        for item in db.query(Lead).filter(Lead.import_batch_id == batch_id):
            item.attributes = {**item.attributes, "email_check": {"status": "valid", "provider": "free", "detail": ""}}
        db.commit()
    monkeypatch.setattr(ev, "verify_batch", verify)

    assert pipeline_run.pipeline_status(db, 5)["stage"] == "stopped"          # not running and not finished
    assert pipeline_run.run_batch_pipeline(5, db=db) is True
    assert order == ["audit", "weakness", "verify"]
    status = pipeline_run.pipeline_status(db, 5)
    assert status["stage"] == "done" and status["emails"] == {"ready": 1, "written": 1}
    assert 5 not in pipeline_run.RUNNING


def test_a_failing_check_ends_the_run_and_shows_as_stopped(seeded_db, ghl, monkeypatch):
    db = seeded_db
    db.add(ImportBatch(id=6, name="b"))
    make_lead(db, "x@a.example", import_batch_id=6)

    def boom(batch_id, db=None):
        raise RuntimeError("audit crashed")
    monkeypatch.setattr(import_api, "audit_batch", boom)
    assert pipeline_run.run_batch_pipeline(6, db=db) is False
    assert pipeline_run.pipeline_status(db, 6)["stage"] == "stopped" and 6 not in pipeline_run.RUNNING


def test_the_import_page_starts_the_checks_and_follows_them(seeded_db, monkeypatch):
    db = seeded_db
    db.add(ImportBatch(id=7, name="b"))
    make_lead(db, "x@a.example", import_batch_id=7)
    started = []
    monkeypatch.setattr(pipeline_run, "run_batch_pipeline", lambda batch_id, job_id=None, db=None: started.append(batch_id))
    app = FastAPI()
    app.include_router(import_api.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    assert client.post("/imports/7/run").status_code == 202 and started == [7]
    assert client.post("/imports/999/run").status_code == 404
    first = client.get("/imports/7/pipeline").json()
    assert first["stage"] == "audit" and {"audit", "verify", "emails", "weakness"} <= set(first)       # running: the first step is shown
    pipeline_run.RUNNING.discard(7)


# ─── the Leads page and the approval page ────────────────────────────────────

def test_the_journey_has_a_step_for_every_stage_of_the_new_flow(seeded_db, ghl, writer):
    db = seeded_db

    def key(lead):
        db.commit()
        db.refresh(lead)
        return lead_journey.journeys(db, [lead])[lead.id]["key"]

    no_email = make_lead(db, None)
    assert key(no_email) == "no_email"
    lead = make_lead(db, "dana@acme.example")
    assert key(lead) == "to_audit"
    lead.audited_at = NOW
    assert key(lead) == "to_check"
    lead.attributes = {**lead.attributes, "email_check": {"status": "invalid"}}
    assert key(lead) == "bad_email"
    lead.attributes = {**lead.attributes, "email_check": {"status": "valid"}}
    assert key(lead) == "to_write"
    first_email.write_batch(db)
    assert key(lead) == "draft"
    ms.approve(db, db.query(OutreachMessage).filter_by(lead_id=lead.id).one().id, NOW)
    assert key(lead) == "approved"
    lead.attributes = {**lead.attributes, "ghl_contact": {"status": "sent"}}
    assert key(lead) == "in_ghl"
    lead.do_not_contact = True
    assert key(lead) == "dnc"


def test_the_leads_api_filters_by_import_and_summarises_the_steps(seeded_db, monkeypatch):
    import app as main
    from config import settings
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    db = seeded_db
    db.add_all([ImportBatch(id=1, name="old"), ImportBatch(id=2, name="new")])
    first = make_lead(db, "a@x.example", import_batch_id=1)
    second = make_lead(db, "b@x.example", import_batch_id=2)
    third = make_lead(db, "c@x.example", import_batch_id=2)
    main.app.dependency_overrides[main.get_db] = lambda: db
    try:
        client = TestClient(main.app)
        everything = client.get("/leads").json()["leads"]
        assert [lead["id"] for lead in everything] == [second.id, third.id, first.id]     # newest import first, as pasted
        assert [lead["id"] for lead in client.get("/leads?batch=2").json()["leads"]] == [second.id, third.id]
        summary = client.get("/leads/status-summary").json()
        assert summary["total"] == 3 and summary["by_step"] == {"to_audit": 3}
        assert "weakness" in everything[0] and "weakness_edited" in everything[0]
    finally:
        main.app.dependency_overrides.clear()


def test_the_approval_page_shows_the_weakness_and_can_rewrite(seeded_db, ghl, writer, monkeypatch):
    import app as main
    from config import settings
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", "")
    db = seeded_db
    lead = ready_lead(db, site_has_chat=True)
    wc.apply(lead)
    db.commit()
    first_email.write_batch(db)
    main.app.dependency_overrides[main.get_db] = lambda: db
    main.app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(main.app)
        item = client.get("/outreach-messages?status=draft").json()["items"][0]
        assert item["weakness"] == "no online booking" and item["weakness_edited"] is True and item["email_check"] == "valid"
        monkeypatch.setattr(first_email, "write_message", lambda *a: message_writer.Draft("Fresh", "Fresh body"))
        new_id = client.post(f"/outreach-messages/{item['id']}/rewrite").json()["id"]
        assert new_id != item["id"]
        assert [i["subject"] for i in client.get("/outreach-messages?status=draft").json()["items"]] == ["Fresh"]
        assert client.post("/outreach-messages/9999/rewrite").status_code == 404
    finally:
        main.app.dependency_overrides.clear()


def test_the_weakness_survives_the_import_and_is_kept_as_the_original(seeded_db):
    db = seeded_db
    app = FastAPI()
    app.include_router(import_api.router)
    app.dependency_overrides[get_db] = lambda: db
    body = {"batch_name": "weakness test", "source": "pasted text", "total_rows": 1,
            "rows": [{"row": 2, "business_name": "Acme Heating", "email": "dana@acme.example", "website": "acme.example",
                      "industry": "hvac", "city": "Austin", "state": "TX", "weakness": "no online booking, no chat"}]}
    assert TestClient(app).post("/imports/commit", json=body).json()["imported"] == 1
    lead = db.query(Lead).one()
    assert lead.attributes["weakness"] == "no online booking, no chat"
    assert lead.attributes["weakness_original"] == "no online booking, no chat"


# ─── the dashboard ───────────────────────────────────────────────────────────

def dashboard(db):
    import dashboard_api
    app = FastAPI()
    app.include_router(dashboard_api.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app).get("/dashboard/summary").json()


def test_the_dashboard_follows_leads_through_every_step(seeded_db, ghl, writer, monkeypatch):
    import dashboard_api
    from database import DNCList
    monkeypatch.setattr(dashboard_api.settings, "GHL_API_TOKEN", "", raising=False)
    db = seeded_db
    db.add(ImportBatch(id=3, name="First import", imported=4, duplicates=1))
    approved_lead = ready_lead(db, "a@x.example", batch=3)
    ready_lead(db, "b@x.example", status="catch-all", batch=3, weakness="")
    ready_lead(db, "c@x.example", status="invalid", batch=3)
    make_lead(db, None, import_batch_id=3)
    wc.apply(db.query(Lead).filter_by(email="a@x.example").one())
    first_email.write_batch(db)
    ms.approve(db, db.query(OutreachMessage).filter_by(lead_id=approved_lead.id).one().id, NOW)
    approved_lead.attributes = {**approved_lead.attributes, "ghl_contact": {"status": "sent"}, "ghl_flags": {"opened": True, "replied": True}}
    db.add_all([DNCList(email="stop@x.example", reason="opt_out_keyword: STOP"),
                DNCList(email="me@x.example", reason="Blocked by admin from the lead page"),
                DNCList(email="u@x.example", reason="Unsubscribed in GHL")])
    db.commit()

    out = dashboard(db)

    steps = {f["key"]: f["count"] for f in out["funnel"]}
    assert steps == {"imported": 4, "website": 3, "email": 2, "written": 2, "approved": 1, "ghl": 1, "replied": 1}
    assert out["email_quality"] == {"valid": 1, "catch_all": 1, "unknown": 0, "invalid": 1, "not_checked": 0, "no_email": 1}
    assert out["totals"] == {"leads": 4, "imports": 1, "drafts": 1, "approved": 1, "do_not_contact": 3}
    assert out["weakness"]["with_weakness"] >= 1 and out["weakness"]["without"] >= 1
    assert out["ghl"] == {"configured": False, "in_ghl": 1, "sent": 0, "opened": 1, "replied": 1, "bounced": 0, "unsubscribed": 0}
    assert {g["label"]: g["count"] for g in out["dnc"]["groups"]} == {
        "Asked to stop or unsubscribed": 1, "Added by you": 1, "Blocked or unsubscribed in GoHighLevel": 1}
    assert out["by_import"][0]["name"] == "First import" and out["by_import"][0]["leads"] == 4
    hvac = next(r for r in out["by_industry"] if r["industry"] == "HVAC")
    assert (hvac["leads"], hvac["approved"], hvac["in_ghl"]) == (4, 1, 1)
    assert len(out["daily_imports"]) == 14


def test_the_dashboard_says_what_needs_you_and_that_ghl_is_not_connected(seeded_db, ghl, writer, monkeypatch):
    import dashboard_api
    monkeypatch.setattr(dashboard_api.settings, "GHL_API_TOKEN", "", raising=False)
    ready_lead(seeded_db)
    first_email.write_batch(seeded_db)
    todo = {item["key"]: item for item in dashboard(seeded_db)["todo"]}
    assert todo["drafts"]["to"] == "/admin/approve" and "1 first email" in todo["drafts"]["text"]
    assert todo["ghl"]["tone"] == "bad" and "GHL_API_TOKEN" in todo["ghl"]["text"]

    monkeypatch.setattr(dashboard_api.settings, "GHL_API_TOKEN", "token", raising=False)
    monkeypatch.setattr(dashboard_api.settings, "GHL_LOCATION_ID", "loc", raising=False)
    assert "ghl" not in {item["key"] for item in dashboard(seeded_db)["todo"]}
