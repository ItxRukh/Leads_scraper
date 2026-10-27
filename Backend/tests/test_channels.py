"""Reading replies, the WhatsApp extension contract, connection status and the message review API."""
import json
from datetime import datetime, timedelta
from email.message import EmailMessage

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import connections
import email_inbox as inbox
import email_sender
import message_service as ms
import message_writer as mw
import queue_service as q
import scheduling as sch
from config import settings
from database import (
    ConnectionStatus, DNCList, EmailLog, EmailStatus, InboundMessage, Lead, LeadTier,
    OutreachMessage, PipelineStage, WhatsAppLog,
)

MON_10AM_NY = datetime(2026, 10, 12, 14, 0)


@pytest.fixture()
def db(seeded_db):
    return seeded_db


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_ADDRESS", "sales@example.com")
    monkeypatch.setattr(settings, "EMAIL_APP_PASSWORD", "app-password")


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    def fake(system_prompt, user_prompt, max_tokens=1000, temperature=0.3):
        if "Return ONLY JSON" in system_prompt:
            return json.dumps({"subject": "Quick question", "body": "Hi there."})
        return "Hi, a short WhatsApp message."
    monkeypatch.setattr(mw, "_call_groq", fake)


def queue_lead(db, **kw):
    base = dict(name="Lead", email="owner@lead.com", phone="+13055550100", city="Miami FL",
                tier=LeadTier.HOT, final_score=90)
    base.update(kw)
    lead = Lead(**base)
    db.add(lead)
    db.commit()
    return lead, q.enqueue_if_eligible(db, lead)


def mail(sender="owner@lead.com", subject="Re: Quick question", body="Sounds good, tell me more.",
         message_id="<r1@lead.com>", **headers):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, "sales@example.com", subject, message_id
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return m


# ─── Reading the inbox ────────────────────────────────────────────────────────

def test_a_reply_is_recorded_and_stops_the_sequence(db, clock):
    lead, entry = queue_lead(db)
    db.add(EmailLog(lead_id=lead.id, subject="Quick question", body="..."))
    db.commit()

    assert inbox.handle_message(db, mail(body="Sounds good.\n\nOn Mon, Sam wrote:\n> the old text")) == "reply"

    log = db.query(EmailLog).one()
    assert log.replied and log.reply_content == "Sounds good." and log.is_read is False
    assert lead.pipeline_stage == PipelineStage.REPLIED
    sch.refresh_stops(db)
    assert entry.sequence_status == sch.STOPPED and entry.stop_reason == "replied"
    assert db.query(DNCList).count() == 0


def test_the_same_message_is_not_counted_twice(db, clock):
    queue_lead(db)
    assert inbox.handle_message(db, mail()) == "reply"
    assert inbox.handle_message(db, mail()) is None
    assert db.query(InboundMessage).count() == 1


def test_a_stop_reply_adds_the_lead_to_dnc(db, clock):
    lead, _ = queue_lead(db)
    inbox.handle_message(db, mail(body="STOP emailing me"))
    assert lead.do_not_contact is True
    assert db.query(DNCList).filter_by(email="owner@lead.com").count() == 1


def test_unknown_senders_and_auto_replies_are_ignored(db, clock):
    queue_lead(db)
    assert inbox.handle_message(db, mail(sender="stranger@else.com", message_id="<a>")) is None
    assert inbox.handle_message(db, mail(message_id="<b>", Auto_Submitted="auto-replied")) is None
    assert inbox.handle_message(db, mail(message_id="<c>", subject="Out of office: back Monday")) is None
    assert db.query(InboundMessage).count() == 0


def test_a_reply_from_another_address_is_matched_by_thread(db, clock):
    lead, _ = queue_lead(db)
    db.add(OutreachMessage(lead_id=lead.id, step_key="email_1", channel="email", subject="Hi",
                           body="x", status="sent", external_id="<ours@example.com>"))
    db.commit()
    assert inbox.handle_message(db, mail(sender="assistant@lead.com", In_Reply_To="<ours@example.com>")) == "reply"
    assert db.query(InboundMessage).one().lead_id == lead.id


