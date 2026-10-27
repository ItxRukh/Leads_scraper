"""
dashboard_api.py — The few numbers that matter, for the Dashboard page
-----------------------------------------------------------------------
    GET /dashboard/summary

Counts follow a lead through the pipeline:
  imported -> audited -> email checked -> emailed -> replied -> interested -> sent to GoHighLevel
plus the do-not-contact list, bounces and a per-industry breakdown.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app_settings import get_app_settings
from database import (EmailLog, InboundMessage, Lead, LeadTier, OutreachMessage, OutreachQueueEntry, OutreachStepLog,
                      get_db)
from industries import industry_label
from integrations_api import _blockers

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


def _leads_with(db: Session, label: str) -> int:
    return db.query(func.count(distinct(InboundMessage.lead_id))).filter(
        InboundMessage.kind == "reply", InboundMessage.label == label, InboundMessage.lead_id.isnot(None)).scalar() or 0


def _industry_of(lead: Lead) -> str:
    attrs = lead.attributes or {}
    key = attrs.get("industry_key") or lead.niche
    return (industry_label(key) if key else None) or lead.niche or "Other"


def _todo(db: Session, leads: list) -> list[dict]:
    """What needs a person or a button press now, most urgent first. Each item links to the page that does it."""
    live = [lead for lead in leads if not lead.do_not_contact]
    to_answer = sum(1 for lead in live if (lead.attributes or {}).get("suggested_reply")
                    and ((lead.attributes or {}).get("reply_sent") or {}).get("for")
                    != ((lead.attributes or {}).get("reply_context") or {}).get("inbound_id"))
    drafts = db.query(func.count(OutreachMessage.id)).filter(OutreachMessage.status == "draft", OutreachMessage.kind != "reply").scalar() or 0
    to_audit = sum(1 for lead in live if lead.website and not lead.audited_at)
    to_check = sum(1 for lead in live if lead.audited_at and lead.email and lead.tier in (LeadTier.HOT, LeadTier.WARM)
                   and not (lead.attributes or {}).get("email_check"))

    items = []
    for problem in _blockers(get_app_settings()):
        items.append({"key": "setup", "tone": "bad", "text": problem, "to": "/admin/integrations"})
    if to_answer:
        items.append({"key": "answer", "tone": "ok", "text": f"{to_answer} repl{'y' if to_answer == 1 else 'ies'} to answer", "to": "/admin/leads"})
    if drafts:
        items.append({"key": "drafts", "tone": "warn", "text": f"{drafts} draft{'' if drafts == 1 else 's'} waiting for your approval", "to": "/admin/queue"})
    if to_audit:
        items.append({"key": "audit", "tone": "info", "text": f"{to_audit} lead{'' if to_audit == 1 else 's'} waiting for the website check", "to": "/admin/import"})
    if to_check:
        items.append({"key": "verify", "tone": "info", "text": f"{to_check} email address{'' if to_check == 1 else 'es'} still to check", "to": "/admin/import"})
    return items


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    leads = db.query(Lead).all()
    total = len(leads)
    tiers = {t.value: sum(1 for lead in leads if lead.tier == t) for t in (LeadTier.HOT, LeadTier.WARM, LeadTier.COLD)}
    ghl_sent = sum(1 for lead in leads if ((lead.attributes or {}).get("ghl") or {}).get("status") == "sent")
    day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)

    emailed_ids = {lead_id for (lead_id,) in db.query(distinct(EmailLog.lead_id)).filter(EmailLog.sent_at.isnot(None))}
    replied_ids = {lead_id for (lead_id,) in db.query(distinct(InboundMessage.lead_id)).filter(
        InboundMessage.kind == "reply", InboundMessage.lead_id.isnot(None))}

    funnel = [
        {"key": "imported", "label": "Leads", "count": total},
        {"key": "audited", "label": "Website audited", "count": sum(1 for lead in leads if lead.audited_at)},
        {"key": "checked", "label": "Email checked", "count": sum(1 for lead in leads if (lead.attributes or {}).get("email_check"))},
        {"key": "emailed", "label": "Emailed", "count": len(emailed_ids)},
        {"key": "replied", "label": "Replied", "count": len(replied_ids)},
        {"key": "interested", "label": "Interested", "count": _leads_with(db, "interested")},
        {"key": "ghl", "label": "Sent to GoHighLevel", "count": ghl_sent},
    ]

    by_industry: dict[str, dict] = {}
    for lead in leads:
        key = (lead.attributes or {}).get("industry_key") or (lead.niche or "Other")
        row = by_industry.setdefault(key, {"industry": industry_label(key) or key, "leads": 0, "hot": 0, "emailed": 0, "replied": 0})
        row["leads"] += 1
        row["hot"] += lead.tier == LeadTier.HOT
        row["emailed"] += lead.id in emailed_ids
        row["replied"] += lead.id in replied_ids

    recent = db.query(InboundMessage, Lead).join(Lead, Lead.id == InboundMessage.lead_id).filter(
        InboundMessage.kind == "reply").order_by(InboundMessage.id.desc()).limit(8).all()

    return {
        "todo": _todo(db, leads),
        "tiers": tiers,
        "funnel": funnel,
        "today": {"emails": db.query(func.count(OutreachStepLog.id)).filter(
            OutreachStepLog.channel == "email", OutreachStepLog.status == "done", OutreachStepLog.at >= day_start).scalar() or 0},
        "waiting_in_instantly": db.query(func.count(OutreachQueueEntry.id)).filter(
            OutreachQueueEntry.sequence_status == "handed_off").scalar() or 0,
        "do_not_contact": sum(1 for lead in leads if lead.do_not_contact),
        "bounced": db.query(func.count(InboundMessage.id)).filter(InboundMessage.kind == "bounce").scalar() or 0,
        "by_industry": sorted(by_industry.values(), key=lambda r: -r["leads"]),
        "recent_replies": [{"lead_id": lead.id, "lead_name": lead.name, "industry": _industry_of(lead), "city": lead.city, "label": m.label, "summary": m.summary,
                            "text": (m.body or "")[:160], "at": m.received_at.isoformat() + "Z" if m.received_at else None,
                            "ghl": ((lead.attributes or {}).get("ghl") or {}).get("status")} for m, lead in recent],
    }
