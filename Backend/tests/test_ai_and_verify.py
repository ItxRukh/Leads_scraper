import json
from datetime import datetime

import pytest

import profile_service as svc
import queue_service as q
from config import settings
from database import Lead, LeadTier
from profile_schema import ProfileIn
from scripts import auditor
from scripts.email_verify import rescore_after_verification
from utils import groq_helper


@pytest.fixture()
def db(seeded_db):
    return seeded_db


# ─── answer_questions ─────────────────────────────────────────────────────────

TEXT = "We are a family run heating and cooling company serving Austin for 20 years. " * 3


def test_answers_are_parsed_from_json(monkeypatch):
    monkeypatch.setattr(groq_helper, "_call_groq",
                        lambda **kw: '```json\n{"chatbot": true, "booking": false}\n```')
    out = groq_helper.answer_questions("Cool Air", TEXT, {"chatbot": "Has a chatbot?", "booking": "Online booking?"})
    assert out == {"chatbot": True, "booking": False}


def test_unreadable_or_missing_answers_stay_unknown(monkeypatch):
    qs = {"a": "Q a?", "b": "Q b?"}
    monkeypatch.setattr(groq_helper, "_call_groq", lambda **kw: "sorry, I cannot")
    assert groq_helper.answer_questions("X", TEXT, qs) == {"a": None, "b": None}

    monkeypatch.setattr(groq_helper, "_call_groq", lambda **kw: json.dumps({"a": True, "b": "yes"}))
    assert groq_helper.answer_questions("X", TEXT, qs) == {"a": True, "b": None}

    monkeypatch.setattr(groq_helper, "_call_groq", lambda **kw: None)
    assert groq_helper.answer_questions("X", TEXT, qs) == {"a": None, "b": None}


def test_no_text_means_no_call(monkeypatch):
    def boom(**kw):
        raise AssertionError("the AI must not be called without website text")
    monkeypatch.setattr(groq_helper, "_call_groq", boom)
    assert groq_helper.answer_questions("X", "short", {"a": "Q?"}) == {"a": None}


# ─── AI rules inside an audit ─────────────────────────────────────────────────

@pytest.fixture()
def ai_profile(db):
    return svc.create_profile(db, ProfileIn(name="Chatbot check", config={
        "scoring": {"hot_threshold": 30, "warm_threshold": 10, "rules": [
            {"key": "no_chatbot", "label": "Site has no chatbot", "kind": "ai", "points": 40,
             "requires": ["website"], "params": {"question": "Does the site have NO chatbot?"}},
            {"key": "reviews", "label": "50+ reviews", "kind": "field", "points": 10,
             "params": {"field": "review_count", "op": ">=", "value": 50}},
        ]}}))


def new_lead(db, profile, **kw):
    base = dict(name="Cool Air", email="a@b.com", website="https://b.com", website_crawled=True,
                crawled_text=TEXT, review_count=60, rating=4.5, profile_id=profile.id)
    base.update(kw)
    lead = Lead(**base)
    db.add(lead)
    db.commit()
    return lead


def test_audit_asks_the_profiles_questions_and_scores_the_answer(db, ai_profile, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    asked = {}

    def fake(name, text, questions):
        asked.update(questions)
        return {k: True for k in questions}
    monkeypatch.setattr(auditor, "answer_questions", fake)
    monkeypatch.setattr(auditor, "enhance_with_claude",
                        lambda lead: pytest.fail("dropship analysis must not run for this profile"))

    lead = new_lead(db, ai_profile)
    auditor.audit_lead(lead, db, use_claude=True)

    assert asked == {"no_chatbot": "Does the site have NO chatbot?"}
    assert lead.signals["no_chatbot"] is True
    assert lead.final_score == 50 and lead.tier == LeadTier.HOT
    assert lead.score_incomplete is False


def test_unanswered_question_leaves_the_score_incomplete(db, ai_profile, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(auditor, "answer_questions", lambda n, t, qs: {k: None for k in qs})
    lead = new_lead(db, ai_profile)
    auditor.audit_lead(lead, db, use_claude=True)
    assert lead.score_incomplete is True
    assert lead.final_score == 10


def test_answered_questions_are_not_asked_again(db, ai_profile, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    monkeypatch.setattr(auditor, "answer_questions",
                        lambda *a: pytest.fail("already answered, must not ask again"))
    lead = new_lead(db, ai_profile, signals={"no_chatbot": False})
    auditor.audit_lead(lead, db, use_claude=True)
    assert lead.signals["no_chatbot"] is False


def test_dropship_analysis_still_runs_for_the_default_profile(db, monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "test-key")
    called = []
    monkeypatch.setattr(auditor, "enhance_with_claude", lambda lead: called.append(lead.id) or {})
    default = svc.resolve_profile_by_id(db, None)
    lead = new_lead(db, default)
    auditor.audit_lead(lead, db, use_claude=True)
    assert called == [lead.id]


# ─── Email verification re-scores through the profile ─────────────────────────

def test_verifying_an_email_rescores_and_queues(db):
    default = svc.resolve_profile_by_id(db, None)
    lead = new_lead(db, default, rating=4.5, review_count=50)   # 10+10+10 = 30: Cold
    auditor.audit_lead(lead, db, use_claude=False)
    assert lead.final_score == 30 and lead.tier == LeadTier.COLD
    assert q.list_queue(db)["total"] == 0

    lead.email_verified = True
    rescore_after_verification(db, lead)

    assert lead.final_score == 40 and lead.tier == LeadTier.WARM   # +10 verified bonus
    assert q.list_queue(db)["total"] == 1


def test_verifying_a_never_audited_lead_only_sets_the_flag(db):
    default = svc.resolve_profile_by_id(db, None)
    lead = new_lead(db, default)
    assert lead.audited_at is None
    lead.email_verified = True
    rescore_after_verification(db, lead)
    assert lead.audited_at is None and lead.final_score in (0, None)


def test_profile_thresholds_apply_after_verification(db):
    p = svc.create_profile(db, ProfileIn(name="Strict", config={"scoring": {
        "hot_threshold": 90, "warm_threshold": 80, "rules": [
            {"key": "verified", "label": "Verified", "kind": "field", "points": 10, "group": "bonus",
             "params": {"field": "email_verified", "op": "==", "value": True}}]}}))
    lead = new_lead(db, p)
    lead.audited_at = datetime.utcnow()
    lead.email_verified = True
    rescore_after_verification(db, lead)
    assert lead.final_score == 10 and lead.tier == LeadTier.COLD   # global 40 would have said Warm
