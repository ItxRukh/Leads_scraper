"""
queue_service.py — The Outreach Queue
-------------------------------------
Leads that score Hot (and Warm, unless Settings says Hot only) are placed in
the queue automatically after they are scored. The queue only records who is
waiting; sending and scheduling are separate steps.

A lead that someone removed by hand is never re-added automatically.
"""

from typing import Optional

from loguru import logger
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app_settings import get_app_settings
from database import IndustryProfile, InboundMessage, Lead, LeadTier, OutreachQueueEntry, PipelineStage
from geo import ensure_geo
from profile_schema import ProfileConfig
from scheduling import (
    ACTIVE, load_settings, refresh_stops, schedule_entry, schedule_info, sequence_for, utcnow,
)

QUEUED, PAUSED, REMOVED = "queued", "paused", "removed"
STATUSES = (QUEUED, PAUSED, REMOVED)

_BLOCKED_STAGES = (PipelineStage.REJECTED, PipelineStage.DNC, PipelineStage.ONBOARDED)


def _qualifying_tiers() -> tuple[LeadTier, ...]:
    if get_app_settings().get("queueMinTier") == "hot":
        return (LeadTier.HOT,)
    return (LeadTier.HOT, LeadTier.WARM)


def _can_contact(lead: Lead) -> bool:
    return bool(lead.email or lead.phone) and not lead.do_not_contact \
        and lead.pipeline_stage not in _BLOCKED_STAGES


def enqueue_if_eligible(db: Session, lead: Lead) -> Optional[OutreachQueueEntry]:
    """Add the lead to the queue if it qualifies and is not already there."""
    if lead.tier not in _qualifying_tiers() or not _can_contact(lead):
        return None
    if db.query(OutreachQueueEntry.id).filter(OutreachQueueEntry.lead_id == lead.id).first():
        return None  # already queued, paused, or removed on purpose

    ensure_geo(lead)
    entry = OutreachQueueEntry(
        lead_id=lead.id, profile_id=lead.profile_id, status=QUEUED,
        tier=lead.tier.value, score=lead.final_score,
    )
    db.add(entry)
    db.commit()
    schedule_entry(db, entry)
    logger.info(f"Queued lead {lead.id} ({lead.name}) — {entry.tier}, score {entry.score}")
    return entry


def sync_queue(db: Session, profile_id: Optional[int] = None) -> int:
    """Queue every qualifying lead that is not queued yet. Returns how many were added."""
    q = db.query(Lead).filter(
        Lead.tier.in_(_qualifying_tiers()),
        ~db.query(OutreachQueueEntry.id).filter(OutreachQueueEntry.lead_id == Lead.id).exists(),
    )
    if profile_id is not None:
        q = q.filter(Lead.profile_id == profile_id)
    return sum(1 for lead in q.all() if enqueue_if_eligible(db, lead))


def add_manually(db: Session, lead_id: int) -> OutreachQueueEntry:
    """Put any lead (even Cold) in the queue, or bring back a removed one."""
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise LookupError(f"Lead {lead_id} not found")
    if not _can_contact(lead):
        raise ValueError("This lead has no email or phone, or is marked do-not-contact")

    entry = db.query(OutreachQueueEntry).filter(OutreachQueueEntry.lead_id == lead_id).first()
    if entry is None:
        ensure_geo(lead)
        entry = OutreachQueueEntry(
            lead_id=lead.id, profile_id=lead.profile_id,
            tier=lead.tier.value if lead.tier else None, score=lead.final_score,
        )
        db.add(entry)
    entry.status, entry.note = QUEUED, "Added manually"
    entry.sequence_status, entry.stop_reason = ACTIVE, None
    db.commit()
    schedule_entry(db, entry, recompute=True)
    db.refresh(entry)
    return entry


def set_status(db: Session, lead_id: int, status: str, note: Optional[str] = None) -> OutreachQueueEntry:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    entry = db.query(OutreachQueueEntry).filter(OutreachQueueEntry.lead_id == lead_id).first()
    if entry is None:
        raise LookupError(f"Lead {lead_id} is not in the queue")
    entry.status = status
    if note is not None:
        entry.note = note
    db.commit()
    if status == QUEUED:
        schedule_entry(db, entry, recompute=True)   # resuming: plan from now, inside working hours
    db.refresh(entry)
    return entry


def list_queue(db: Session, status: str = QUEUED, tier: Optional[str] = None,
               profile_id: Optional[int] = None, search: Optional[str] = None,
               due: bool = False, limit: int = 50, offset: int = 0) -> dict:
    refresh_stops(db)   # leads that replied or opted out since the last look
    now = utcnow()
    settings = load_settings()

    base = db.query(OutreachQueueEntry, Lead, IndustryProfile.name).join(
        Lead, Lead.id == OutreachQueueEntry.lead_id
    ).outerjoin(IndustryProfile, IndustryProfile.id == OutreachQueueEntry.profile_id)

    counts = dict(
        db.query(OutreachQueueEntry.status, func.count(OutreachQueueEntry.id))
        .group_by(OutreachQueueEntry.status).all()
    )
    due_now = db.query(func.count(OutreachQueueEntry.id)).filter(
        OutreachQueueEntry.status == QUEUED, OutreachQueueEntry.sequence_status == ACTIVE,
        OutreachQueueEntry.next_due_at <= now,
    ).scalar() or 0

    q = base.filter(OutreachQueueEntry.status == status)
    if tier:
        q = q.filter(OutreachQueueEntry.tier == tier)
    if profile_id is not None:
        q = q.filter(OutreachQueueEntry.profile_id == profile_id)
    if due:
        q = q.filter(OutreachQueueEntry.sequence_status == ACTIVE, OutreachQueueEntry.next_due_at <= now)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(or_(Lead.name.ilike(like), Lead.email.ilike(like),
                         Lead.phone.ilike(like), Lead.city.ilike(like)))

    total = q.count()
    rows = q.order_by(OutreachQueueEntry.score.desc(), OutreachQueueEntry.queued_at)             .offset(offset).limit(limit).all()

    steps_cache: dict = {}
    items = []
    for entry, lead, profile_name in rows:
        if lead.profile_id not in steps_cache:
            steps_cache[lead.profile_id] = sequence_for(db, lead)
        reply = db.query(InboundMessage).filter(
            InboundMessage.lead_id == lead.id, InboundMessage.kind == "reply"
        ).order_by(InboundMessage.id.desc()).first()
        items.append({
            "reply_label": reply.label if reply else None,
            "reply_summary": reply.summary if reply else None,
            "lead_id": lead.id, "name": lead.name, "niche": lead.niche,
            "city": lead.city, "country": lead.country, "timezone": lead.timezone,
            "email": lead.email, "phone": lead.phone,
            "tier": entry.tier, "score": entry.score, "status": entry.status,
            "profile_id": entry.profile_id, "profile_name": profile_name,
            "pipeline_stage": lead.pipeline_stage.value if lead.pipeline_stage else None,
            "queued_at": entry.queued_at.isoformat() if entry.queued_at else None,
            "note": entry.note,
            **schedule_info(entry, lead, settings, steps_cache[lead.profile_id], now),
        })

    return {
        "total": total,
        "counts": {st: counts.get(st, 0) for st in STATUSES},
        "due_now": due_now,
        "items": items,
    }
