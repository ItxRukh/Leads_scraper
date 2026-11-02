"""Phase 4 and 5: verification, handing leads to Instantly, Instantly's events, and GoHighLevel."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import email_verification as ev
import ghl_sync
import import_api
import instantly_client
import instantly_events
import instantly_handoff
import integrations_api
import message_service as ms
import message_writer
import scheduling as sch
import industry_profiles as ip
from config import settings
from database import (DNCList, EmailLog, EmailStatus, InboundMessage, Lead, LeadSource, LeadTier, OutreachMessage,
                      OutreachQueueEntry, PipelineStage, get_db)

NOW = datetime(2026, 11, 3, 15, 0, 0)               # a Tuesday, 15:00 UTC


@pytest.fixture
def cfg(monkeypatch):
    """The Settings values for a test: Instantly mode, a campaign, a postal address, keys present."""
    store = {"sendProvider": "instantly", "instantlyCampaignId": "camp-12345678", "companyName": "Leadwren",
             "companyAddress": "100 Main St, Austin, TX 78701", "verifyBeforeSend": True}
    monkeypatch.setattr(ms, "get_app_settings", lambda: dict(store))
    monkeypatch.setattr(instantly_handoff, "get_app_settings", lambda: dict(store))
    monkeypatch.setattr(integrations_api, "get_app_settings", lambda: dict(store))
    monkeypatch.setattr(settings, "INSTANTLY_API_KEY", "test-key")
    return store


def make_lead(db, email="dana@acme.example", tier=LeadTier.HOT, **kwargs):
    lead = Lead(name="Acme Heating", email=email, website="https://acme.example", niche="HVAC", city="Austin",
                source=LeadSource.MANUAL, tier=tier, final_score=90, linkedin_name="Dr. Dana Rivera",
                profile_id=db.query(ip.IndustryProfile).filter_by(slug="hvac").one().id, timezone="America/Chicago",
                attributes={"state": "TX", "industry_key": "hvac",
                            "top_gaps": [{"label": "No after-hours mention", "points": 17, "fix": "After-Hours Call Coverage"}]},
                **kwargs)
    db.add(lead)
    db.commit()
    return lead


def queue(db, lead):
    entry = OutreachQueueEntry(lead_id=lead.id, status="queued", tier=lead.tier.value, score=lead.final_score)
    db.add(entry)
    db.commit()
    sch.schedule_entry(db, entry, NOW)
    return entry


# ─── verification ────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def test_a_dead_domain_is_never_sent_to_the_paid_verifier(monkeypatch):
    monkeypatch.setattr(settings, "MILLIONVERIFIER_API_KEY", "key")
    monkeypatch.setattr(ev.requests, "get", lambda *a, **k: pytest.fail("the paid check must not run"))
    assert ev.verify_address("not-an-email")["status"] == "invalid"


@pytest.mark.parametrize("result,status", [("ok", "valid"), ("catch_all", "catch-all"), ("invalid", "invalid"),
                                           ("disposable", "invalid"), ("unknown", "unknown"), ("something_new", "unknown")])
def test_millionverifier_results_map_to_our_statuses(monkeypatch, result, status):
    monkeypatch.setattr(settings, "MILLIONVERIFIER_API_KEY", "key")
    monkeypatch.setattr("scripts.email_verify.verify_email_local", lambda e: {"status": "valid", "sub_status": "mx_found"})
    monkeypatch.setattr(ev.requests, "get", lambda *a, **k: FakeResponse({"result": result, "subresult": "x", "error": ""}))
    out = ev.verify_address("dana@acme.example")
    assert out["status"] == status and out["provider"] == "millionverifier"


def test_a_failing_paid_service_never_condemns_an_address(monkeypatch):
    monkeypatch.setattr(settings, "MILLIONVERIFIER_API_KEY", "key")
    monkeypatch.setattr("scripts.email_verify.verify_email_local", lambda e: {"status": "valid", "sub_status": "mx_found"})
    monkeypatch.setattr(ev.requests, "get", lambda *a, **k: FakeResponse({"result": "", "error": "insufficient credits"}))
    out = ev.verify_address("dana@acme.example")
    assert out["status"] == "valid" and out["provider"] == "free" and "insufficient credits" in out["detail"]


def test_without_a_key_only_the_free_check_runs(monkeypatch):
    monkeypatch.setattr(settings, "MILLIONVERIFIER_API_KEY", "")
    monkeypatch.setattr(settings, "ZEROBOUNCE_API_KEY", "")
    monkeypatch.setattr("scripts.email_verify.verify_email_local", lambda e: {"status": "valid", "sub_status": "mx_found"})
    assert ev.verify_address("a@b.example")["provider"] == "free"


def test_batch_verification_checks_every_lead_with_an_email_once(seeded_db, monkeypatch):
    db = seeded_db
    hot, cold = make_lead(db, "hot@a.example", import_batch_id=7), make_lead(db, "cold@a.example", tier=LeadTier.COLD, import_batch_id=7)
    no_email = make_lead(db, None, import_batch_id=7)
    asked = []
    monkeypatch.setattr(ev, "verify_address", lambda e: asked.append(e) or {"status": "valid", "provider": "millionverifier", "detail": "ok"})

    counts = ev.verify_batch(7, db=db)

    assert sorted(asked) == ["cold@a.example", "hot@a.example"] and counts["valid"] == 2      # no tier is skipped
    assert hot.email_status == EmailStatus.VALID and hot.email_verified and hot.attributes["email_check"]["provider"] == "millionverifier"
    assert cold.email_status == EmailStatus.VALID and no_email.email_status != EmailStatus.VALID
    assert ev.verify_batch(7, db=db)["checked"] == 0                           # already checked: nothing is paid for twice
    status = ev.batch_status(db, 7)
    assert status["valid"] == 2 and status["to_check"] == 2 and status["no_email"] == 1
    assert ev.verify_batch(7, db=db, tiers=(LeadTier.HOT,))["checked"] == 0


# ─── handing a lead to Instantly ─────────────────────────────────────────────

def drafted_lead(db, cfg_store=None, auto=False):
    lead = make_lead(db)
    queue(db, lead)
    return lead


def write_fake(monkeypatch):
    texts = iter(f"Hello {n}.\n\nSecond paragraph {n}." for n in range(1, 10))
    monkeypatch.setattr(ms, "write_message",
                        lambda lead, step, steps, config, previous: message_writer.Draft(f"Subject {step.key}", next(texts)))


def test_instantly_mode_writes_the_whole_sequence_at_once(seeded_db, cfg, monkeypatch):
    db = seeded_db
    write_fake(monkeypatch)
    lead = drafted_lead(db)
    sch.save_settings(sch.ScheduleSettings())

    assert ms.ensure_drafts(db, NOW) == 1
    msgs = db.query(OutreachMessage).filter_by(lead_id=lead.id).order_by(OutreachMessage.id).all()
    assert [m.step_key for m in msgs] == ["email_1", "email_2", "email_3", "email_4"]
    assert [m.kind for m in msgs] == ["step", "followup", "followup", "followup"]
    assert msgs[1].subject == "Re: Subject email_1"                              # follow-ups continue the first thread
    assert msgs[2].body != msgs[1].body

    shown = ms.list_messages(db, "draft")
    assert len(shown) == 1 and len(shown[0]["followups"]) == 3                    # a person reviews one item per lead


def test_approving_the_first_email_approves_the_whole_sequence_and_discarding_drops_it(seeded_db, cfg, monkeypatch):
    db = seeded_db
    write_fake(monkeypatch)
    lead = drafted_lead(db)
    ms.ensure_drafts(db, NOW)
    first = db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").one()

    ms.approve(db, first.id, NOW)
    assert {m.status for m in db.query(OutreachMessage).filter_by(lead_id=lead.id)} == {"approved"}

    ms.discard(db, first.id)
    assert {m.status for m in db.query(OutreachMessage).filter_by(lead_id=lead.id)} == {"discarded"}


def test_hand_off_adds_the_lead_with_all_emails_as_variables(seeded_db, cfg, monkeypatch):
    db = seeded_db
    write_fake(monkeypatch)
    lead = drafted_lead(db)
    ms.ensure_drafts(db, NOW)
    first = db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").one()
    ms.approve(db, first.id, NOW)
    monkeypatch.setattr(ev, "verify_address", lambda e: {"status": "valid", "provider": "free", "detail": "mx_found"})
    sent = {}
    monkeypatch.setattr(instantly_client, "create_lead",
                        lambda campaign, email, **kw: sent.update(campaign=campaign, email=email, **kw) or "inst-lead-1")

    result = ms.send_due_emails(db, NOW + timedelta(days=1))

    assert result == {"sent": 1, "failed": 0, "held": 0}
    assert sent["campaign"] == "camp-12345678" and sent["email"] == "dana@acme.example"
    assert sent["first_name"] == "Dana" and sent["last_name"] == "Rivera"           # "Dr." is a title, not a name
    v = sent["custom_variables"]
    assert v["lead_id"] == lead.id and v["industry"] == "HVAC" and v["email_steps"] == 4
    assert v["email_1_subject"] == "Subject email_1" and v["email_2_subject"] == "Re: Subject email_1"
    assert "<p>Hello 1.</p><p>Second paragraph 1.</p>" in v["email_1_body"]
    assert "100 Main St, Austin, TX 78701" in v["email_4_body"]                      # the postal address is in every email
    assert v["top_gap_1"] == "No after-hours mention"

    statuses = [m.status for m in db.query(OutreachMessage).filter_by(lead_id=lead.id).order_by(OutreachMessage.id)]
    assert statuses == ["sent", "handed_off", "handed_off", "handed_off"]
    entry = db.query(OutreachQueueEntry).filter_by(lead_id=lead.id).one()
    assert entry.sequence_status == "handed_off" and entry.next_step_key is None   # our scheduler leaves it to Instantly
    assert db.get(Lead, lead.id).pipeline_stage == PipelineStage.EMAIL_SENT


@pytest.mark.parametrize("verdict,expected", [("invalid", {"sent": 0, "failed": 1, "held": 0}),
                                              ("catch-all", {"sent": 0, "failed": 0, "held": 1})])
def test_bad_or_risky_addresses_are_not_handed_over(seeded_db, cfg, monkeypatch, verdict, expected):
    db = seeded_db
    write_fake(monkeypatch)
    lead = drafted_lead(db)
    ms.ensure_drafts(db, NOW)
    ms.approve(db, db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").one().id, NOW)
    monkeypatch.setattr(ev, "verify_address", lambda e: {"status": verdict, "provider": "millionverifier", "detail": "x"})
    monkeypatch.setattr(instantly_client, "create_lead", lambda *a, **k: pytest.fail("must not be handed over"))

    assert ms.send_due_emails(db, NOW + timedelta(days=1)) == expected


def test_nothing_is_handed_over_without_a_postal_address(seeded_db, cfg, monkeypatch):
    db = seeded_db
    cfg["companyAddress"] = ""
    write_fake(monkeypatch)
    lead = drafted_lead(db)
    ms.ensure_drafts(db, NOW)
    ms.approve(db, db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").one().id, NOW)
    monkeypatch.setattr(instantly_client, "create_lead", lambda *a, **k: pytest.fail("must not be handed over"))
    assert ms.send_due_emails(db, NOW + timedelta(days=1)) == {"sent": 0, "failed": 0, "held": 0}


def test_an_instantly_error_is_retried_not_lost(seeded_db, cfg, monkeypatch):
    db = seeded_db
    write_fake(monkeypatch)
    lead = drafted_lead(db)
    ms.ensure_drafts(db, NOW)
    first = db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").one()
    ms.approve(db, first.id, NOW)
    monkeypatch.setattr(ev, "verify_address", lambda e: {"status": "valid", "provider": "free", "detail": ""})

    def boom(*a, **k):
        raise instantly_client.InstantlyError("Instantly said 429 (rate limiting)")
    monkeypatch.setattr(instantly_client, "create_lead", boom)

    assert ms.send_due_emails(db, NOW + timedelta(days=1))["failed"] == 1
    db.refresh(first)
    assert first.status == "approved" and first.attempts == 1 and "429" in first.error     # tried again later


# ─── Instantly's client ──────────────────────────────────────────────────────

def test_an_old_v1_address_in_env_is_ignored(monkeypatch):
    monkeypatch.setattr(settings, "INSTANTLY_BASE_URL", "https://api.instantly.ai/api/v1")
    assert instantly_client.base_url() == "https://api.instantly.ai/api/v2"


def test_create_lead_sends_the_v2_body_and_bearer_key(monkeypatch):
    monkeypatch.setattr(settings, "INSTANTLY_API_KEY", "secret-key")
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"id": "lead-uuid"}

    def fake(method, url, **kw):
        seen.update(method=method, url=url, **kw)
        return Resp()
    monkeypatch.setattr(instantly_client.requests, "request", fake)

    assert instantly_client.create_lead("camp", "a@b.example", first_name="Dana", custom_variables={"lead_id": 5}) == "lead-uuid"
    assert seen["url"] == "https://api.instantly.ai/api/v2/leads" and seen["headers"]["Authorization"] == "Bearer secret-key"
    assert seen["json"] == {"campaign": "camp", "email": "a@b.example", "skip_if_in_workspace": True,
                            "custom_variables": {"lead_id": 5}, "first_name": "Dana"}


@pytest.mark.parametrize("code,fragment", [(401, "API key was not accepted"), (402, "plan"), (429, "rate limiting")])
def test_instantly_errors_are_explained(monkeypatch, code, fragment):
    monkeypatch.setattr(settings, "INSTANTLY_API_KEY", "k")

    class Resp:
        status_code = code
        text = "x"

        def json(self):
            return {"message": "nope"}
    monkeypatch.setattr(instantly_client.requests, "request", lambda *a, **k: Resp())
    with pytest.raises(instantly_client.InstantlyError, match=fragment):
        instantly_client.list_campaigns()


# ─── Instantly's events ──────────────────────────────────────────────────────

@pytest.fixture
def handed_off(seeded_db, cfg, monkeypatch):
    db = seeded_db
    write_fake(monkeypatch)
    lead = drafted_lead(db)
    ms.ensure_drafts(db, NOW)
    ms.approve(db, db.query(OutreachMessage).filter_by(lead_id=lead.id, kind="step").one().id, NOW)
    monkeypatch.setattr(ev, "verify_address", lambda e: {"status": "valid", "provider": "free", "detail": ""})
    monkeypatch.setattr(instantly_client, "create_lead", lambda *a, **k: "inst-1")
    ms.send_due_emails(db, NOW + timedelta(days=1))
    return db, lead


def reply(lead, text, **extra):
    return {"event_type": "reply_received", "lead_email": lead.email, "lead_id": lead.id, "timestamp": "2026-11-05T10:00:00Z",
            "reply_text": text, **extra}


def test_an_interested_reply_is_recorded_labelled_and_sent_to_ghl(handed_off, monkeypatch):
    db, lead = handed_off
    import reply_ai
    monkeypatch.setattr(reply_ai, "classify_reply", lambda lead, text: ("interested", "Wants a call"))
    monkeypatch.setattr(settings, "GHL_WEBHOOK_URL", "https://ghl.example/hook")
    posted = {}

    class Resp:
        status_code = 200
        text = "ok"
    monkeypatch.setattr(ghl_sync.requests, "post", lambda url, json, timeout: posted.update(url=url, json=json) or Resp())
    monkeypatch.setattr(ghl_sync, "_suggest_reply", lambda db, lead, inbound: "Happy to help. When suits you?")

    out = instantly_events.handle_event(db, reply(lead, "Yes, interested. Can we talk Thursday?"), NOW)

    assert out == {"status": "ok", "label": "interested", "ghl": "sent"}
    p = posted["json"]
    assert posted["url"] == "https://ghl.example/hook" and p["source"] == "outbound-prospecting" and p["event"] == "interested"
    assert p["email"] == lead.email and p["industryKey"] == "hvac" and "industry-hvac" in p["tags"] and "interested" in p["tags"]
    assert p["matchingServices"] == ["After-Hours Call Coverage"] and p["suggestedReply"] == "Happy to help. When suits you?"
    assert p["replyText"].startswith("Yes, interested") and p["isTest"] is False

    db.refresh(lead)
    assert lead.pipeline_stage == PipelineStage.REPLIED and lead.attributes["ghl"]["status"] == "sent"
    assert db.query(OutreachQueueEntry).filter_by(lead_id=lead.id).one().sequence_status == sch.STOPPED
    assert instantly_events.handle_event(db, reply(lead, "Yes, interested. Can we talk Thursday?"), NOW)["status"] == "duplicate"


def test_not_now_and_other_replies_are_recorded_but_not_sent_to_ghl(handed_off, monkeypatch):
    db, lead = handed_off
    import reply_ai
    monkeypatch.setattr(reply_ai, "classify_reply", lambda lead, text: ("not_now", "Busy until spring"))
    monkeypatch.setattr(settings, "GHL_WEBHOOK_URL", "https://ghl.example/hook")
    monkeypatch.setattr(ghl_sync.requests, "post", lambda *a, **k: pytest.fail("not for GHL"))
    assert instantly_events.handle_event(db, reply(lead, "Busy until spring, try later"), NOW) == {"status": "ok", "label": "not_now"}


def test_a_stop_reply_puts_the_lead_on_the_dnc_list(handed_off):
    db, lead = handed_off
    instantly_events.handle_event(db, reply(lead, "STOP"), NOW)
    db.refresh(lead)
    assert lead.do_not_contact and db.query(DNCList).filter_by(email=lead.email).count() == 1


def test_unsubscribe_bounce_not_interested_and_meeting_events(handed_off, monkeypatch):
    db, lead = handed_off
    monkeypatch.setattr(settings, "GHL_WEBHOOK_URL", "")

    assert instantly_events.handle_event(db, {"event_type": "email_bounced", "lead_email": lead.email, "timestamp": "t"}, NOW)["status"] == "ok"
    db.refresh(lead)
    assert lead.email_status == EmailStatus.INVALID

    instantly_events.handle_event(db, {"event_type": "lead_not_interested", "lead_id": lead.id}, NOW)
    assert db.get(Lead, lead.id).pipeline_stage == PipelineStage.REJECTED

    instantly_events.handle_event(db, {"event_type": "lead_unsubscribed", "lead_email": lead.email}, NOW)
    assert db.get(Lead, lead.id).do_not_contact and db.query(DNCList).filter_by(email=lead.email).count() == 1


def test_a_later_email_going_out_is_recorded(handed_off):
    db, lead = handed_off
    instantly_events.handle_event(db, {"event_type": "email_sent", "lead_id": lead.id, "step": 2}, NOW + timedelta(days=3))
    statuses = [m.status for m in db.query(OutreachMessage).filter_by(lead_id=lead.id).order_by(OutreachMessage.id)]
    assert statuses == ["sent", "sent", "handed_off", "handed_off"]
    assert db.query(EmailLog).filter_by(lead_id=lead.id).count() == 2


def test_events_about_unknown_leads_or_unneeded_events_change_nothing(seeded_db):
    assert instantly_events.handle_event(seeded_db, {"event_type": "reply_received", "lead_email": "nobody@x.example"})["status"] == "ignored"
    lead = make_lead(seeded_db)
    assert instantly_events.handle_event(seeded_db, {"event_type": "link_clicked", "lead_id": lead.id})["status"] == "ignored"


# ─── GoHighLevel ─────────────────────────────────────────────────────────────

def test_ghl_is_skipped_without_a_url_and_sends_a_lead_only_once(seeded_db, monkeypatch):
    lead = make_lead(seeded_db)
    monkeypatch.setattr(settings, "GHL_WEBHOOK_URL", "")
    assert ghl_sync.push(seeded_db, lead, "interested")["status"] == "skipped"

    monkeypatch.setattr(settings, "GHL_WEBHOOK_URL", "https://ghl.example/hook")
    calls = []

    class Resp:
        status_code = 200
        text = ""
    monkeypatch.setattr(ghl_sync.requests, "post", lambda *a, **k: calls.append(1) or Resp())
    assert ghl_sync.push(seeded_db, lead, "interested")["status"] == "sent"
    assert ghl_sync.push(seeded_db, lead, "interested")["status"] == "already_sent"
    assert ghl_sync.push(seeded_db, lead, "manual", force=True)["status"] == "sent"
    assert len(calls) == 2


def test_a_rejected_ghl_push_is_recorded_so_it_can_be_retried(seeded_db, monkeypatch):
    lead = make_lead(seeded_db)
    monkeypatch.setattr(settings, "GHL_WEBHOOK_URL", "https://ghl.example/hook")

    class Resp:
        status_code = 422
        text = "bad workflow"
    monkeypatch.setattr(ghl_sync.requests, "post", lambda *a, **k: Resp())
    out = ghl_sync.push(seeded_db, lead, "interested")
    assert out["status"] == "failed" and "422" in out["error"]
    assert lead.attributes["ghl"]["status"] == "failed"
    monkeypatch.setattr(ghl_sync.requests, "post", lambda *a, **k: type("R", (), {"status_code": 200, "text": ""})())
    assert ghl_sync.push(seeded_db, lead, "interested")["status"] == "sent"      # a failed push is retried


# ─── the endpoints ───────────────────────────────────────────────────────────

@pytest.fixture
def api(seeded_db, cfg, monkeypatch):
    saved = {}
    monkeypatch.setattr(integrations_api, "save_app_settings", lambda data: saved.update(data) or cfg.update(data))
    app = FastAPI()
    app.include_router(integrations_api.router)
    app.include_router(import_api.router)
    app.dependency_overrides[get_db] = lambda: seeded_db
    return TestClient(app), saved, seeded_db


def test_status_lists_what_still_blocks_sending(api, cfg, monkeypatch):
    client, _, _ = api
    cfg["companyAddress"], cfg["instantlyCampaignId"] = "", ""
    monkeypatch.setattr(settings, "INSTANTLY_API_KEY", "")
    monkeypatch.setattr(settings, "DISPATCHER_ENABLED", False)
    body = client.get("/integrations/status").json()
    assert body["provider"] == "instantly" and len(body["blockers"]) == 4
    assert any("postal address" in b for b in body["blockers"])

    cfg.update(companyAddress="1 Main St", instantlyCampaignId="camp-12345678")
    monkeypatch.setattr(settings, "INSTANTLY_API_KEY", "k")
    monkeypatch.setattr(settings, "DISPATCHER_ENABLED", True)
    assert client.get("/integrations/status").json()["blockers"] == []


def test_choosing_a_campaign_a_provider_and_the_address_is_saved(api):
    client, saved, _ = api
    client.post("/integrations/instantly/campaign", json={"campaign_id": "camp-abcdefgh"})
    client.post("/integrations/provider", json={"provider": "smtp"})
    client.post("/integrations/company", json={"address": "  1 Main St,\n Austin TX ", "send_to_catch_all": True})
    assert saved == {"instantlyCampaignId": "camp-abcdefgh", "sendProvider": "smtp",
                     "companyAddress": "1 Main St, Austin TX", "sendToCatchAll": True}
    assert client.post("/integrations/provider", json={"provider": "carrier-pigeon"}).status_code == 422


def test_the_campaign_template_matches_the_variables_we_send(api):
    client, _, _ = api
    steps = client.get("/integrations/instantly/template").json()["steps"]
    assert [(s["step"], s["wait_days"]) for s in steps] == [(1, 0), (2, 3), (3, 4), (4, 7)]
    assert steps[0]["subject"] == "{{email_1_subject}}" and steps[3]["body"] == "{{email_4_body}}"


def test_webhooks_need_a_public_https_address_and_a_secret(api, monkeypatch):
    client, _, _ = api
    monkeypatch.setattr(settings, "INSTANTLY_WEBHOOK_SECRET", "")
    assert client.post("/integrations/instantly/webhooks", json={"public_url": "http://localhost:8000"}).status_code == 422
    assert client.post("/integrations/instantly/webhooks", json={"public_url": "https://x.example"}).status_code == 422

    monkeypatch.setattr(settings, "INSTANTLY_WEBHOOK_SECRET", "long-secret")
    created = []
    monkeypatch.setattr(instantly_client, "create_webhook", lambda url, event, secret, name=None: created.append((url, event, secret)))
    body = client.post("/integrations/instantly/webhooks", json={"public_url": "https://x.example/"}).json()
    assert body["target"] == "https://x.example/webhooks/instantly" and body["failed"] == []
    assert {e for _, e, _ in created} == set(instantly_client.WEBHOOK_EVENTS) and all(s == "long-secret" for *_, s in created)


def test_instantly_errors_reach_the_page_as_readable_messages(api, monkeypatch):
    client, _, _ = api
    monkeypatch.setattr(settings, "INSTANTLY_API_KEY", "")
    r = client.get("/integrations/instantly/campaigns")
    assert r.status_code == 502 and "INSTANTLY_API_KEY" in r.json()["detail"]


def test_the_import_page_can_verify_a_batch(api, monkeypatch):
    client, _, db = api
    make_lead(db, "x@a.example", import_batch_id=3)
    from database import ImportBatch
    db.add(ImportBatch(id=3, name="b"))
    db.commit()
    monkeypatch.setattr(ev, "verify_address", lambda e: {"status": "valid", "provider": "free", "detail": ""})
    monkeypatch.setattr(ev, "verify_batch", lambda *a, **k: None)       # the background job uses its own session
    assert client.post("/imports/3/verify").json() == {"started": True, "pending": 1}
    assert client.get("/imports/3/verify-status").json()["to_check"] == 1
    assert client.post("/imports/999/verify").status_code == 404
