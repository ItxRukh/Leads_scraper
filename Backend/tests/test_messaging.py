"""Phone numbers, message writing, drafts, approval, release timing and sending."""
import json
import random
from datetime import datetime, timedelta

import pytest

import email_sender
import message_service as ms
import message_writer as mw
import queue_service as q
import scheduling as sch
from config import settings
from database import EmailLog, Lead, LeadTier, OutreachMessage, PipelineStage, WhatsAppLog
from phones import find_lead_by_phone, phone_key, whatsapp_number

MON_10AM_NY = datetime(2026, 10, 12, 14, 0)


@pytest.fixture()
def db(seeded_db):
    return seeded_db


@pytest.fixture(autouse=True)
def fake_ai(monkeypatch):
    """The AI returns a fixed email (JSON) or WhatsApp text, depending on what was asked."""
    calls = []

    def fake(system_prompt, user_prompt, max_tokens=1000, temperature=0.3):
        calls.append(user_prompt)
        if "Return ONLY JSON" in system_prompt:
            return json.dumps({"subject": "Quick question", "body": "Hi there, a short email."})
        return "Hi, a short WhatsApp message."
    monkeypatch.setattr(mw, "_call_groq", fake)
    return calls


@pytest.fixture(autouse=True)
def email_account(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_ADDRESS", "sales@example.com")
    monkeypatch.setattr(settings, "EMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setattr(settings, "EMAIL_FROM_NAME", "Sam Seller")


def queue_lead(db, **kw):
    base = dict(name="Lead", email="a@b.com", phone="+13055550100", city="Miami FL",
                tier=LeadTier.HOT, final_score=90)
    base.update(kw)
    lead = Lead(**base)
    db.add(lead)
    db.commit()
    entry = q.enqueue_if_eligible(db, lead)
    assert entry is not None
    return lead, entry


def set_mode(**modes):
    s = sch.load_settings()
    for channel, mode in modes.items():
        getattr(s, channel).mode = mode
    sch.save_settings(s)


# ─── Phone numbers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,country,expected", [
    ("+1 (305) 555-0100", None, "13055550100"),
    ("(305) 555-0100", None, "13055550100"),
    ("305-555-0100", "United States", "13055550100"),
    ("1-305-555-0100", None, "13055550100"),
    ("0092 300 1234567", None, "923001234567"),
    ("+92 300 1234567", None, "923001234567"),
    ("0300 1234567", None, None),               # national format: country unknown
    ("0300 1234567", "Pakistan", None),         # leading 0 is a trunk prefix; do not guess
    ("12345", None, None),
    ("", None, None),
    (None, None, None),
])
def test_whatsapp_number(raw, country, expected):
    assert whatsapp_number(raw, country) == expected


def test_find_lead_by_phone_ignores_formatting(db):
    lead = Lead(name="X", phone="(305) 555-0100")
    db.add(lead)
    db.add(Lead(name="Y", phone="(305) 555-0199"))
    db.commit()
    assert find_lead_by_phone(db, "+1 305 555 0100").id == lead.id
    assert find_lead_by_phone(db, "13055550100").id == lead.id
    assert find_lead_by_phone(db, "13055559999") is None
    assert find_lead_by_phone(db, "123") is None
    assert phone_key("+1 (305) 555-0100") == "3055550100"


# ─── Writing ──────────────────────────────────────────────────────────────────

def _steps_and_config(db, lead):
    from profile_schema import ProfileConfig
    from scoring_engine import resolve_profile
    steps = sch.sequence_for(db, lead)
    return steps, ProfileConfig.model_validate(resolve_profile(db, lead).config)


def test_step_intent(db):
    lead, _ = queue_lead(db)
    steps, _ = _steps_and_config(db, lead)
    by = {s.key: s for s in steps}
    assert mw.step_intent(steps, by["email_1"]) == "first_contact"
    assert mw.step_intent(steps, by["email_2"]) == "follow_up"
    assert mw.step_intent(steps, by["email_4"]) == "closing"
    assert mw.step_intent(steps, by["whatsapp_1"]) == "first_contact"


def test_email_draft_gets_signature_and_opt_out_line(db):
    lead, _ = queue_lead(db)
    steps, cfg = _steps_and_config(db, lead)
    draft = mw.write_message(lead, steps[0], steps, cfg, [])
    assert draft.subject == "Quick question"
    assert draft.body.startswith("Hi there, a short email.")
    assert "Sam Seller" in draft.body
    assert draft.body.rstrip().endswith("reply STOP and I won't contact you again.")


def test_whatsapp_opt_out_only_on_first_contact(db):
    from profile_schema import SequenceStep
    lead, _ = queue_lead(db)
    steps, cfg = _steps_and_config(db, lead)
    second = SequenceStep(key="whatsapp_2", label="Nudge", channel="whatsapp", delay_days=2, min_tier="warm")
    all_steps = steps + [second]

    first = mw.write_message(lead, next(s for s in steps if s.key == "whatsapp_1"), all_steps, cfg, [])
    assert first.subject is None and first.body.endswith("Reply STOP to opt out.")

    later = mw.write_message(lead, second, all_steps, cfg, [first.body])
    assert later.subject is None and "STOP" not in later.body


