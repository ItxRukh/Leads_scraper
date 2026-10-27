"""
industry_profiles.py — Ready-made Industry Profiles for US local-service industries
-------------------------------------------------------------------------------------
One profile per industry in industries.py (the profile's slug is the industry key, so a lead imported
with that industry is scored under it). Everything in a profile comes from our agency website:

  * What we offer, the knowledge base and the FAQ: `knowledge/<industry>.md`, built from the website's
    workflows, boundaries, process and timelines. No prices are ever included.
  * Scoring: how much the business's website shows or lacks of the things our workflows fix. Each rule
    names the workflow that addresses it (`fix`), so a message can point at the right service. A high
    score means a big opportunity, so Hot = the most to gain.
  * Contact is by email only, in four steps over two weeks. SMS and calls start only after a reply, from GHL.

`seed_industry_profiles(db)` is safe to run on every start. It adds profiles that are missing and refreshes
a profile only while it has never been edited (version 1); once someone edits it on the Industry profiles
page, it is left alone.
"""

import re
from pathlib import Path

from loguru import logger
from sqlalchemy.orm import Session

from database import IndustryProfile, Lead
from industries import industry_label, industry_options
from profile_schema import ProfileConfig

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

# ─── The checks (rule key -> lead field, value that earns the points, wording) ─

_GAPS = {                     # earn points when the website LACKS something (field == False)
    "no_online_booking": ("site_has_online_booking",   "No way to book or request an appointment online was found on the website"),
    "no_chat":           ("site_has_chat",             "No chat or text-back widget was found on the website"),
    "no_click_to_call":  ("site_has_click_to_call",    "No tap-to-call phone link was found on the website"),
    "no_contact_form":   ("site_has_contact_form",     "No contact or enquiry form was found on the website"),
    "no_after_hours":    ("site_mentions_after_hours", "The website does not mention after-hours or emergency availability"),
    "no_service_area":   ("site_mentions_service_area", "The website does not say which areas it serves"),
    "no_patient_forms":  ("site_has_patient_forms",    "No online forms or new-patient / new-client intake were found on the website"),
    "no_reviews_shown":  ("site_has_reviews",          "No customer reviews or testimonials were found on the website"),
    "not_mobile_ready":  ("site_mobile_friendly",      "The website does not appear to be set up for phones"),
    "no_https":          ("site_https",                "The website is not served over a secure (https) connection"),
}
_SIGNS = {                    # earn points when the website SHOWS something that our workflows build on (field == True)
    "outdated_site":     ("site_looks_outdated",       "The website's footer shows a copyright year several years old"),
    "invites_estimates": ("site_invites_estimates",    "The website invites estimate, quote or inspection requests, so open estimates need follow-up"),
    "recurring_plan":    ("site_mentions_recurring_plan", "The website sells maintenance or recurring plans, so renewals and plan follow-up matter"),
    "referral_traffic":  ("site_mentions_referrals",   "The website invites referrals, so referral intake and status updates matter"),
}

# short names used in the tables below -> rule keys
_SHORT = {"booking": "no_online_booking", "chat": "no_chat", "call": "no_click_to_call", "form": "no_contact_form",
          "after": "no_after_hours", "area": "no_service_area", "forms": "no_patient_forms", "reviews": "no_reviews_shown",
          "estimates": "invites_estimates", "recurring": "recurring_plan", "referrals": "referral_traffic"}

# Every site gets these three; the industry weights below add up to the other 88 points.
_HYGIENE = {"no_https": 3, "not_mobile_ready": 5, "outdated_site": 4}
_HYGIENE_FIX = "Full Website Rebuild (optional service)"

