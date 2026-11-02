"""
dashboard_api.py — The numbers for the Dashboard page
------------------------------------------------------
    GET /dashboard/summary

Everything the app itself knows (imports, checks, weaknesses, emails written and approved, do-not-contact), plus what
GoHighLevel reported back (kept on each lead under `ghl_contact` and `ghl_flags` by the GoHighLevel sync).
"""

from collections import Counter
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from database import DNCList, ImportBatch, InboundMessage, Lead, OutreachMessage, get_db
from industries import industry_label

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

APPROVED_STATES = ("approved", "claimed", "sent", "handed_off")
DAYS = 14


def ghl_configured() -> bool:
    return bool(getattr(settings, "GHL_API_TOKEN", "") and getattr(settings, "GHL_LOCATION_ID", ""))


def _email_state(lead: Lead) -> str:
    """valid | catch_all | unknown | invalid | not_checked | no_email"""
    if not lead.email:
        return "no_email"
    status = ((lead.attributes or {}).get("email_check") or {}).get("status")
    return {"valid": "valid", "catch-all": "catch_all", "unknown": "unknown", "invalid": "invalid"}.get(status, "not_checked")


def _dnc_group(reason: str) -> str:
    text = (reason or "").lower()
    if "ghl" in text or "gohighlevel" in text:
        return "Blocked or unsubscribed in GoHighLevel"
    if "opt_out" in text or "stop" in text or "unsubscribe" in text:
        return "Asked to stop or unsubscribed"
    if "admin" in text or "manual" in text or not text:
        return "Added by you"
    return "Other"


def _todo(leads: list, drafts: int, no_weakness: int) -> list[dict]:
    live = [lead for lead in leads if not lead.do_not_contact]
    to_check = sum(1 for lead in live if lead.email and (
        (lead.website and not lead.audited_at) or _email_state(lead) == "not_checked"))
    items = []
    if drafts:
        items.append({"key": "drafts", "tone": "warn", "to": "/admin/approve",
                      "text": f"{drafts} first email{'' if drafts == 1 else 's'} waiting for your approval"})
    if to_check:
        items.append({"key": "check", "tone": "info", "to": "/admin/import",
                      "text": f"{to_check} lead{'' if to_check == 1 else 's'} still to check (website or email)"})
    if no_weakness > 0:
        items.append({"key": "weakness", "tone": "info", "to": "/admin/leads",
                      "text": f"{no_weakness} lead{'' if no_weakness == 1 else 's'} without a weakness: their email has a general opening"})
    if not ghl_configured():
        items.append({"key": "ghl", "tone": "bad", "to": None,
                      "text": "GoHighLevel is not connected: add GHL_API_TOKEN and GHL_LOCATION_ID to Backend/.env"})
    return items


