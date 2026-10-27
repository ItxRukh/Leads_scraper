import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import import_api
import industry_profiles as ip
import message_writer
import utils.site_signals as site_signals
from database import ImportBatch, IndustryProfile, Lead, LeadSource, LeadTier, get_db
from industries import industry_options
from scoring_engine import score_lead
from scripts.auditor import audit_lead

KEYS = [o["key"] for o in industry_options()]

GAP_FACTS = ("site_https", "site_has_click_to_call", "site_has_contact_form", "site_has_online_booking", "site_has_chat",
             "site_mentions_after_hours", "site_mentions_service_area", "site_has_patient_forms", "site_has_reviews",
             "site_mobile_friendly")
SIGN_FACTS = ("site_looks_outdated", "site_invites_estimates", "site_mentions_recurring_plan", "site_mentions_referrals",
              "site_multiple_locations")

# a site with nothing the agency's workflows would add, but plenty they build on
BARE_SITE = {"site_checked": True, **{f: False for f in GAP_FACTS}, **{f: True for f in SIGN_FACTS}}
# a site that already has everything
FULL_SITE = {"site_checked": True, **{f: True for f in GAP_FACTS}, **{f: False for f in SIGN_FACTS}}


def profile(db, key):
    return db.query(IndustryProfile).filter_by(slug=key).one()


def make_lead(db, key="hvac", site=None, website="https://acme.example", **kwargs):
    lead = Lead(name="Acme", website=website, source=LeadSource.MANUAL,
                profile_id=profile(db, key).id, attributes=dict(site or {}), **kwargs)
    db.add(lead)
    db.commit()
    return lead


# ─── the profiles ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", KEYS)
def test_every_industry_has_a_complete_profile(key, seeded_db):
    cfg = ip.build_config(key)
    assert sum(r.points for r in cfg.scoring.rules if r.group == "base") == 100
    assert [s.channel for s in cfg.sequence.steps] == ["email"] * 4                          # email only
    assert profile(seeded_db, key).name.endswith("(US)")
    assert 400 < len(cfg.offer.summary) < 700 and "AI-powered booking and follow-up" in cfg.offer.summary
    assert 5000 < len(cfg.offer.knowledge) <= 14000 and 1500 < len(cfg.offer.faq) <= 6000


@pytest.mark.parametrize("key", KEYS)
def test_no_price_appears_anywhere_in_the_offer(key):
    offer = ip.build_config(key).offer
    for text in (offer.summary, offer.faq, offer.knowledge):
        assert "$" not in text
    assert "scoped per business" in offer.faq and "Never quote prices" in offer.knowledge


@pytest.mark.parametrize("key", KEYS)
def test_every_service_a_rule_points_to_is_on_the_agency_website(key):
    """A rule's `fix` must be a workflow that really is in this industry's knowledge base (taken from the website)."""
    cfg = ip.build_config(key)
    for rule in cfg.scoring.rules:
        fix = rule.params.get("fix", "")
        for name in filter(None, (n.strip() for n in fix.split(" / "))):
            assert name.split(" (")[0] in cfg.offer.knowledge, f"{key}: '{name}' is not in the knowledge base"


def test_the_offer_uses_the_industry_s_own_workflows_and_boundaries():
    dental = ip.build_config("dental").offer
    assert "dental practices" in dental.summary and "does not diagnose" in dental.summary
    assert "New-Patient Missed-Call Rescue" in dental.knowledge and "Dentrix" in dental.faq
    assert "Review & Referral Request" in dental.knowledge

    hvac = ip.build_config("hvac").offer
    assert "Replacement Estimate Follow-Up" in hvac.knowledge and "Maintenance Agreement Renewals" in hvac.knowledge
    assert "ServiceTitan" in hvac.faq and "does not diagnose HVAC equipment" in hvac.knowledge

    law = ip.build_config("law-firms").offer
    assert "legal advice" in law.knowledge and "conflicts" in law.knowledge.lower()