def test_prompt_mentions_earlier_messages_and_what_we_noticed(db, fake_ai):
    lead, _ = queue_lead(db, score_breakdown={"rules": [
        {"label": "Has no online booking", "status": "matched", "points": 20},
        {"label": "Not matched thing", "status": "not_matched", "points": 10}]})
    steps, cfg = _steps_and_config(db, lead)
    mw.write_message(lead, steps[1], steps, cfg, ["Our first email text"])
    prompt = fake_ai[-1]
    assert "Has no online booking" in prompt and "Not matched thing" not in prompt
    assert "Our first email text" in prompt


def test_writer_returns_none_when_the_ai_fails(db, monkeypatch):
    lead, _ = queue_lead(db)
    steps, cfg = _steps_and_config(db, lead)
    monkeypatch.setattr(mw, "_call_groq", lambda **kw: None)
    assert mw.write_message(lead, steps[0], steps, cfg, []) is None
    monkeypatch.setattr(mw, "_call_groq", lambda **kw: "not json at all")
    assert mw.write_message(lead, steps[0], steps, cfg, []) is None
    monkeypatch.setattr(mw, "_call_groq", lambda **kw: json.dumps({"subject": "", "body": "x"}))
    assert mw.write_message(lead, steps[0], steps, cfg, []) is None


# ─── Drafts and approval ──────────────────────────────────────────────────────

def test_manual_channels_get_no_drafts(db, clock):
    set_mode(email="manual", whatsapp="manual")
    queue_lead(db)
    assert ms.ensure_drafts(db) == 0


def test_assisted_creates_a_draft_once(db, clock):
    set_mode(email="assisted")
    lead, _ = queue_lead(db)
    assert ms.ensure_drafts(db) == 1
    assert ms.ensure_drafts(db) == 0                              # not written twice
    msg = db.query(OutreachMessage).one()
    assert (msg.channel, msg.step_key, msg.status) == ("email", "email_1", "draft")
    assert msg.release_at is None and msg.lead_id == lead.id


def test_auto_approves_and_plans_a_release(db, clock):
    set_mode(email="auto")
    queue_lead(db)
    ms.ensure_drafts(db)
    msg = db.query(OutreachMessage).one()
    assert msg.status == "approved" and msg.mode == "auto"
    assert msg.release_at == MON_10AM_NY


def test_drafts_respect_the_daily_limit(db, clock):
    s = sch.load_settings()
    s.email.mode, s.email.daily_cap = "assisted", 2
    sch.save_settings(s)
    for i in range(4):
        queue_lead(db, name=f"L{i}", email=f"l{i}@b.com", final_score=90 - i)
    assert ms.ensure_drafts(db) == 2


def test_manual_approval_spaces_release_times(db, clock):
    s = sch.load_settings()
    s.email.mode, s.email.min_gap_seconds, s.email.max_gap_seconds = "assisted", 60, 120
    sch.save_settings(s)
    for i in range(3):
        queue_lead(db, name=f"L{i}", email=f"l{i}@b.com", final_score=90 - i)
    ms.ensure_drafts(db)

    assert ms.approve_all(db, rng=random.Random(5)) == 3
    times = [m.release_at for m in db.query(OutreachMessage).order_by(OutreachMessage.id)]
    assert times[0] == MON_10AM_NY
    for a, b in zip(times, times[1:]):
        assert 60 <= (b - a).total_seconds() <= 120


def test_edit_approve_discard_and_skip(db, clock):
    lead, entry = queue_lead(db)
    ms.ensure_drafts(db)
    msg = db.query(OutreachMessage).one()

    with pytest.raises(ValueError):
        ms.update_draft(db, msg.id, "Subject", "   ")
    with pytest.raises(ValueError):
        ms.update_draft(db, msg.id, "", "Body")
    ms.update_draft(db, msg.id, "New subject", "New body")
    assert (msg.subject, msg.body) == ("New subject", "New body")

    ms.discard(db, msg.id)
    assert msg.status == "discarded"
    assert ms.ensure_drafts(db) == 1                              # step still due: a fresh draft is written
    fresh = db.query(OutreachMessage).filter_by(status="draft").one()

    ms.skip(db, fresh.id)
    assert fresh.status == "discarded" and entry.next_step_key == "email_2"
    with pytest.raises(ValueError):
        ms.approve(db, fresh.id)
    with pytest.raises(LookupError):
        ms.approve(db, 9999)


def test_follow_up_email_is_threaded_under_the_first(db, clock):
    lead, entry = queue_lead(db)
    ms.ensure_drafts(db)
    first = db.query(OutreachMessage).one()
    ms.approve(db, first.id)
    ms.mark_sent(db, first, "<first@example.com>")                # email_1 done

    clock(MON_10AM_NY + timedelta(days=3))
    assert ms.ensure_drafts(db) == 1
    second = db.query(OutreachMessage).filter_by(status="draft").one()
    assert second.step_key == "email_2" and second.subject == "Re: Quick question"


