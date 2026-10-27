"""
ghl_sync.py — Hand an interested lead to GoHighLevel
-----------------------------------------------------
A lead that answers well (interested, or asking a real question) is sent to a GoHighLevel workflow through its
Inbound Webhook (GHL_WEBHOOK_URL). From there SMS, calls and booking are GHL's job, because the lead has now
reached out to us. What GHL receives is described in docs/ghl-outbound.md.

push() never raises: the result is stored on the lead (attributes["ghl"]) so the page can show it and a failed
push can be retried. A lead is sent once; pass force=True to send again.
"""

from datetime import datetime
from typing import Optional

import requests
from loguru import logger
from sqlalchemy.orm import Session

from config import settings
from database import InboundMessage, Lead
from industries import industry_label

TIMEOUT_SECONDS = 10


def configured() -> bool:
    return bool(settings.GHL_WEBHOOK_URL)


def _contact_names(lead: Lead) -> tuple[str, str]:
    from instantly_handoff import split_name
    first, last = split_name(lead.linkedin_name)
    return first or "", last or ""


def build_payload(lead: Lead, event: str, inbound: Optional[InboundMessage] = None,
                  suggested_reply: Optional[str] = None, is_test: bool = False) -> dict:
    attrs = lead.attributes or {}
    first, last = _contact_names(lead)
    key = attrs.get("industry_key") or ""
    gaps = attrs.get("top_gaps", [])
    tags = ["outbound", f"industry-{key}" if key else "industry-unknown"]
    if inbound is not None and inbound.label:
        tags.append(inbound.label)
    return {
        "source": "outbound-prospecting",
        "event": event,
        "isTest": is_test,
        "leadId": lead.id,
        "businessName": lead.name,
        "firstName": first, "lastName": last, "contactName": lead.linkedin_name or "",
        "email": lead.email or "", "phone": lead.phone or "", "website": lead.website or "",
        "city": lead.city or "", "state": attrs.get("state") or "",
        "industry": industry_label(key) or lead.niche or "", "industryKey": key,
        "tags": tags,
        "auditScore": lead.final_score or 0, "tier": lead.tier.value if lead.tier else "",
        "topOpportunities": [g.get("label", "") for g in gaps],
        "matchingServices": [g["fix"] for g in gaps if g.get("fix")],
        "replyLabel": inbound.label if inbound else "", "replySummary": inbound.summary if inbound else "",
        "replyText": (inbound.body or "")[:2000] if inbound else "",
        "suggestedReply": suggested_reply or "",
    }


def _suggest_reply(db: Session, lead: Lead, inbound: InboundMessage) -> Optional[str]:
    """A draft answer from the industry's knowledge base, for the person who continues the conversation. May be None."""
    from message_writer import write_reply
    from profile_schema import ProfileConfig
    from scoring_engine import resolve_profile

    profile = resolve_profile(db, lead)
    if profile is None or not inbound.body:
        return None
    try:
        draft = write_reply(lead, "email", inbound.body, inbound.label or "question",
                            ProfileConfig.model_validate(profile.config), previous=[])
    except Exception as e:                            # the hand-off must not fail because the AI did
        logger.warning(f"[ghl] suggested reply failed for lead {lead.id}: {e}")
        return None
    return draft.body if draft else None


def _post(payload: dict) -> Optional[str]:
    """None on success, else a short reason. One retry for a timeout or a server error."""
    error = "unknown error"
    for _ in range(2):
        try:
            response = requests.post(settings.GHL_WEBHOOK_URL, json=payload, timeout=TIMEOUT_SECONDS)
            if response.status_code < 300:
                return None
            error = f"GoHighLevel answered {response.status_code}: {response.text[:120]}"
            if response.status_code < 500:
                break                                 # a 4xx will not get better by trying again
        except requests.RequestException as e:
            error = f"Could not reach GoHighLevel: {e}"
    return error


def push(db: Session, lead: Lead, event: str, inbound: Optional[InboundMessage] = None, force: bool = False) -> dict:
    """Returns {"status": "sent" | "failed" | "skipped" | "already_sent", "error": ...}."""
    if not configured():
        return {"status": "skipped", "error": "GHL_WEBHOOK_URL is not set in Backend/.env"}
    previous = (lead.attributes or {}).get("ghl") or {}
    if previous.get("status") == "sent" and not force:
        return {"status": "already_sent", "error": None}

    attrs = lead.attributes or {}
    if inbound is None:
        suggested = None
    elif attrs.get("suggested_reply_for") == inbound.id:        # already written when the reply came in
        suggested = attrs.get("suggested_reply")
    else:
        suggested = _suggest_reply(db, lead, inbound)
    error = _post(build_payload(lead, event, inbound, suggested))
    record = {"status": "failed" if error else "sent", "event": event, "error": error,
              "at": datetime.utcnow().isoformat() + "Z"}
    lead.attributes = {**(lead.attributes or {}), "ghl": record,
                       **({"suggested_reply": suggested, "suggested_reply_for": inbound.id} if suggested else {})}
    db.commit()
    if error:
        logger.error(f"[ghl] lead {lead.id}: {error}")
    else:
        logger.info(f"[ghl] lead {lead.id} sent ({event})")
    return {"status": record["status"], "error": error}


def push_test() -> dict:
    """A clearly marked test contact, so the GHL workflow can be built and checked before real leads arrive."""
    if not configured():
        return {"status": "skipped", "error": "GHL_WEBHOOK_URL is not set in Backend/.env"}
    sample = Lead(id=0, name="Test Business (Leadwren)", email="test@example.com", phone="+15125550100",
                  website="https://example.com", city="Austin", niche="HVAC", linkedin_name="Test Contact",
                  attributes={"state": "TX", "industry_key": "hvac", "top_gaps": [
                      {"label": "No after-hours mention (test)", "points": 17, "fix": "After-Hours Call Coverage"}]})
    error = _post(build_payload(sample, "test", None, "Thanks for your reply. (test)", is_test=True))
    return {"status": "failed" if error else "sent", "error": error}