def test_seeding_twice_adds_nothing(seeded_db):
    assert ip.seed_industry_profiles(seeded_db) == 0


def test_a_profile_nobody_edited_is_brought_up_to_date_but_an_edited_one_is_kept(seeded_db):
    stale, edited = profile(seeded_db, "hvac"), profile(seeded_db, "dental")
    stale.config = {**stale.config, "offer": {**stale.config["offer"], "summary": "old text"}}
    edited.config = {**edited.config, "offer": {**edited.config["offer"], "summary": "my own wording"}}
    edited.version = 2                                           # the Industry profiles page bumps this on every save
    seeded_db.commit()

    ip.seed_industry_profiles(seeded_db)
    assert profile(seeded_db, "hvac").config["offer"]["summary"] == ip.build_config("hvac").offer.summary
    assert profile(seeded_db, "dental").config["offer"]["summary"] == "my own wording"


def test_imported_leads_are_pointed_at_their_industry_profile(seeded_db):
    lead = Lead(name="Imported", source=LeadSource.MANUAL, import_batch_id=1, attributes={"industry_key": "plumbing"})
    seeded_db.add(lead)
    seeded_db.commit()
    ip.seed_industry_profiles(seeded_db)
    assert lead.profile_id == profile(seeded_db, "plumbing").id


# ─── scoring a website ───────────────────────────────────────────────────────

def test_a_bare_site_is_a_hot_lead_and_the_biggest_opportunities_come_first(seeded_db):
    result = score_lead(seeded_db, make_lead(seeded_db, "hvac", BARE_SITE, website_crawled=True))
    assert result.base == 100 and result.tier == LeadTier.HOT
    gaps = result.top_gaps()
    assert [g["key"] for g in gaps] == ["no_after_hours", "no_online_booking", "no_chat"]    # an urgent trade: after-hours first
    assert [g["points"] for g in gaps] == [17, 15, 15]
    assert gaps[0]["fix"] == "After-Hours Call Coverage" and gaps[1]["fix"] == "Approved Service Booking"


def test_the_same_site_weighs_differently_per_industry(seeded_db):
    dental = score_lead(seeded_db, make_lead(seeded_db, "dental", BARE_SITE)).top_gaps()
    assert dental[0]["key"] == "no_online_booking" and dental[0]["points"] == 28
    assert dental[0]["fix"] == "New-Patient Booking Workflow"

    pest = score_lead(seeded_db, make_lead(seeded_db, "pest-control", BARE_SITE)).top_gaps()
    assert pest[0]["key"] == "recurring_plan" and pest[0]["points"] == 22               # recurring plans are the heart of pest control
    assert pest[0]["fix"].startswith("Recurring-Plan Follow-Up")


def test_a_site_that_shows_estimates_and_plans_is_an_opportunity_not_a_gap(seeded_db):
    site = {**FULL_SITE, "site_invites_estimates": True}
    result = score_lead(seeded_db, make_lead(seeded_db, "roofing", site))
    assert result.base == 14 and result.top_gaps()[0]["fix"] == "Estimate & Proposal Follow-Up"


def test_a_well_equipped_site_is_a_cold_lead(seeded_db):
    result = score_lead(seeded_db, make_lead(seeded_db, "hvac", FULL_SITE, email="a@acme.example"))
    assert result.base == 0 and result.tier == LeadTier.COLD and result.top_gaps() == []


def test_without_website_details_nothing_is_scored_as_missing(seeded_db):
    result = score_lead(seeded_db, make_lead(seeded_db, "hvac", None))
    assert result.base == 0 and result.incomplete
    assert "could not be checked" in result.incomplete_reason

    no_site = score_lead(seeded_db, make_lead(seeded_db, "hvac", None, website=None))
    assert "no website found" in no_site.incomplete_reason