def test_mark_sent_records_logs_and_plans_the_next_step(db, clock):
    lead, entry = queue_lead(db)
    ms.ensure_drafts(db)
    msg = db.query(OutreachMessage).one()
    ms.approve(db, msg.id)
    ms.mark_sent(db, msg, "<id@x>")

    assert msg.status == "sent" and msg.external_id == "<id@x>"
    assert entry.next_step_key == "email_2"
    assert db.query(EmailLog).filter_by(lead_id=lead.id).count() == 1
    assert lead.pipeline_stage == PipelineStage.EMAIL_SENT


# ─── Release rules ────────────────────────────────────────────────────────────

def approved_email(db, clock_now=None, **lead_kw):
    set_mode(email="auto")
    lead, entry = queue_lead(db, **lead_kw)
    ms.ensure_drafts(db)
    return lead, entry, db.query(OutreachMessage).filter_by(lead_id=lead.id).one()


def test_ready_only_when_released_and_inside_working_hours(db, clock):
    lead, _, msg = approved_email(db)
    msg.release_at = MON_10AM_NY + timedelta(minutes=10)
    db.commit()
    assert ms.ready_messages(db, "email") == []                    # not released yet
    clock(MON_10AM_NY + timedelta(minutes=10))
    assert [m.id for m in ms.ready_messages(db, "email")] == [msg.id]
    clock(datetime(2026, 10, 12, 22, 0))                           # 18:00 in Miami: closed
    assert ms.ready_messages(db, "email") == []
    assert msg.release_at == datetime(2026, 10, 13, 13, 0)         # moved to tomorrow 09:00


def test_stale_or_moved_on_messages_are_discarded_not_sent(db, clock):
    lead, entry, msg = approved_email(db)
    entry.next_step_key = "email_2"                                # the step moved on
    db.commit()
    assert ms.ready_messages(db, "email") == []
    assert msg.status == "discarded"


def test_daily_cap_blocks_release(db, clock):
    s = sch.load_settings()
    s.email.mode, s.email.daily_cap = "auto", 1
    sch.save_settings(s)
    a, _ = queue_lead(db, name="A", email="a@b.com")
    ms.ensure_drafts(db)
    sch.complete_step(db, a.id)                                    # one email already done today
    b, _ = queue_lead(db, name="B", email="b@b.com")
    ms.ensure_drafts(db)
    assert ms.ready_messages(db, "email") == []


def test_stale_claims_return_to_approved(db, clock):
    lead, _, msg = approved_email(db)
    ms.claim(db, msg)
    clock(MON_10AM_NY + timedelta(minutes=11))
    assert ms.release_stale_claims(db) == 1
    assert msg.status == "approved"


# ─── Sending email ────────────────────────────────────────────────────────────

def test_send_due_emails_sends_and_advances(db, clock, monkeypatch):
    sent = []
    monkeypatch.setattr(email_sender, "send_email",
                        lambda to, subject, body, reply_to=None, tracking_token=None: sent.append((to, subject, reply_to)) or "<m1@x>")
    lead, entry, msg = approved_email(db)

    assert ms.send_due_emails(db) == {"sent": 1, "failed": 0}
    assert sent == [("a@b.com", "Quick question", None)]
    assert msg.status == "sent" and msg.external_id == "<m1@x>"
    assert entry.next_step_key == "email_2"


def test_failed_sends_retry_then_give_up(db, clock, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(email_sender, "send_email", boom)
    lead, entry, msg = approved_email(db)

    assert ms.send_due_emails(db) == {"sent": 0, "failed": 1}
    assert msg.status == "approved" and msg.attempts == 1 and msg.release_at == MON_10AM_NY + timedelta(minutes=5)

    clock(MON_10AM_NY + timedelta(minutes=5))
    ms.send_due_emails(db)
    clock(MON_10AM_NY + timedelta(minutes=20))
    ms.send_due_emails(db)
    assert msg.status == "failed" and msg.attempts == 3 and entry.next_step_key == "email_1"


def test_nothing_is_sent_without_an_email_account(db, clock, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_APP_PASSWORD", "")
    approved_email(db)
    assert ms.send_due_emails(db) == {"sent": 0, "failed": 0}


# ─── The email itself ─────────────────────────────────────────────────────────

def test_built_email_headers_and_threading():
    msg = email_sender.build_message("lead@b.com", "Hello", "Body text", "<root@x>")
    assert msg["To"] == "lead@b.com" and "Sam Seller" in msg["From"]
    assert msg["List-Unsubscribe"] == "<mailto:sales@example.com?subject=STOP>"
    assert msg["In-Reply-To"] == msg["References"] == "<root@x>"
    assert msg["Message-ID"].endswith("@example.com>")
    assert msg.get_content().strip() == "Body text"


def test_smtp_test_reports_a_missing_password(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_APP_PASSWORD", "")
    ok, error = email_sender.test_smtp()
    assert not ok and "EMAIL_APP_PASSWORD" in error