def _attr(lead: Lead, *path):
    value = lead.attributes or {}
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return value


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    leads = db.query(Lead).all()
    total = len(leads)
    ids = {lead.id for lead in leads}

    # emails per lead: the furthest state each lead has reached
    written, approved = set(), set()
    for lead_id, status in db.query(OutreachMessage.lead_id, OutreachMessage.status).filter(
            OutreachMessage.kind != "reply", OutreachMessage.status != "discarded"):
        written.add(lead_id)
        if status in APPROVED_STATES:
            approved.add(lead_id)
    drafts = db.query(func.count(OutreachMessage.id)).filter(
        OutreachMessage.status == "draft", OutreachMessage.kind != "reply").scalar() or 0
    replied = {lead_id for (lead_id,) in db.query(InboundMessage.lead_id).filter(
        InboundMessage.kind == "reply", InboundMessage.lead_id.isnot(None))}

    in_ghl = {lead.id for lead in leads if _attr(lead, "ghl_contact", "status") == "sent"}
    flags = {name: {lead.id for lead in leads if _attr(lead, "ghl_flags", name)}
             for name in ("sent", "opened", "replied", "bounced", "unsubscribed")}
    replied |= flags["replied"]

    states = Counter(_email_state(lead) for lead in leads)
    email_ok = sum(states[k] for k in ("valid", "catch_all", "unknown"))
    with_weakness = sum(1 for lead in leads if _attr(lead, "weakness"))
    edited = sum(1 for lead in leads if _attr(lead, "weakness_edited"))
    blocked = sum(1 for lead in leads if lead.do_not_contact)

    funnel = [
        {"key": "imported", "label": "Imported", "count": total},
        {"key": "website", "label": "Website checked", "count": sum(1 for lead in leads if lead.audited_at)},
        {"key": "email", "label": "Email address OK", "count": email_ok},
        {"key": "written", "label": "First email written", "count": len(written & ids)},
        {"key": "approved", "label": "Approved", "count": len(approved & ids)},
        {"key": "ghl", "label": "In GoHighLevel", "count": len(in_ghl)},
        {"key": "replied", "label": "Replied", "count": len(replied & ids)},
    ]

    by_industry: dict[str, dict] = {}
    for lead in leads:
        key = _attr(lead, "industry_key") or lead.niche or "Other"
        row = by_industry.setdefault(key, {"industry": industry_label(key) or key, "leads": 0, "email_ok": 0,
                                           "drafts": 0, "approved": 0, "in_ghl": 0})
        row["leads"] += 1
        row["email_ok"] += _email_state(lead) in ("valid", "catch_all", "unknown")
        row["drafts"] += lead.id in written and lead.id not in approved
        row["approved"] += lead.id in approved
        row["in_ghl"] += lead.id in in_ghl

    batches = []
    for batch in db.query(ImportBatch).order_by(ImportBatch.id.desc()).limit(8):
        members = [lead for lead in leads if lead.import_batch_id == batch.id]
        batches.append({"id": batch.id, "name": batch.name,
                        "at": batch.created_at.isoformat() + "Z" if batch.created_at else None,
                        "leads": len(members),
                        "checked": sum(1 for lead in members if lead.audited_at or not lead.website),
                        "written": sum(1 for lead in members if lead.id in written),
                        "approved": sum(1 for lead in members if lead.id in approved),
                        "skipped": (batch.duplicates or 0) + (batch.on_dnc or 0) + (batch.invalid or 0)})

    since = datetime.utcnow().date() - timedelta(days=DAYS - 1)
    per_day = Counter(lead.scraped_at.date() for lead in leads if lead.scraped_at and lead.scraped_at.date() >= since)
    daily = [{"date": (since + timedelta(days=i)).isoformat(), "leads": per_day.get(since + timedelta(days=i), 0)}
             for i in range(DAYS)]

    dnc_rows = db.query(DNCList).order_by(DNCList.id.desc()).all()
    dnc_groups = Counter(_dnc_group(row.reason) for row in dnc_rows)
    recent_dnc = [{"who": row.email or row.phone or row.domain, "why": _dnc_group(row.reason),
                   "at": row.added_at.isoformat() + "Z" if row.added_at else None} for row in dnc_rows[:6]]

    return {
        "todo": _todo(leads, drafts, total - with_weakness - blocked),
        "totals": {"leads": total, "imports": db.query(func.count(ImportBatch.id)).scalar() or 0,
                   "drafts": drafts, "approved": len(approved & ids), "do_not_contact": len(dnc_rows)},
        "funnel": funnel,
        "email_quality": {key: states.get(key, 0) for key in ("valid", "catch_all", "unknown", "invalid", "not_checked", "no_email")},
        "weakness": {"with_weakness": with_weakness, "without": total - with_weakness, "edited": edited},
        "by_industry": sorted(by_industry.values(), key=lambda r: -r["leads"]),
        "by_import": batches,
        "daily_imports": daily,
        "dnc": {"total": len(dnc_rows), "groups": [{"label": k, "count": v} for k, v in dnc_groups.most_common()], "recent": recent_dnc},
        "ghl": {"configured": ghl_configured(), "in_ghl": len(in_ghl),
                **{name: len(members & ids) for name, members in flags.items()}},
    }