def test_bonus_points_for_an_email_a_named_contact_and_several_branches(seeded_db):
    site = {**FULL_SITE, "site_multiple_locations": True}
    clinic = score_lead(seeded_db, make_lead(seeded_db, "dental", site, email="a@acme.example", linkedin_name="Dana Rivera"))
    assert clinic.bonus == 15
    assert clinic.results[-1].fix.startswith("Multi-Location")                        # the industry's own routing workflow
    assert score_lead(seeded_db, make_lead(seeded_db, "hvac", site, email="a@acme.example", linkedin_name="Dana")).bonus == 10


# ─── the audit ───────────────────────────────────────────────────────────────

def fake_collect(facts, emails=()):
    return lambda url: {"facts": dict(facts), "text": "Acme Heating and Air", "emails": list(emails)}


def test_audit_checks_the_site_scores_it_and_remembers_the_top_opportunities(seeded_db, monkeypatch):
    monkeypatch.setattr(site_signals, "collect_site_signals", fake_collect(BARE_SITE, ["info@acme.example"]))
    lead = Lead(name="Acme", website="https://acme.example", source=LeadSource.MANUAL, profile_id=profile(seeded_db, "hvac").id)
    seeded_db.add(lead)
    seeded_db.commit()

    audit_lead(lead, seeded_db, use_claude=False)

    assert lead.website_crawled and lead.email == "info@acme.example"
    assert lead.tier == LeadTier.HOT and lead.base_score == 100 and lead.bonus_score == 5   # +5: the email found on the site
    assert [g["key"] for g in lead.attributes["top_gaps"]][0] == "no_after_hours"
    assert lead.attributes["site_checked"] is True and lead.audited_at is not None
    assert "we offer: After-Hours Call Coverage" in lead.audit_summary


def test_audit_does_not_check_the_site_twice(seeded_db, monkeypatch):
    calls = []
    monkeypatch.setattr(site_signals, "collect_site_signals",
                        lambda url: calls.append(url) or {"facts": dict(FULL_SITE), "text": "x", "emails": []})
    lead = make_lead(seeded_db, "hvac", None)
    audit_lead(lead, seeded_db, use_claude=False)
    audit_lead(lead, seeded_db, use_claude=False)
    assert len(calls) == 1


def test_an_unreachable_site_is_reported_not_punished(seeded_db, monkeypatch):
    monkeypatch.setattr(site_signals, "collect_site_signals", lambda url: None)
    lead = make_lead(seeded_db, "hvac", None)
    audit_lead(lead, seeded_db, use_claude=False)
    assert lead.final_score == 0 and lead.score_incomplete and not lead.website_crawled


# ─── what the AI is given ────────────────────────────────────────────────────

class Capture:
    def __init__(self):
        self.system = self.user = ""

    def __call__(self, system_prompt, user_prompt, **kwargs):
        self.system, self.user = system_prompt, user_prompt
        return '{"subject": "Quick idea", "body": "Hello there."}'


def test_a_first_email_gets_the_facts_the_matching_service_and_the_price_rule(seeded_db, monkeypatch):
    capture = Capture()
    monkeypatch.setattr(message_writer, "_call_groq", capture)
    lead = score_and_save(seeded_db, "hvac")
    cfg = ip.build_config("hvac")

    message_writer.write_message(lead, cfg.sequence.steps[0], cfg.sequence.steps, cfg, previous=[])

    assert "Facts about our service you may use" in capture.system
    assert "Missed calls turn into lost jobs" in capture.system                       # a problem from the website, in the prompt
    assert "Never state or hint at prices" in capture.system
    assert "our matching service: After-Hours Call Coverage" in capture.user
    assert "Maintenance Agreement Renewals" not in capture.system                    # the full catalogue is only for replies


