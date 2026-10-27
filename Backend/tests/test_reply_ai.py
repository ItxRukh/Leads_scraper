"""Understanding replies: labels, answer drafts, "not now" leads, and sending a reply."""
import json
import random
from datetime import datetime, timedelta

import pytest

import email_sender
import message_service as ms
import message_writer as mw
import queue_service as q
import reply_ai
import replies
import scheduling as sch
from config import settings
from database import (
    DNCList, EmailLog, InboundMessage, Lead, LeadTier, OutreachMessage, OutreachStepLog, PipelineStage,
)

MON_10AM_NY = datetime(2026, 10, 12, 14, 0)


@pytest.fixture()
def db(seeded_db):
    return seeded_db


@pytest.fixture(autouse=True)
def account(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_ADDRESS", "sales@example.com")
    monkeypatch.setattr(settings, "EMAIL_APP_PASSWORD", "app-password")


@pytest.fixture()
def ai(monkeypatch):
    """The label the fake AI gives, and the prompts it was asked."""
    state = {"label": "interested", "summary": "Wants to know more", "prompts": []}

    def classify(system_prompt, user_prompt, max_tokens=1000, temperature=0.3):
        if state["label"] is None:
            return None
        return json.dumps({"label": state["label"], "summary": state["summary"]})

    def write(system_prompt, user_prompt, max_tokens=1000, temperature=0.3):
        state["prompts"].append((system_prompt, user_prompt))
        if "Return ONLY JSON" in system_prompt:
            return json.dumps({"subject": "S", "body": "Thanks for getting back to me."})
        return "Thanks, happy to help."

    monkeypatch.setattr(reply_ai, "_call_groq", classify)
    monkeypatch.setattr(mw, "_call_groq", write)
    return state


def set_rules(**changes):
    s = sch.load_settings()
    for key, value in changes.items():
        if isinstance(value, dict):
            for k, v in value.items():
                setattr(getattr(s, key), k, v)
        else:
            setattr(s, key, value)
    sch.save_settings(s)


def queue_lead(db, **kw):
    base = dict(name="Lead", email="owner@lead.com", phone="+13055550100", city="Miami FL",
                tier=LeadTier.HOT, final_score=90)
    base.update(kw)
    lead = Lead(**base)
    db.add(lead)
    db.commit()
    return lead, q.enqueue_if_eligible(db, lead)


def reply(db, lead, text="Sounds interesting, tell me more", channel="email", key="<r1@lead.com>", at=MON_10AM_NY):
    assert replies.record_reply(db, lead, channel, key, lead.email or "x", text, at)
    return db.query(InboundMessage).filter_by(message_key=key).one()


# ─── Labelling ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ('{"label": "interested", "summary": "Wants a call"}', ("interested", "Wants a call")),
    ('```json\n{"label": "Not_Now", "summary": "Busy"}\n```', ("not_now", "Busy")),
    ('{"label": "maybe", "summary": "x"}', None),
    ("no idea", None),
    (None, None),
])
def test_classify_reply_reads_the_ai_answer(db, monkeypatch, raw, expected):
    lead, _ = queue_lead(db)
    monkeypatch.setattr(reply_ai, "_call_groq", lambda **kw: raw)
    assert reply_ai.classify_reply(lead, "hello") == expected


def test_replies_are_labelled_and_unreadable_ones_retried(db, clock, ai):
    set_rules(email={"mode": "manual"})
    lead, _ = queue_lead(db)
    inbound = reply(db, lead)

    ai["label"] = None
    assert reply_ai.process_new_replies(db) == 0 and inbound.label is None
    ai["label"] = "other"
    assert reply_ai.process_new_replies(db) == 1
    assert (inbound.label, inbound.summary) == ("other", "Wants to know more")
    assert reply_ai.process_new_replies(db) == 0                  # labelled once


# ─── What each label does ─────────────────────────────────────────────────────

def test_interested_gets_a_draft_that_goes_through_approval(db, clock, ai):
    set_rules(email={"mode": "assisted"})
    lead, _ = queue_lead(db)
    inbound = reply(db, lead)
    reply_ai.process_new_replies(db)

    draft = db.query(OutreachMessage).filter_by(kind="reply").one()
    assert (draft.status, draft.channel, draft.in_reply_to_id) == ("draft", "email", inbound.id)
    assert draft.subject == "Re: your message"                    # nothing was sent before
    assert draft.body.startswith("Thanks for getting back to me.")
    assert "STOP" not in draft.body                               # a reply carries no opt-out line

    reply_ai.process_new_replies(db)
    assert db.query(OutreachMessage).filter_by(kind="reply").count() == 1


