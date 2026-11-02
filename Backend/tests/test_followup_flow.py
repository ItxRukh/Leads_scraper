"""The Leads page follow-up: where each lead is, blocking a lead everywhere, scraped leads, and the one-click checks."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