def test_a_bounce_marks_the_address_invalid(db, clock):
    lead, entry = queue_lead(db)
    bounce = mail(sender="MAILER-DAEMON@googlemail.com", subject="Delivery Status Notification (Failure)",
                  body="Address not found", message_id="<bounce1>", X_Failed_Recipients="owner@lead.com")
    assert inbox.handle_message(db, bounce) == "bounce"
    assert lead.email_status == EmailStatus.INVALID and lead.email_verified is False
    # email steps are now skipped for this lead
    entry.next_due_at = None
    sch.schedule_entry(db, entry, recompute=True)
    assert entry.next_channel == "whatsapp"


def test_a_bounce_without_the_header_is_read_from_the_text(db, clock):
    lead, _ = queue_lead(db)
    bounce = mail(sender="postmaster@lead.com", subject="Undeliverable", message_id="<bounce2>",
                  body="Your message to owner@lead.com could not be delivered.")
    assert inbox.handle_message(db, bounce) == "bounce"


def test_quoted_text_is_stripped():
    text = "Yes please.\nCall me at 3.\n\nOn Tue, 13 Oct 2026, Sam <s@x.com> wrote:\n> hello\n> there"
    assert inbox.strip_quoted(text) == "Yes please.\nCall me at 3."


class FakeIMAP:
    """Enough of imaplib for poll_inbox. Like the real server, UID n:* may return an older last message."""
    def __init__(self, messages):
        self.messages, self.searches = messages, []

    def select(self, box, readonly=False):
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "search":
            self.searches.append(args)
            return "OK", [" ".join(str(u) for u in sorted(self.messages)).encode()]
        raw = self.messages[int(args[0])].as_bytes()
        return "OK", [(b"1 (UID 1 BODY[] {%d}" % len(raw), raw), b")"]

    def logout(self):
        pass


def test_poll_inbox_tracks_the_last_uid(db, clock, monkeypatch):
    queue_lead(db)
    fake = FakeIMAP({10: mail(message_id="<m10>"), 11: mail(sender="x@else.com", message_id="<m11>")})
    monkeypatch.setattr(inbox, "_connect", lambda: fake)

    assert inbox.poll_inbox(db, MON_10AM_NY) == {"replies": 1, "bounces": 0}
    assert fake.searches[0][1] == "SINCE"                       # first run: look back 14 days
    assert db.get(ConnectionStatus, "email").detail["imap_last_uid"] == 11

    fake.messages[12] = mail(message_id="<m12>")
    assert inbox.poll_inbox(db, MON_10AM_NY) == {"replies": 1, "bounces": 0}
    assert fake.searches[1] == (None, "UID", "12:*")                  # later runs: only newer mail
    assert db.get(ConnectionStatus, "email").detail["imap_last_uid"] == 12


def test_poll_inbox_records_a_connection_error(db, monkeypatch):
    def boom():
        raise OSError("network down")
    monkeypatch.setattr(inbox, "_connect", boom)
    assert inbox.poll_inbox(db) == {"replies": 0, "bounces": 0}
    row = db.get(ConnectionStatus, "email")
    assert row.state == "error" and "network down" in row.last_error
    assert connections.email_summary(db)["state"] == "error"


