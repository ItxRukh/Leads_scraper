"""Email open tracking: the pixel in the email, and recording an open."""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import connections
import email_sender
import message_service as ms
import queue_service as q
import tracking_api
from config import settings
from database import EmailLog, Lead, LeadTier, OutreachMessage, PipelineStage

NOW = datetime(2026, 10, 12, 14, 0)


@pytest.fixture()
def db(seeded_db):
    return seeded_db


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_ADDRESS", "sales@example.com")
    monkeypatch.setattr(settings, "EMAIL_APP_PASSWORD", "app-password")


def tracking(monkeypatch, enabled=True, url="https://track.example.com"):
    monkeypatch.setattr(settings, "EMAIL_OPEN_TRACKING", enabled)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", url)


@pytest.mark.parametrize("url,public", [
    ("https://track.example.com", True), ("https://abc123.ngrok-free.app/", True),
    ("http://localhost:8000", False), ("http://127.0.0.1:8000", False),
    ("http://192.168.1.5:8000", False), ("http://10.0.0.2", False), ("", False),
])
def test_which_addresses_can_be_reached_from_outside(url, public):
    assert email_sender.is_public_address(url) is public


def test_pixel_is_added_only_when_tracking_is_ready(monkeypatch):
    tracking(monkeypatch)
    msg = email_sender.build_message("a@b.com", "Hi", "Line one\n\nLine <two>", None, "tok123")
    html_part = msg.get_body(preferencelist=("html",)).get_content()
    assert 'src="https://track.example.com/track/open/tok123.gif"' in html_part
    assert "Line &lt;two&gt;" in html_part                        # the text is escaped in the HTML copy
    assert msg.get_body(preferencelist=("plain",)).get_content().startswith("Line one")

    for off in (dict(enabled=False), dict(url="http://localhost:8000")):
        tracking(monkeypatch, **off)
        plain = email_sender.build_message("a@b.com", "Hi", "Body", None, "tok123")
        assert not plain.is_multipart()

    tracking(monkeypatch)
    assert not email_sender.build_message("a@b.com", "Hi", "Body").is_multipart()   # no token, no pixel


def sent_email(db, **kw):
    lead = Lead(name="L", email="a@b.com", tier=LeadTier.HOT, final_score=90, city="Miami FL",
                pipeline_stage=PipelineStage.EMAIL_SENT)
    db.add(lead)
    db.commit()
    db.add(EmailLog(lead_id=lead.id, subject="Hi", sent_at=NOW))
    msg = OutreachMessage(lead_id=lead.id, step_key="email_1", channel="email", body="x", status="sent",
                          sent_at=NOW, tracking_token="tok1", **kw)
    db.add(msg)
    db.commit()
    return lead, msg


def test_an_open_is_recorded(db):
    lead, msg = sent_email(db)
    assert tracking_api.record_open(db, "tok1", NOW + timedelta(minutes=5)) is True
    assert msg.open_count == 1 and msg.opened_at == NOW + timedelta(minutes=5)
    assert db.query(EmailLog).one().email_opened is True
    assert lead.pipeline_stage == PipelineStage.EMAIL_OPENED

    tracking_api.record_open(db, "tok1", NOW + timedelta(hours=1))
    assert msg.open_count == 2 and msg.opened_at == NOW + timedelta(minutes=5)     # first open kept


def test_loads_right_after_sending_and_unknown_tokens_are_ignored(db):
    lead, msg = sent_email(db)
    assert tracking_api.record_open(db, "tok1", NOW + timedelta(seconds=5)) is False     # a scanner
    assert tracking_api.record_open(db, "nope", NOW + timedelta(minutes=5)) is False
    assert msg.open_count == 0 and lead.pipeline_stage == PipelineStage.EMAIL_SENT


def test_an_open_does_not_move_a_lead_that_already_replied(db):
    lead, _ = sent_email(db)
    lead.pipeline_stage = PipelineStage.REPLIED
    db.commit()
    tracking_api.record_open(db, "tok1", NOW + timedelta(minutes=5))
    assert lead.pipeline_stage == PipelineStage.REPLIED


def test_the_pixel_route_always_answers_with_an_image(db):
    from database import get_db
    app = FastAPI()
    app.include_router(tracking_api.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)

    lead, msg = sent_email(db)
    msg.sent_at = datetime.utcnow() - timedelta(minutes=5)
    db.commit()
    for token in ("tok1", "unknown"):
        r = client.get(f"/track/open/{token}.gif")
        assert r.status_code == 200 and r.headers["content-type"] == "image/gif"
        assert r.content.startswith(b"GIF89a") and "no-store" in r.headers["cache-control"]
    assert msg.open_count == 1


def test_every_email_draft_gets_a_token_and_it_is_sent(db, clock, monkeypatch):
    import json
    import message_writer as mw
    monkeypatch.setattr(mw, "_call_groq", lambda **k: json.dumps({"subject": "S", "body": "B"}))
    import scheduling as sch
    rules = sch.load_settings()
    rules.email.mode = "auto"
    sch.save_settings(rules)
    lead = Lead(name="L", email="a@b.com", phone="+13055550100", city="Miami FL", tier=LeadTier.HOT, final_score=90)
    db.add(lead)
    db.commit()
    q.enqueue_if_eligible(db, lead)
    ms.ensure_drafts(db)
    msg = db.query(OutreachMessage).one()
    assert msg.tracking_token and len(msg.tracking_token) >= 16

    seen = {}
    monkeypatch.setattr(email_sender, "send_email",
                        lambda to, subject, body, reply_to=None, tracking_token=None: seen.update(token=tracking_token) or "<id>")
    ms.send_due_emails(db)
    assert seen["token"] == msg.tracking_token


def test_connection_summary_reports_tracking(db, monkeypatch):
    tracking(monkeypatch, enabled=True, url="http://localhost:8000")
    info = connections.email_summary(db)["open_tracking"]
    assert info == {"enabled": True, "ready": False, "opened": 0}
    tracking(monkeypatch)
    sent_email(db, open_count=2)
    assert connections.email_summary(db)["open_tracking"] == {"enabled": True, "ready": True, "opened": 1}
