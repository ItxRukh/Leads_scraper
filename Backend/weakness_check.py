"""
weakness_check.py — Is the weakness written in the list really true? If not, correct it.
-----------------------------------------------------------------------------------------
The list may say what a business is missing ("no online booking, no chat"). After the website audit has collected
what the site has, each point is checked and the lead's `weakness` is corrected, so the email is built on the truth:

    the point matches the site        -> it stays, word for word
    the site has the thing            -> the point is removed
    every point was wrong             -> the real gaps the audit found are written instead
    nothing was written in the list   -> the real gaps the audit found are written
    the check cannot tell             -> the point stays (nothing proves it wrong)

The original text is kept in `weakness_original`, so an edit can be undone. Nothing in the text is invented: the new
words come only from the audit's own gap labels, and a point is removed only when the site's facts (or, for points the
audit has no check for, a quote that really appears on the site) show the thing is there.
"""

import json
import re
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from database import Lead

# words in a point -> the site fact that tells whether it is true, and whether a "no" can be trusted
#   (pattern, fact, fact value that makes the point true, trusted when the fact says the point is false)
_RULES = [
    (r"\bbook(ing)?\b|appointment|\bschedul|reserv", "site_has_online_booking", False, True),
    (r"\bchat|text.?back|messag(e|ing) widget|live agent", "site_has_chat", False, True),
    (r"click.?to.?call|tap.?to.?call|call button|phone link|tel link", "site_has_click_to_call", False, True),
    (r"patient form|intake|new patient", "site_has_patient_forms", False, True),
    (r"contact form|inquiry form|enquiry form|lead form|\bform\b", "site_has_contact_form", False, True),
    (r"after.?hours|24 ?/ ?7|24 hour|emergency|weekend|\bnight", "site_mentions_after_hours", False, True),
    (r"review|testimonial|rating", "site_has_reviews", False, True),
    (r"mobile|responsive", "site_mobile_friendly", False, True),
    (r"https|ssl|not secure|secure site", "site_https", False, True),
    (r"service area|areas? we serve|coverage area", "site_mentions_service_area", False, True),
    (r"estimate|free quote|\bquotes?\b", "site_invites_estimates", False, True),
    (r"maintenance|membership|recurring|subscription plan", "site_mentions_recurring_plan", False, True),
    (r"referral", "site_mentions_referrals", False, True),
    (r"outdated|\bdated\b|old site|old.?fashioned|copyright year", "site_looks_outdated", True, False),
]

_SPLIT = re.compile(r"\s*(?:[;\n\r•|]+|,|\band\b|&)\s*", re.I)
MAX_GAPS = 2


def split_points(text: str) -> list[str]:
    return [p.strip(" .-–—*") for p in _SPLIT.split(text or "") if p and p.strip(" .-–—*")]


def _rule_for(point: str):
    return next((rule for rule in _RULES if re.search(rule[0], point, re.I)), None)


def _fact_verdict(point: str, facts: dict) -> Optional[str]:
    """'true', 'false' or 'unclear' from the audit's facts; None when no fact applies to this point."""
    rule = _rule_for(point)
    if rule is None:
        return None
    _, fact, true_when, trusted = rule
    value = facts.get(fact)
    if value is None:
        return "unclear"
    if value == true_when:
        return "true"
    return "false" if trusted else "unclear"


def _parse_json(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _judge_other_points(points: list[str], site_text: str) -> dict[str, bool]:
    """
    For points the audit has no check for ("no blog", "no financing options"): which ones does the site's own text
    show to be wrong? A point is returned as wrong only if the AI quotes words that really appear in the text.
    """
    if not points or len(site_text or "") < 200:
        return {}
    from utils import groq_helper

    system = ("You check claims about what a small business website is MISSING. For each claim, say whether the "
              "website text proves the thing IS present. Answer only with JSON: "
              '{"results": [{"claim": "...", "present": true or false, "quote": "exact words from the text or empty"}]}. '
              "Say present: true only when the text clearly shows it, and copy the exact words as the quote.")
    reply = _parse_json(groq_helper._call_groq(system, f"Claims: {json.dumps(points)}\n\nWebsite text:\n{site_text[:6000]}",
                                               max_tokens=600, temperature=0))
    haystack = re.sub(r"\s+", " ", site_text).lower()
    wrong: dict[str, bool] = {}
    for item in (reply or {}).get("results", []):
        quote = re.sub(r"\s+", " ", str(item.get("quote") or "")).strip().lower()
        if item.get("present") is True and len(quote) >= 12 and quote in haystack:
            wrong[str(item.get("claim"))] = True
    return wrong


def _gap_labels(lead: Lead) -> list[str]:
    return [g["label"] for g in (lead.attributes or {}).get("top_gaps", []) if g.get("label")][:MAX_GAPS]


def check(lead: Lead) -> dict:
    """Work out the corrected weakness for one audited lead. Does not save anything."""
    attrs = lead.attributes or {}
    original = (attrs.get("weakness_original") or "").strip()
    facts = {k: v for k, v in attrs.items() if k.startswith("site_")}
    checked = bool(facts.get("site_checked"))
    points = split_points(original)

    verdicts: dict[str, str] = {}                                  # point -> "true" | "false" | "unclear"
    unmapped = []
    for point in points:
        verdict = _fact_verdict(point, facts) if checked else "unclear"
        if verdict is None:
            unmapped.append(point)
            verdict = "unclear"
        verdicts[point] = verdict
    if checked and unmapped:
        for point in _judge_other_points(unmapped, lead.crawled_text or ""):
            if point in verdicts:
                verdicts[point] = "false"

    dropped = [p for p in points if verdicts[p] == "false"]
    kept = [p for p in points if verdicts[p] != "false"]

    if not points:                                                  # nothing was written: use what the audit found
        final = "; ".join(_gap_labels(lead)) if checked else ""
        note = "No weakness in the list: taken from the website check." if final else ""
    elif not dropped:
        final, note = original, ""                                  # same as written, word for word
    elif kept:
        final = ", ".join(kept)
        note = "Removed (the website has it): " + "; ".join(dropped)
    else:
        final = "; ".join(_gap_labels(lead))
        note = "None of the listed points matched the website: " + "; ".join(dropped)
    return {"weakness": final, "edited": final != original, "note": note, "checked": checked}


def apply(lead: Lead) -> dict:
    """Check one lead and store the result on it. The caller commits."""
    result = check(lead)
    lead.attributes = {**(lead.attributes or {}), "weakness": result["weakness"], "weakness_edited": result["edited"],
                       "weakness_note": result["note"], "weakness_checked": True}
    return result


def check_batch(batch_id: int, db: Session) -> int:
    """Check every audited lead of a batch that has not been checked yet. One failing lead does not stop the rest."""
    done = 0
    for lead in db.query(Lead).filter(Lead.import_batch_id == batch_id, Lead.audited_at.isnot(None)):
        if (lead.attributes or {}).get("weakness_checked"):
            continue
        try:
            apply(lead)
            db.commit()
            done += 1
        except Exception as e:
            logger.error(f"[weakness] lead {lead.id}: {e}")
            db.rollback()
    return done