def test_the_answer_uses_the_profiles_booking_link_and_faq(db, clock, ai):
    from profile_service import get_profile
    set_rules(email={"mode": "assisted"})
    profile = get_profile(db, 1)
    config = dict(profile.config)
    config["offer"] = {**config["offer"], "booking_link": "https://cal.example.com/demo", "faq": "Q: Price? A: From $99."}
    profile.config = config
    db.commit()

    lead, _ = queue_lead(db)
    reply(db, lead)
    reply_ai.process_new_replies(db)

    system, user = ai["prompts"][-1]
    assert "Q: Price? A: From $99." in system
    assert "https://cal.example.com/demo" in user
    assert "never invent facts" in system


def test_the_subject_follows_the_first_email(db, clock, ai):
    set_rules(email={"mode": "assisted"})
    lead, _ = queue_lead(db)
    db.add(OutreachMessage(lead_id=lead.id, step_key="email_1", channel="email", subject="Quick question",
                           body="x", status="sent", sent_at=MON_10AM_NY, external_id="<ours@x>"))
    db.commit()
    reply(db, lead)
    reply_ai.process_new_replies(db)
    assert db.query(OutreachMessage).filter_by(kind="reply").one().subject == "Re: Quick question"


def test_manual_channels_get_a_label_but_no_draft(db, clock, ai):
    set_rules(email={"mode": "manual"})
    lead, _ = queue_lead(db)
    inbound = reply(db, lead)
    reply_ai.process_new_replies(db)
    assert inbound.label == "interested"
    assert db.query(OutreachMessage).filter_by(kind="reply").count() == 0


def test_auto_replies_need_both_switches(db, clock, ai):
    set_rules(email={"mode": "auto"}, auto_reply=False)
    lead, _ = queue_lead(db, name="A", email="a@lead.com")
    reply(db, lead, key="<a>")
    reply_ai.process_new_replies(db)
    assert db.query(OutreachMessage).filter_by(kind="reply").one().status == "draft"

    set_rules(auto_reply=True)
    other, _ = queue_lead(db, name="B", email="b@lead.com")
    reply(db, other, key="<b>")
    reply_ai.process_new_replies(db)
    drafts = {m.lead_id: m for m in db.query(OutreachMessage).filter_by(kind="reply")}
    assert drafts[other.id].status == "approved" and drafts[other.id].mode == "auto"


def test_a_refusal_the_keywords_missed_goes_to_dnc(db, clock, ai):
    set_rules(email={"mode": "assisted"})
    lead, _ = queue_lead(db)
    reply(db, lead, text="We already have someone for that, please don't write again")
    assert lead.do_not_contact is False                           # no keyword matched
    ai["label"] = "stop"
    reply_ai.process_new_replies(db)
    assert lead.do_not_contact is True and db.query(DNCList).count() == 1
    assert db.query(OutreachMessage).filter_by(kind="reply").count() == 0


def test_not_now_sets_a_revisit_date_and_drafts_an_acknowledgement(db, clock, ai):
    set_rules(email={"mode": "assisted"}, revisit_days=30)
    lead, entry = queue_lead(db)
    ai["label"] = "not_now"
    reply(db, lead, text="Busy this quarter, maybe later")
    reply_ai.process_new_replies(db)
    assert entry.revisit_at == MON_10AM_NY + timedelta(days=30)
    assert db.query(OutreachMessage).filter_by(kind="reply").count() == 1


def test_old_replies_are_no_longer_answered(db, clock, ai):
    set_rules(email={"mode": "assisted"})
    lead, _ = queue_lead(db)
    reply(db, lead, at=MON_10AM_NY - timedelta(days=5))
    reply_ai.process_new_replies(db)
    assert db.query(OutreachMessage).filter_by(kind="reply").count() == 0


# ─── Coming back to a "not now" lead ──────────────────────────────────────────

