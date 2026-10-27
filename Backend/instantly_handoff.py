"""
instantly_handoff.py — Give Instantly a lead and its whole email sequence
--------------------------------------------------------------------------
Instantly sends the emails (it rotates the mailboxes, warms them up, spaces the sends and stops a lead's
sequence when they reply). We write and approve the text, then add the lead to an Instantly campaign together
with every email of its sequence as a variable:

    email_1_subject, email_1_body, email_2_subject, email_2_body, ...

The campaign in Instantly has one step per email whose subject and body are just those variables (see
campaign_template()). Each lead therefore gets text written for it, on the schedule set in the campaign.

Also sent as variables: lead_id (comes back in every webhook), industry, audit_score and the top opportunities.
"""

import html
import re
from typing import Optional

from app_settings import get_app_settings
from config import settings
from database import Lead, OutreachMessage

_TITLES = {"dr", "mr", "mrs", "ms", "miss", "prof"}


def campaign_id() -> str:
    return (get_app_settings().get("instantlyCampaignId") or settings.INSTANTLY_CAMPAIGN_ID or "").strip()


def company() -> tuple[str, str]:
    """(company name, postal address). US anti-spam law requires the address in every commercial email."""
    cfg = get_app_settings()
    return (cfg.get("companyName") or "").strip(), (cfg.get("companyAddress") or "").strip()


def split_name(full_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """'Dr. Chris Lee' -> ('Chris', 'Lee'); a bare title is not a first name."""
    words = [w for w in (full_name or "").split() if w.rstrip(".").lower() not in _TITLES]
    if not words:
        return None, None
    return words[0], " ".join(words[1:]) or None


def to_html(text: str) -> str:
    """Plain text with blank-line paragraphs -> simple HTML, so Instantly keeps the line breaks."""
    paragraphs = [p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs)


def footer_html() -> str:
    name, address = company()
    lines = [x for x in (name, address) if x]
    return f"<p>{'<br>'.join(html.escape(x) for x in lines)}</p>" if lines else ""


def variables(lead: Lead, messages: list[OutreachMessage]) -> dict:
    """The custom variables for Instantly. Values must be text, numbers, true/false or null."""
    out: dict = {"lead_id": lead.id, "industry": lead.niche or "", "audit_score": lead.final_score or 0,
                 "email_steps": len(messages)}
    for n, gap in enumerate((lead.attributes or {}).get("top_gaps", [])[:3], start=1):
        out[f"top_gap_{n}"] = gap.get("label", "")
    footer = footer_html()
    for n, msg in enumerate(messages, start=1):
        out[f"email_{n}_subject"] = msg.subject or ""
        out[f"email_{n}_body"] = to_html(msg.body) + footer
    return out


def lead_fields(lead: Lead) -> dict:
    first, last = split_name(lead.linkedin_name)
    return {"first_name": first, "last_name": last, "company_name": lead.name, "website": lead.website,
            "phone": lead.phone, "personalization": ((lead.attributes or {}).get("top_gaps") or [{}])[0].get("label")}


def campaign_template(delays: list[int], steps: int) -> list[dict]:
    """What the Instantly campaign must contain: one step per email, using the variables above."""
    return [{"step": n, "wait_days": 0 if n == 1 else delays[n - 2] if n - 2 < len(delays) else 3,
             "subject": "{{email_%d_subject}}" % n, "body": "{{email_%d_body}}" % n} for n in range(1, steps + 1)]