# industry -> (weights, workflow of ours that fixes each). Workflow names are the ones on our agency website;
# a test checks that every name is in the industry's knowledge file.
_SPEC = {
    "hvac": (dict(booking=15, chat=15, call=8, form=7, after=17, area=6, estimates=8, recurring=7, reviews=5),
             dict(booking="Approved Service Booking", chat="Missed-Call Job Rescue / Instant Web-Lead Response", call="24/7 HVAC AI Receptionist",
                  form="Service & Estimate Request Intake", after="After-Hours Call Coverage", area="Service-Area Checker",
                  estimates="Replacement Estimate Follow-Up", recurring="Maintenance Agreement Renewals")),
    "plumbing": (dict(booking=15, chat=17, call=8, form=7, after=22, area=6, estimates=6, reviews=7),
                 dict(booking="Booking & Dispatch Handoff", chat="Instant Web-Lead Response", call="24/7 Plumbing AI Receptionist",
                      form="Structured Service Request Intake", after="After-Hours Missed-Call Rescue", area="ZIP, Capacity & Job-Type Router",
                      estimates="Open Estimate Follow-Up", reviews="Job-Complete Customer Follow-Up")),
    "electrical": (dict(booking=15, chat=15, call=8, form=8, after=18, area=8, estimates=10, reviews=6),
                   dict(booking="Booking Request & Dispatch Handoff", chat="Missed-Call Service Capture", call="Electrical AI Receptionist",
                        form="Job-Type & ZIP Intake", after="After-Hours Service Intake", area="Service-Area Checker",
                        estimates="Open-Estimate Follow-Up")),
    "garage-door": (dict(booking=15, chat=17, call=10, form=7, after=18, area=8, estimates=8, reviews=5),
                    dict(booking="Repair Appointment Booking", chat="Urgent Repair Lead Rescue", call="Garage Door AI Receptionist",
                         form="Repair-Request Intake", after="After-Hours Request Capture", area="Service-Area Checker",
                         estimates="Installation Estimate Follow-Up / Price-Shopper Follow-Up")),
    "roofing": (dict(booking=18, chat=17, call=7, form=10, after=8, area=6, estimates=14, reviews=8),
                dict(booking="Inspection Booking Workflow", chat="Instant Inspection Lead Response", call="Missed-Call Roofing Lead Recovery",
                     form="Lead-to-Rep Routing", after="Storm-Surge Lead Router", area="Lead-to-Rep Routing",
                     estimates="Estimate & Proposal Follow-Up")),
    "pest-control": (dict(booking=15, chat=15, call=8, form=8, after=6, area=8, recurring=22, reviews=6),
                     dict(booking="Initial-Service Booking", chat="Peak-Season Lead Rescue", call="Pest Control AI Receptionist",
                          form="Approved Pest-Service FAQ", after="Pest Control AI Receptionist", area="Service-Area Checker",
                          recurring="Recurring-Plan Follow-Up / Cancellation & Renewal Reminder")),
    "solar": (dict(booking=20, chat=17, call=6, form=12, after=4, area=8, estimates=16, reviews=5),
              dict(booking="Solar Consultation Booking", chat="Instant Solar Lead Response", call="Missed-Call Lead Recovery",
                   form="Instant Solar Lead Response", after="After-Hours Lead Coverage", area="Service-Area & Project-Fit Checker",
                   estimates="Solar Proposal Follow-Up")),
    "construction": (dict(booking=15, chat=12, call=8, form=16, after=4, area=8, estimates=20, reviews=5),
                     dict(booking="Site Visit & Estimate Scheduler", chat="Instant Bid Request Response", call="Missed Bid Inquiry Rescue",
                          form="Project Intake Assistant", after="After-Hours Emergency Call Routing", area="Project Location Qualifier",
                          estimates="Open Bid Follow-Up")),
    "home-services": (dict(booking=15, chat=15, call=8, form=8, after=14, area=8, estimates=8, recurring=6, reviews=6),
                      dict(booking="Booking & Dispatch Handoff", chat="Instant Web-Lead Response", call="24/7 Home Service AI Receptionist",
                           form="Structured Job-Request Intake", after="After-Hours Call Coverage", area="Service-Area Checker",
                           estimates="Open Estimate Follow-Up", recurring="Seasonal Customer Outreach", reviews="Job-Complete Customer Follow-Up")),
    "landscaping": (dict(booking=12, chat=14, call=6, form=14, area=8, estimates=14, recurring=14, reviews=6),
                    dict(booking="Estimate Booking Workflow", chat="Quote-Request Rescue", call="Missed-Call Text-Back",
                         form="Smart Property & Service Intake", area="Service-Area Checker", estimates="Open-Estimate Follow-Up",
                         recurring="Recurring Service Follow-Up / Seasonal Client Reactivation")),
    "dental": (dict(booking=28, chat=17, call=8, form=7, after=8, forms=12, reviews=8),
               dict(booking="New-Patient Booking Workflow", chat="Instant New-Patient Lead Response", call="New-Patient Missed-Call Rescue",
                    form="Instant New-Patient Lead Response", after="After-Hours Inquiry Capture", forms="Digital New-Patient Intake Checklist",
                    reviews="Review & Referral Request")),
    "chiropractic": (dict(booking=26, chat=16, call=8, form=7, after=8, forms=12, reviews=11),
                     dict(booking="New-Patient Booking Workflow", chat="New-Patient Lead Rescue", call="Missed-Call Text-Back",
                          form="New-Patient Lead Rescue", after="After-Hours Inquiry Capture", forms="New-Patient Intake Checklist",
                          reviews="Review & Referral Request")),
    "med-spa": (dict(booking=24, chat=24, call=6, form=10, after=10, reviews=14),
                dict(booking="Consultation Booking Workflow", chat="Instant Consultation Lead Rescue", call="Missed-Call Lead Rescue",
                     form="Treatment-Interest Lead Concierge", after="After-Hours Consultation Capture", reviews="Review & Referral Request")),
    "physical-therapy": (dict(booking=18, chat=12, call=8, form=8, after=4, forms=14, referrals=16, reviews=8),
                         dict(booking="Evaluation Booking Request", chat="Missed-Call Recovery", call="Missed-Call Recovery",
                              form="Referral Acknowledgement Workflow", after="PT AI Call Handler", forms="Intake Completion Reminder",
                              referrals="Referral-to-Evaluation Follow-Up")),
    "dermatology": (dict(booking=20, chat=16, call=8, form=10, after=6, forms=8, referrals=12, reviews=8),
                    dict(booking="Appointment Request Capture", chat="Instant Cosmetic Lead Response", call="Missed-Call Recovery",
                         form="Medical vs. Cosmetic Inquiry Router", after="After-Hours Inquiry Capture", forms="Referral Intake Checklist",
                         referrals="Referral Intake Checklist")),
    "orthopedic": (dict(booking=18, chat=12, call=10, form=10, after=6, forms=10, referrals=18, reviews=4),
                   dict(booking="Consultation Request Follow-Up", chat="Web Inquiry Speed-to-Response", call="Missed-Call Consultation Recovery",
                        form="Approved Request Routing", after="Orthopedic AI Receptionist", forms="Incomplete Intake Follow-Up",
                        referrals="Referral Intake Capture")),
    "veterinary": (dict(booking=26, chat=20, call=10, form=10, after=10, forms=6, reviews=6),
                   dict(booking="Appointment Request Capture", chat="Missed-Call Text-Back", call="Call-Overflow Handler",
                        form="New-Client Intake Workflow", after="After-Hours Message & Escalation", forms="New-Client Intake Workflow")),
    "law-firms": (dict(booking=20, chat=22, call=8, form=18, after=12, referrals=4, reviews=4),
                  dict(booking="Consultation Request & Scheduling", chat="Instant Web-Inquiry Response", call="New-Matter Missed-Call Response",
                       form="Structured New-Matter Intake", after="24/7 Legal Intake Assistant", referrals="Referral Intake & Follow-Up")),
    "radiology": (dict(booking=8, chat=14, call=12, form=20, after=14, referrals=16, reviews=4),
                  dict(booking="Referral Status Update Workflow", chat="Referrer Inquiry Response Workflow", call="After-Hours Referrer Call Assistant",
                       form="Referral Intake Completeness Workflow", after="After-Hours Referrer Call Assistant",
                       referrals="Referral Intake Completeness Workflow")),
    "imaging": (dict(booking=16, chat=14, call=12, form=14, after=14, referrals=14, reviews=4),
                dict(booking="Appointment Request Follow-Up", chat="Missed-Call Scan Request Recovery", call="Missed-Call Scan Request Recovery",
                     form="Referral Intake Capture", after="After-Hours Patient Access Assistant", referrals="Referral Follow-Up Sequence")),
    "other": (dict(booking=20, chat=16, call=10, form=12, after=8, area=4, estimates=8, reviews=10),
              dict(booking="Booking & Dispatch Handoff", chat="Instant Web-Lead Response", call="24/7 Home Service AI Receptionist",
                   form="Structured Job-Request Intake", after="After-Hours Call Coverage", area="Service-Area Checker",
                   estimates="Open Estimate Follow-Up", reviews="Job-Complete Customer Follow-Up")),
}