def test_a_not_now_lead_is_contacted_again_after_its_revisit_date(db, clock, ai):
    set_rules(email={"mode": "manual"}, revisit_days=30)
    lead, entry = queue_lead(db)
    sch.complete_step(db, lead.id)                                # email_1 done
    ai["label"] = "not_now"
    reply(db, lead, text="Maybe later")
    reply_ai.process_new_replies(db)
    sch.refresh_stops(db)
    assert entry.sequence_status == sch.STOPPED and lead.pipeline_stage == PipelineStage.REPLIED

    assert sch.reopen_snoozed(db, MON_10AM_NY + timedelta(days=29)) == 0
    clock(MON_10AM_NY + timedelta(days=30))
    assert sch.reopen_snoozed(db) == 1

    assert entry.sequence_status == sch.ACTIVE and entry.next_step_key == "email_1"
    assert entry.revisit_at is None and lead.pipeline_stage != PipelineStage.REPLIED
    sch.refresh_stops(db)
    assert entry.sequence_status == sch.ACTIVE                    # the old reply no longer stops it

    # a new reply does
    replies.record_reply(db, lead, "email", "<r2>", "owner@lead.com", "Now is a good time", MON_10AM_NY + timedelta(days=31))
    sch.refresh_stops(db)
    assert entry.sequence_status == sch.STOPPED


def test_a_revisit_does_not_reopen_a_lead_that_opted_out(db, clock):
    lead, entry = queue_lead(db)
    entry.revisit_at = MON_10AM_NY - timedelta(days=1)
    lead.do_not_contact = True
    db.commit()
    assert sch.reopen_snoozed(db) == 0 and entry.revisit_at is None


def test_the_re_engagement_message_does_not_start_from_scratch(db, ai):
    lead, _ = queue_lead(db)
    from profile_schema import ProfileConfig
    from scoring_engine import resolve_profile
    steps = sch.sequence_for(db, lead)
    config = ProfileConfig.model_validate(resolve_profile(db, lead).config)
    mw.write_message(lead, steps[0], steps, config, ["Our first email from last quarter"])
    assert "not the right time" in ai["prompts"][-1][1]


# ─── Sending a reply ──────────────────────────────────────────────────────────

def test_an_email_reply_is_threaded_under_the_leads_message_and_does_not_advance_the_sequence(db, clock, ai, monkeypatch):
    set_rules(email={"mode": "assisted"})
    lead, entry = queue_lead(db)
    inbound = reply(db, lead, key="<theirs@lead.com>")
    reply_ai.process_new_replies(db)
    draft = db.query(OutreachMessage).filter_by(kind="reply").one()
    ms.approve(db, draft.id)

    sent = {}
    monkeypatch.setattr(email_sender, "send_email",
                        lambda to, subject, body, reply_to=None, tracking_token=None: sent.update(to=to, reply_to=reply_to) or "<ours@x>")
    step_before = entry.step_index
    assert ms.send_due_emails(db) == {"sent": 1, "failed": 0}

    assert sent == {"to": "owner@lead.com", "reply_to": "<theirs@lead.com>"}
    assert draft.status == "sent" and entry.step_index == step_before
    assert db.query(OutreachStepLog).filter_by(status="done").count() == 0
    assert db.query(EmailLog).filter(EmailLog.sent_at.isnot(None)).count() >= 1


def test_a_reply_is_not_sent_to_a_lead_who_opted_out_meanwhile(db, clock, ai):
    set_rules(email={"mode": "auto"}, auto_reply=True)
    lead, _ = queue_lead(db)
    reply(db, lead)
    reply_ai.process_new_replies(db)
    draft = db.query(OutreachMessage).filter_by(kind="reply").one()
    lead.do_not_contact = True
    db.commit()
    assert ms.ready_messages(db, "email") == []
    assert draft.status == "discarded"


def test_skipping_a_reply_only_drops_it(db, clock, ai):
    set_rules(email={"mode": "assisted"})
    lead, entry = queue_lead(db)
    reply(db, lead)
    reply_ai.process_new_replies(db)
    draft = db.query(OutreachMessage).filter_by(kind="reply").one()

    item = ms.list_messages(db)[0]
    assert (item["kind"], item["label"], item["their_message"]) == ("reply", "interested", "Sounds interesting, tell me more")
    ms.skip(db, draft.id)
    assert draft.status == "discarded"
    assert db.query(OutreachStepLog).count() == 0                 # no step was skipped