def test_a_reply_gets_the_whole_knowledge_base_and_the_price_rule(seeded_db, monkeypatch):
    capture = Capture()
    monkeypatch.setattr(message_writer, "_call_groq", capture)
    lead = score_and_save(seeded_db, "dental")

    message_writer.write_reply(lead, "email", "Do you work with Dentrix? And what does it cost?", "question",
                               ip.build_config("dental"), previous=[])

    assert "Knowledge base (answer only from this and the FAQ)" in capture.system
    assert "Overdue Patient Reactivation" in capture.system and "Dentrix" in capture.system
    assert "Never state or hint at prices" in capture.system


def score_and_save(db, key):
    from scoring_engine import apply_result
    lead = make_lead(db, key, BARE_SITE, website_crawled=True, linkedin_name="Dana Rivera")
    apply_result(lead, score_lead(db, lead))
    db.commit()
    return lead


# ─── auditing a whole import ─────────────────────────────────────────────────

@pytest.fixture
def api(seeded_db, monkeypatch):
    original = import_api.audit_batch
    monkeypatch.setattr(import_api, "audit_batch", lambda batch_id: original(batch_id, db=seeded_db))
    app = FastAPI()
    app.include_router(import_api.router)
    app.dependency_overrides[get_db] = lambda: seeded_db
    return TestClient(app), seeded_db


def test_audit_a_batch_then_read_the_progress(api, monkeypatch):
    client, db = api
    facts_by_site = {"https://bare.example": BARE_SITE, "https://full.example": FULL_SITE}
    monkeypatch.setattr(site_signals, "collect_site_signals",
                        lambda url: {"facts": dict(facts_by_site[url]), "text": "x", "emails": []} if url in facts_by_site else None)

    client.post("/imports/commit", json={"batch_name": "x", "rows": [
        {"business_name": "Bare", "website": "bare.example", "industry": "hvac"},
        {"business_name": "Full", "website": "full.example", "industry": "hvac"},
        {"business_name": "Down", "website": "down.example", "industry": "hvac"}]})
    batch = db.query(ImportBatch).one()
    assert db.query(Lead).filter_by(profile_id=profile(db, "hvac").id).count() == 3        # imported straight into the HVAC profile

    assert client.post(f"/imports/{batch.id}/audit").json() == {"started": True, "pending": 3}
    status = client.get(f"/imports/{batch.id}/audit-status").json()
    assert status == {"total": 3, "audited": 3, "hot": 1, "warm": 0, "cold": 2, "incomplete": 1}

    assert client.post(f"/imports/{batch.id}/audit").json() == {"started": False, "pending": 0}   # nothing left to do
    assert client.get("/imports").json()[0]["audited"] == 3


def test_audit_endpoints_reject_unknown_batches(api):
    client, _ = api
    assert client.post("/imports/999/audit").status_code == 404
    assert client.get("/imports/999/audit-status").status_code == 404


def test_a_contact_with_a_title_is_greeted_by_title_and_surname():
    assert message_writer._first_name(Lead(name="x", linkedin_name="Dr. Chris Lee")) == "Dr. Lee"
    assert message_writer._first_name(Lead(name="x", linkedin_name="mrs patel")) == "Mrs. patel"
    assert message_writer._first_name(Lead(name="x", linkedin_name="Dana Rivera")) == "Dana"
    assert message_writer._first_name(Lead(name="x", linkedin_name="Dr.")) == "Dr."
    assert message_writer._first_name(Lead(name="x")) is None


def test_the_rule_against_unwritten_promises_reaches_both_prompts(seeded_db, monkeypatch):
    capture = Capture()
    monkeypatch.setattr(message_writer, "_call_groq", capture)
    lead = score_and_save(seeded_db, "hvac")
    cfg = ip.build_config("hvac")
    message_writer.write_message(lead, cfg.sequence.steps[0], cfg.sequence.steps, cfg, previous=[])
    first = capture.system
    message_writer.write_reply(lead, "email", "Do you work with Zoho?", "question", cfg, previous=[])
    for prompt in (first, capture.system):
        assert "Only name a specific software product" in prompt