# Industries whose workflows include routing between locations: a second branch earns a small bonus.
_MULTI_LOCATION = {"chiropractic", "dental", "dermatology", "electrical", "garage-door", "home-services", "imaging", "landscaping",
                   "law-firms", "med-spa", "orthopedic", "pest-control", "physical-therapy", "radiology", "roofing", "veterinary"}

# Email only, four steps over two weeks. Delays count from the previous step: day 0, 3, 7 and 14.
_SEQUENCE = [
    {"key": "email_1", "label": "First email",       "channel": "email", "delay_days": 0, "min_tier": "warm"},
    {"key": "email_2", "label": "Follow-up email",   "channel": "email", "delay_days": 3, "min_tier": "warm"},
    {"key": "email_3", "label": "New-angle email",   "channel": "email", "delay_days": 4, "min_tier": "warm"},
    {"key": "email_4", "label": "Closing note",      "channel": "email", "delay_days": 7, "min_tier": "warm"},
]

_MARKER = re.compile(r"^=== (SUMMARY|FAQ|KNOWLEDGE) ===\s*$", re.M)


def load_content(key: str) -> dict[str, str]:
    """{'summary', 'faq', 'knowledge'} from knowledge/<key>.md."""
    parts = _MARKER.split((KNOWLEDGE_DIR / f"{key}.md").read_text(encoding="utf-8"))
    return {name.lower(): body.strip() for name, body in zip(parts[1::2], parts[2::2])}


