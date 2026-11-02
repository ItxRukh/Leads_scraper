"""
email_verification.py — Is this address safe to send to?
---------------------------------------------------------
Two layers, cheapest first:
  1. Free check (no key): syntax, throwaway domains, and whether the domain can receive mail (MX record).
  2. Paid check, only when a key is configured: MILLIONVERIFIER_API_KEY, else ZEROBOUNCE_API_KEY. It asks the mail
     server about the individual mailbox, and spots catch-all servers.
The paid check is only run on leads worth contacting (Hot and Warm), so the cost stays small.

Result of verify_address(): {"status": "valid" | "invalid" | "catch-all" | "unknown", "provider": ..., "detail": ...}

A free-only "valid" means "the domain can receive mail", not "this mailbox exists": some bounces are still
possible, which is why the daily limits stay low. When the paid service fails (bad key, no credits, timeout),
the free result is kept and the reason is written in `detail`; an address is never condemned because of a service error.
"""

from datetime import datetime
from typing import Optional

import requests
from loguru import logger
from sqlalchemy.orm import Session

from config import settings
from database import EmailStatus, Lead, LeadTier

TIMEOUT_SECONDS = 15

# MillionVerifier "result" -> our status. Anything it adds later is read as "unknown", never as "valid".
_MILLIONVERIFIER = {"ok": "valid", "catch_all": "catch-all", "invalid": "invalid", "disposable": "invalid"}
_STATUS_TO_ENUM = {"valid": EmailStatus.VALID, "invalid": EmailStatus.INVALID,
                   "catch-all": EmailStatus.CATCH_ALL, "unknown": EmailStatus.UNKNOWN}


def paid_provider() -> Optional[str]:
    if settings.MILLIONVERIFIER_API_KEY:
        return "millionverifier"
    if settings.ZEROBOUNCE_API_KEY:
        return "zerobounce"
    return None


def _millionverifier(email: str) -> dict:
    response = requests.get(f"{settings.MILLIONVERIFIER_BASE_URL.rstrip('/')}/",
                            params={"api": settings.MILLIONVERIFIER_API_KEY, "email": email, "timeout": 10},
                            timeout=TIMEOUT_SECONDS)
    data = response.json()
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:120])
    result = str(data.get("result", "")).lower()
    return {"status": _MILLIONVERIFIER.get(result, "unknown"), "provider": "millionverifier",
            "detail": f"{result}{'/' + data['subresult'] if data.get('subresult') else ''}"}


def verify_address(email: str) -> dict:
    from scripts.email_verify import verify_email_local, verify_email_zerobounce

    free = verify_email_local(email)
    base = {"status": free.get("status", "unknown"), "provider": "free", "detail": free.get("sub_status", "")}
    if base["status"] != "valid":
        return base                                   # bad syntax, throwaway or dead domain: no need to pay

    provider = paid_provider()
    if provider is None:
        return base
    try:
        if provider == "millionverifier":
            return _millionverifier(email)
        paid = verify_email_zerobounce(email)
        return {"status": paid.get("status", "unknown"), "provider": "zerobounce", "detail": paid.get("sub_status", "")}
    except Exception as e:                            # a service problem is not a verdict on the address
        logger.warning(f"{provider} check failed for {email}: {e}")
        return {**base, "detail": f"{base['detail']}; paid check failed: {e}"[:200]}


def apply_to_lead(db: Session, lead: Lead, result: dict) -> None:
    """Store a verdict on the lead and, for a verified address, score the lead again (a verified email earns points)."""
    lead.email_status = _STATUS_TO_ENUM.get(result["status"], EmailStatus.UNKNOWN)
    lead.email_verified = result["status"] == "valid"
    lead.attributes = {**(lead.attributes or {}), "email_check": {
        "status": result["status"], "provider": result["provider"], "detail": result.get("detail", ""),
        "at": datetime.utcnow().isoformat()}}
    db.commit()

    if lead.audited_at is not None:
        from queue_service import enqueue_if_eligible
        from scoring_engine import apply_result, score_lead
        apply_result(lead, score_lead(db, lead))
        db.commit()
        enqueue_if_eligible(db, lead)


def verify_lead(db: Session, lead: Lead) -> dict:
    """Verify one lead's address and store the result. Returns the verdict."""
    result = verify_address(lead.email)
    apply_to_lead(db, lead, result)
    return result


def verify_batch(batch_id: int, db: Optional[Session] = None, tiers: Optional[tuple] = None) -> dict:
    """
    Verify every not-yet-checked address in an import (every lead, or only the given tiers). Runs in the background.
    """
    from database import SyncSessionLocal

    own_session = db is None
    db = db or SyncSessionLocal()
    counts = {"checked": 0, "valid": 0, "invalid": 0, "catch-all": 0, "unknown": 0}
    try:
        leads = db.query(Lead).filter(Lead.import_batch_id == batch_id, Lead.email.isnot(None), Lead.email != "",
                                      Lead.do_not_contact == False)  # noqa: E712
        if tiers is not None:
            leads = leads.filter(Lead.tier.in_(tiers))
        for lead in leads.all():
            if (lead.attributes or {}).get("email_check"):
                continue                              # already checked
            try:
                counts[verify_lead(db, lead)["status"]] += 1
                counts["checked"] += 1
            except Exception as e:
                logger.error(f"[verify] lead {lead.id} failed: {e}")
                db.rollback()
        logger.info(f"[verify] batch {batch_id}: {counts}")
    finally:
        if own_session:
            db.close()
    return counts


def batch_status(db: Session, batch_id: int, tiers: Optional[tuple] = None) -> dict:
    """Progress and results of the email check for the leads of an import."""
    q = db.query(Lead).filter(Lead.import_batch_id == batch_id)
    leads = (q.filter(Lead.tier.in_(tiers)) if tiers is not None else q).all()
    with_email = [lead for lead in leads if lead.email]
    checks = [(lead.attributes or {}).get("email_check") for lead in with_email]
    done = [c for c in checks if c]
    return {"to_check": len(with_email), "checked": len(done),
            "valid": sum(c["status"] == "valid" for c in done), "invalid": sum(c["status"] == "invalid" for c in done),
            "catch_all": sum(c["status"] == "catch-all" for c in done), "unknown": sum(c["status"] == "unknown" for c in done),
            "no_email": len(leads) - len(with_email), "provider": paid_provider() or "free"}
