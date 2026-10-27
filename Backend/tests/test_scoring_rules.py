"""Rule kinds on a custom (non-dropship) profile."""
import pytest

import profile_service as svc
from database import EmailLog, Lead, LeadTier
from profile_schema import ProfileIn
from scoring_engine import apply_result, score_lead


@pytest.fixture()
def hvac(seeded_db):
    return svc.create_profile(seeded_db, ProfileIn(name="HVAC", config={
        "search": {"query_template": "HVAC contractor {city}"},
        "scoring": {"hot_threshold": 50, "warm_threshold": 25, "rules": [
            {"key": "emergency", "label": "Advertises 24/7 emergency service", "kind": "keyword",
             "points": 30, "requires": ["website"],
             "params": {"any": ["24/7", "emergency service"]}},
            {"key": "online_booking", "label": "No online booking", "kind": "keyword",
             "points": 20, "requires": ["website"],
             "params": {"any": ["call us"], "none": ["book online", "schedule online"]}},
            {"key": "phone_pattern", "label": "Phone number on site", "kind": "regex",
             "points": 5, "requires": ["website"], "params": {"pattern": r"\(\d{3}\)\s?\d{3}-\d{4}"}},
            {"key": "many_reviews", "label": "50+ reviews", "kind": "field", "points": 10,
             "params": {"field": "review_count", "op": ">=", "value": 50}},
            {"key": "good_category", "label": "Category is HVAC", "kind": "field", "points": 5,
             "params": {"field": "category", "op": "contains", "value": "hvac"}},
            {"key": "chain", "label": "Part of a franchise", "kind": "field", "points": -15,
             "params": {"field": "franchise", "op": "==", "value": True}},
            {"key": "uses_chatbot", "label": "Already has a chatbot", "kind": "ai", "points": -10,
             "params": {"question": "Does the site have a chatbot?"}},
            {"key": "replied", "label": "Replied", "kind": "intent", "points": 40, "group": "bonus",
             "params": {"event": "replied"}},
        ]},
    }))


def make(**kw):
    base = dict(name="Cool Air", website="https://coolair.com", website_crawled=True,
                review_count=60, rating=4.5, email="a@coolair.com",
                crawled_text="Call us for 24/7 emergency service at (305) 555-0100",
                attributes={"category": "HVAC contractor"})
    base.update(kw)
    return Lead(**base)


def test_all_rule_kinds(seeded_db, hvac):
    res = score_lead(seeded_db, make(), hvac)
    by = {r.key: r for r in res.results}
    assert by["emergency"].status == "matched"
    assert by["online_booking"].status == "matched"       # "call us" present, no "book online"
    assert by["phone_pattern"].status == "matched"
    assert by["many_reviews"].earned == 10
    assert by["good_category"].earned == 5                # read from attributes
    assert by["chain"].status == "not_matched"
    assert res.base == 30 + 20 + 5 + 10 + 5 - 0
    assert res.tier == LeadTier.HOT                       # profile thresholds (50/25), not global
    assert res.signals["emergency"] is True


def test_none_words_cancel_a_keyword_rule(seeded_db, hvac):
    lead = make(crawled_text="call us or book online for 24/7 emergency service")
    by = {r.key: r for r in score_lead(seeded_db, lead, hvac).results}
    assert by["online_booking"].status == "not_matched"


def test_penalty_subtracts_and_unanswered_ai_rule_is_not_checked(seeded_db, hvac):
    res = score_lead(seeded_db, make(attributes={"franchise": True}), hvac)
    by = {r.key: r for r in res.results}
    assert by["chain"].earned == -15
    assert by["uses_chatbot"].status == "not_checked"     # no AI answer stored yet
    assert res.incomplete is True


def test_ai_answer_stored_in_signals_is_used(seeded_db, hvac):
    by = {r.key: r for r in score_lead(seeded_db, make(signals={"uses_chatbot": True}), hvac).results}
    assert by["uses_chatbot"].earned == -10


def test_text_rules_not_checked_without_a_crawled_website(seeded_db, hvac):
    lead = make(website=None, website_crawled=False, crawled_text=None)
    res = score_lead(seeded_db, lead, hvac)
    by = {r.key: r for r in res.results}
    assert by["emergency"].status == "not_checked"
    assert "no website" in by["emergency"].note
    assert res.incomplete and "Advertises 24/7" in res.incomplete_reason


def test_intent_rule_counts_a_reply(seeded_db, hvac):
    lead = make()
    seeded_db.add(lead)
    seeded_db.commit()
    seeded_db.add(EmailLog(lead_id=lead.id, replied=True))
    seeded_db.commit()
    seeded_db.refresh(lead)
    assert score_lead(seeded_db, lead, hvac).bonus == 40


def test_apply_result_writes_everything_onto_the_lead(seeded_db, hvac):
    lead = make()
    seeded_db.add(lead)
    seeded_db.commit()
    apply_result(lead, score_lead(seeded_db, lead, hvac))
    seeded_db.commit()
    assert lead.profile_id == hvac.id
    assert lead.final_score == lead.base_score + lead.bonus_score
    assert lead.tier == LeadTier.HOT
    assert lead.score_breakdown["profile"]["name"] == "HVAC"
    assert any(r["key"] == "emergency" for r in lead.score_breakdown["rules"])
    assert "FINAL SCORE" in lead.audit_summary
    assert lead.audited_at is not None


def test_leads_without_a_profile_use_the_default(seeded_db):
    res = score_lead(seeded_db, make(dropship_mentioned=True))
    assert res.profile_name == "Dropship Suppliers"