def _rule(key: str, field: str, label: str, points: int, expected: bool, fix: str = "", group: str = "base") -> dict:
    params = {"field": field, "op": "==", "value": expected}
    if fix:
        params["fix"] = fix
    return {"key": key, "label": label, "kind": "field", "points": points, "group": group,
            "requires": ["site_data"], "params": params}


def build_config(key: str) -> ProfileConfig:
    weights, fixes = _SPEC[key]
    content = load_content(key)

    rules = []
    for short, points in weights.items():
        rule_key = _SHORT[short]
        field, label = _GAPS[rule_key] if rule_key in _GAPS else _SIGNS[rule_key]
        rules.append(_rule(rule_key, field, label, points, expected=rule_key in _SIGNS, fix=fixes.get(short, "")))
    for rule_key, points in _HYGIENE.items():
        field, label = _GAPS[rule_key] if rule_key in _GAPS else _SIGNS[rule_key]
        rules.append(_rule(rule_key, field, label, points, expected=rule_key in _SIGNS, fix=_HYGIENE_FIX))

    rules += [
        {"key": "has_email", "label": "An email address is on file", "kind": "field", "points": 5, "group": "bonus",
         "params": {"field": "email", "op": "exists"}},
        {"key": "named_contact", "label": "A named contact person is on file", "kind": "field", "points": 5, "group": "bonus",
         "params": {"field": "linkedin_name", "op": "exists"}},
    ]
    if key in _MULTI_LOCATION:
        routing = re.search(r"\*\*(Multi-[^*]+)\*\*", content["knowledge"])          # this industry's own routing workflow
        rules.append(_rule("multiple_locations", "site_multiple_locations",
                           "The business has more than one location, so routing between them matters", 5, True,
                           fix=routing.group(1) if routing else "", group="bonus"))

    return ProfileConfig.model_validate({
        "search": {"query_template": "{niche} {city}", "default_niche": industry_label(key)},
        "offer": {"summary": content["summary"], "tone": "friendly, practical, short sentences", "language": "en",
                  "faq": content["faq"], "knowledge": content["knowledge"]},
        "scoring": {"rules": rules},
        "sequence": {"steps": _SEQUENCE},
    })


def seed_industry_profiles(db: Session) -> int:
    """
    Adds any missing industry profile, refreshes profiles nobody has edited, then attaches already-imported
    leads to their industry's profile. Returns the number of profiles added.
    """
    existing = {p.slug: p for p in db.query(IndustryProfile)}
    added = refreshed = 0
    for option in industry_options():
        key = option["key"]
        if key not in _SPEC:
            continue
        config = build_config(key).model_dump()
        description = f"Scores what a {option['label']} website shows or lacks, and offers AI booking and follow-up built from our agency website."
        profile = existing.get(key)
        if profile is None:
            db.add(IndustryProfile(name=f"{option['label']} (US)", slug=key, target_type="business",
                                   description=description, config=config))
            added += 1
        elif (profile.version or 1) == 1 and (profile.config != config or profile.description != description):
            profile.config, profile.description = config, description        # never edited, so safe to bring up to date
            refreshed += 1
    if added or refreshed:
        db.commit()
        logger.info(f"Industry profiles: {added} created, {refreshed} refreshed")

    # Leads imported before their profile existed keep the default profile until they are pointed at their own.
    profiles = {p.slug: p.id for p in db.query(IndustryProfile)}
    attached = 0
    for lead in db.query(Lead).filter(Lead.import_batch_id.isnot(None)):
        key = (lead.attributes or {}).get("industry_key")
        if key in profiles and lead.profile_id != profiles[key] and not lead.audited_at:
            lead.profile_id = profiles[key]
            attached += 1
    if attached:
        db.commit()
        logger.info(f"Attached {attached} imported lead(s) to their industry profile")
    return added