def test_poll_does_nothing_without_an_account(db, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_ADDRESS", "")
    assert inbox.poll_inbox(db) == {"replies": 0, "bounces": 0}


# ─── Connection status ────────────────────────────────────────────────────────

def test_email_summary_states(db, monkeypatch):
    assert connections.email_summary(db)["state"] == "unknown"
    connections.record(db, "email", state="ok", ok=True)
    assert connections.email_summary(db)["state"] == "ok"
    connections.record(db, "email", state="error", error="bad password")
    summary = connections.email_summary(db)
    assert summary["state"] == "error" and summary["last_error"] == "bad password"
    monkeypatch.setattr(settings, "EMAIL_APP_PASSWORD", "")
    assert connections.email_summary(db)["state"] == "not_configured"


def test_whatsapp_goes_offline_when_the_extension_falls_silent(db):
    now = datetime(2026, 10, 12, 14, 0)
    assert connections.whatsapp_summary(db, now)["state"] == "offline"
    connections.record(db, "whatsapp", state="connected", detail={"number": "13055550111"}, now=now)
    assert connections.whatsapp_summary(db, now + timedelta(seconds=60))["online"] is True
    stale = connections.whatsapp_summary(db, now + timedelta(seconds=120))
    assert stale["state"] == "offline" and stale["online"] is False and stale["last_seen_seconds"] == 120
    connections.record(db, "whatsapp", state="logged_out", now=now)
    assert connections.whatsapp_summary(db, now)["state"] == "logged_out"


# ─── WhatsApp extension contract ──────────────────────────────────────────────

@pytest.fixture()
def client(db):
    import connections_api
    import messages_api
    import whatsapp_api
    from database import get_db
    app = FastAPI()
    for module in (whatsapp_api, messages_api, connections_api):
        app.include_router(module.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def whatsapp_ready(db):
    """A lead whose first step is an approved WhatsApp message that is due now."""
    s = sch.load_settings()
    s.whatsapp.mode = "auto"
    sch.save_settings(s)
    lead, entry = queue_lead(db, email=None)                    # phone only: starts at WhatsApp
    ms.ensure_drafts(db)
    return lead, entry, db.query(OutreachMessage).filter_by(channel="whatsapp").one()


def test_heartbeat_sets_the_connection(client, db):
    r = client.post("/whatsapp/heartbeat", json={"state": "connected", "number": "13055550111"})
    assert r.json()["online"] is True and r.json()["number"] == "13055550111"
    assert client.post("/whatsapp/heartbeat", json={"state": "nonsense"}).status_code == 422
    assert client.get("/connections").json()["whatsapp"]["state"] == "connected"


def test_the_extension_claims_and_reports_a_message(client, db, clock):
    lead, entry, msg = whatsapp_ready(db)
    got = client.get("/whatsapp/queue").json()["messages"]
    assert got == [{"message_id": msg.id, "lead_id": lead.id, "lead_name": "Lead",
                    "phone": "13055550100", "text": msg.body}]
    assert client.get("/whatsapp/queue").json()["messages"] == []       # already claimed

    r = client.post("/whatsapp/report", json={"message_id": msg.id, "status": "sent", "external_id": "wa-1"})
    assert r.json() == {"status": "sent"}
    assert entry.next_step_key == "call_1"                               # email steps skipped (no email), WhatsApp done
    assert db.query(WhatsAppLog).filter_by(lead_id=lead.id).one().message_content == msg.body
    assert client.post("/whatsapp/report", json={"message_id": msg.id, "status": "sent"}).json()["note"]


def test_a_number_not_on_whatsapp_skips_the_step(client, db, clock):
    lead, entry, msg = whatsapp_ready(db)
    client.get("/whatsapp/queue")
    client.post("/whatsapp/report", json={"message_id": msg.id, "status": "not_on_whatsapp"})
    assert msg.status == "failed" and "not on WhatsApp" in msg.error
    assert entry.next_step_key != "whatsapp_1"


def test_a_failed_send_is_retried_later(client, db, clock):
    lead, entry, msg = whatsapp_ready(db)
    client.get("/whatsapp/queue")
    client.post("/whatsapp/report", json={"message_id": msg.id, "status": "failed", "error": "chat not found"})
    assert msg.status == "approved" and msg.attempts == 1
    assert client.get("/whatsapp/queue").json()["messages"] == []       # waits 5 minutes
    clock(MON_10AM_NY + timedelta(minutes=5))
    assert len(client.get("/whatsapp/queue").json()["messages"]) == 1


def test_a_number_that_cannot_be_dialled_is_skipped(client, db, clock):
    s = sch.load_settings()
    s.whatsapp.mode = "auto"
    sch.save_settings(s)
    lead, entry = queue_lead(db, email=None, phone="0300 1234567", country="Pakistan")
    ms.ensure_drafts(db)
    assert client.get("/whatsapp/queue").json()["messages"] == []
    msg = db.query(OutreachMessage).one()
    assert msg.status == "failed" and "international" in msg.error


def test_unknown_message_reports_are_rejected(client):
    assert client.post("/whatsapp/report", json={"message_id": 999, "status": "sent"}).status_code == 404


def test_an_inbound_whatsapp_reply_stops_the_lead(client, db, clock):
    lead, entry = queue_lead(db)
    r = client.post("/whatsapp/inbound", json={"phone": "13055550100", "text": "Yes, interested", "message_id": "m1"})
    assert r.json() == {"matched": True, "recorded": True, "lead_id": lead.id}
    assert client.post("/whatsapp/inbound", json={"phone": "13055550100", "text": "Yes", "message_id": "m1"}).json()["recorded"] is False
    assert client.post("/whatsapp/inbound", json={"phone": "19998887777", "text": "hi", "message_id": "m2"}).json()["matched"] is False
    sch.refresh_stops(db)
    assert entry.stop_reason == "replied"
    assert db.query(WhatsAppLog).one().replied is True


def test_a_whatsapp_stop_adds_to_dnc(client, db, clock):
    lead, _ = queue_lead(db)
    client.post("/whatsapp/inbound", json={"phone": "13055550100", "text": "STOP", "message_id": "m9"})
    assert lead.do_not_contact is True


@pytest.mark.parametrize("text,opt_out", [
    ("STOP", True), ("Stop please", True), ("stop.", True), ("Unsubscribe", True),
    ("Please remove me from your list", True),
    ("Stop by tomorrow at 3 and show me the details", False),
    ("Sounds interesting, tell me more", False), ("", False),
])
def test_what_counts_as_an_opt_out(text, opt_out):
    from replies import is_opt_out
    assert is_opt_out(text) is opt_out


# ─── Review API ───────────────────────────────────────────────────────────────

def test_review_and_approve_through_the_api(client, db, clock):
    lead, entry = queue_lead(db)
    ms.ensure_drafts(db)
    items = client.get("/outreach-messages").json()["items"]
    assert len(items) == 1 and items[0]["subject"] == "Quick question" and items[0]["to"] == "owner@lead.com"
    assert items[0]["step_label"] == "First email"
    mid = items[0]["id"]

    assert client.put(f"/outreach-messages/{mid}", json={"subject": "Hello", "body": "Edited"}).json()["status"] == "draft"
    assert client.put(f"/outreach-messages/{mid}", json={"subject": "", "body": "x"}).status_code == 422
    approved = client.post(f"/outreach-messages/{mid}/approve").json()
    assert approved["status"] == "approved" and approved["release_at"] == "2026-10-12T14:00:00Z"
    assert client.post(f"/outreach-messages/{mid}/approve").status_code == 422
    assert client.post("/outreach-messages/999/approve").status_code == 404
    assert client.get("/outreach-messages?status=approved").json()["items"][0]["body"] == "Edited"
    assert client.get("/outreach-messages?status=bogus").status_code == 422


def test_approve_all_discard_and_skip(client, db, clock):
    for i in range(2):
        queue_lead(db, name=f"L{i}", email=f"l{i}@lead.com", phone=f"+1305555010{i}")
    ms.ensure_drafts(db)
    ids = [m["id"] for m in client.get("/outreach-messages").json()["items"]]

    assert client.post(f"/outreach-messages/{ids[0]}/discard").json()["status"] == "discarded"
    assert client.post(f"/outreach-messages/{ids[1]}/skip").json()["status"] == "discarded"
    assert client.post("/outreach-messages/approve-all").json() == {"approved": 0}

    # the discarded step is written again, and so is the next step of the skipped one
    ms.ensure_drafts(db)
    assert client.post("/outreach-messages/approve-all?channel=email").json() == {"approved": 2}


def test_email_test_endpoint(client, db, monkeypatch):
    monkeypatch.setattr(email_sender, "test_smtp", lambda: (True, None))
    monkeypatch.setattr(inbox, "test_imap", lambda: (False, "IMAP is off"))
    body = client.post("/connections/email/test").json()
    assert body["ok"] is False and body["reading_replies"]["error"] == "IMAP is off"
    assert client.get("/connections").json()["email"]["state"] == "error"

    monkeypatch.setattr(inbox, "test_imap", lambda: (True, None))
    assert client.post("/connections/email/test").json()["ok"] is True
    assert client.get("/connections").json()["email"]["state"] == "ok"
