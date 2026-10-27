"""
message_service.py — From a due step to a sent message
------------------------------------------------------
  1. ensure_drafts      writes the text for steps that are due (AI)
  2. approve            a person approves a draft; channels in Auto mode approve by themselves
  3. release time       every approved message gets a planned send time, spaced by the
                        channel's random gap and moved into the lead's working hours
  4. send               email is sent by this server; WhatsApp is handed to the extension
  5. mark_sent / failed records the result and plans the lead's next step

Channels in Manual mode never get drafts: the person does the step and presses Done.
Calls are not handled here.
"""

import random
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

import connections
import email_sender
import scheduling as sch
from database import (
    EmailLog, Lead, LeadTier, OutreachMessage, OutreachQueueEntry, OutreachStepLog,
    PipelineStage, WhatsAppLog,
)
from message_writer import write_message
from phones import whatsapp_number
from profile_schema import ProfileConfig
from scoring_engine import resolve_profile

DISPATCH_CHANNELS = ("email", "whatsapp")
LIVE = ("draft", "approved", "claimed")          # a message in one of these is still in progress
MAX_ATTEMPTS = 3
CLAIM_TIMEOUT = timedelta(minutes=10)            # WhatsApp: the extension must report within this
MAX_DRAFTS_PER_TICK = 10

_EARLY_STAGES = (PipelineStage.SCRAPED, PipelineStage.LINKEDIN_ENRICHED,
                 PipelineStage.AUDITED, PipelineStage.EMAIL_VERIFIED)


def _now(now: Optional[datetime]) -> datetime:
    return now or sch.utcnow()


def _get(db: Session, message_id: int) -> OutreachMessage:
    msg = db.get(OutreachMessage, message_id)
    if msg is None:
        raise LookupError(f"Message {message_id} not found")
    return msg


def _entry_for(db: Session, lead_id: int) -> Optional[OutreachQueueEntry]:
    return db.query(OutreachQueueEntry).filter(OutreachQueueEntry.lead_id == lead_id).first()


# ─── Drafts ───────────────────────────────────────────────────────────────────

def previous_texts(db: Session, lead_id: int) -> list[str]:
    rows = db.query(OutreachMessage.body).filter(
        OutreachMessage.lead_id == lead_id, OutreachMessage.status == "sent"
    ).order_by(OutreachMessage.sent_at).all()
    return [r[0] for r in rows]


def thread_root(db: Session, lead_id: int) -> Optional[OutreachMessage]:
    """The first email we sent this lead; follow-ups are threaded under it."""
    return db.query(OutreachMessage).filter(
        OutreachMessage.lead_id == lead_id, OutreachMessage.channel == "email",
        OutreachMessage.status == "sent",
    ).order_by(OutreachMessage.sent_at).first()


def _in_progress(db: Session, channel: str, statuses: tuple, exclude_id: Optional[int] = None) -> int:
    q = db.query(func.count(OutreachMessage.id)).filter(
        OutreachMessage.channel == channel, OutreachMessage.status.in_(statuses))
    if exclude_id is not None:
        q = q.filter(OutreachMessage.id != exclude_id)
    return q.scalar() or 0


def ensure_drafts(db: Session, now: Optional[datetime] = None, limit: int = MAX_DRAFTS_PER_TICK,
                  rng: Optional[random.Random] = None) -> int:
    """Write a message for every due step on channels in Assisted or Auto mode. Returns how many."""
    now = _now(now)
    s = sch.load_settings()
    created = 0

    for channel in DISPATCH_CHANNELS:
        rules = s.rules(channel)
        if rules.mode == "manual":
            continue
        room = rules.daily_cap - sch.done_today(db, channel, now) - _in_progress(db, channel, ("approved", "claimed"))

        for entry, lead in sch.due_entries(db, now, channel):
            if created >= limit or room <= 0:
                break
            step_key = entry.next_step_key
            if db.query(OutreachMessage.id).filter(
                    OutreachMessage.lead_id == lead.id, OutreachMessage.step_key == step_key,
                    OutreachMessage.status.in_(LIVE + ("failed",))).first():
                continue                                        # already being handled, or gave up

            steps = sch.sequence_for(db, lead)
            step = next((st for st in steps if st.key == step_key), None)
            profile = resolve_profile(db, lead)
            if step is None or profile is None:
                continue

            draft = write_message(lead, step, steps, ProfileConfig.model_validate(profile.config),
                                  previous_texts(db, lead.id))
            if draft is None:
                logger.warning(f"No message could be written for lead {lead.id} ({step_key}); will retry")
                continue

            subject = draft.subject
            root = thread_root(db, lead.id) if channel == "email" else None
            if root and root.subject:
                subject = "Re: " + root.subject.removeprefix("Re: ")

            msg = OutreachMessage(lead_id=lead.id, step_key=step_key, channel=channel,
                                  subject=subject, body=draft.body, status="draft",
                                  tracking_token=email_sender.new_tracking_token() if channel == "email" else None)
            db.add(msg)
            db.flush()
            if rules.mode == "auto":
                approve_draft(db, msg, "auto", now, rng)
            created += 1
            room -= 1

    db.commit()
    if created:
        logger.info(f"Prepared {created} message(s)")
    return created


# ─── Approval and release time ────────────────────────────────────────────────

def _assign_release(db: Session, msg: OutreachMessage, now: datetime, rng: Optional[random.Random]) -> None:
    """Space this message after the last planned or sent one on its channel, inside the lead's hours."""
    rng = rng or random.Random()
    s = sch.load_settings()
    rules = s.rules(msg.channel)

    last_planned = db.query(func.max(OutreachMessage.release_at)).filter(
        OutreachMessage.channel == msg.channel, OutreachMessage.status.in_(("approved", "claimed")),
        OutreachMessage.id != msg.id).scalar()
    last_done = db.query(func.max(OutreachStepLog.at)).filter(
        OutreachStepLog.channel == msg.channel, OutreachStepLog.status == "done").scalar()
    last = max((t for t in (last_planned, last_done) if t), default=None)

    candidate = now if last is None else max(
        now, last + timedelta(seconds=rng.uniform(rules.min_gap_seconds, rules.max_gap_seconds)))
    msg.release_at = sch.next_window_start(candidate, sch.lead_tz(msg.lead, s), s)


def approve_draft(db: Session, msg: OutreachMessage, mode: str, now: datetime,
             rng: Optional[random.Random] = None) -> None:
    msg.status, msg.mode, msg.approved_at, msg.error = "approved", mode, now, None
    _assign_release(db, msg, now, rng)


def update_draft(db: Session, message_id: int, subject: Optional[str], body: str) -> OutreachMessage:
    msg = _get(db, message_id)
    if msg.status != "draft":
        raise ValueError("Only drafts can be edited")
    if not body.strip():
        raise ValueError("The message cannot be empty")
    if msg.channel == "email" and not (subject or "").strip():
        raise ValueError("An email needs a subject")
    msg.body = body.strip()
    if msg.channel == "email":
        msg.subject = subject.strip()
    db.commit()
    return msg


def approve(db: Session, message_id: int, now: Optional[datetime] = None,
            rng: Optional[random.Random] = None) -> OutreachMessage:
    msg = _get(db, message_id)
    if msg.status != "draft":
        raise ValueError("Only drafts can be approved")
    approve_draft(db, msg, "assisted", _now(now), rng)
    db.commit()
    return msg


def approve_all(db: Session, channel: Optional[str] = None, now: Optional[datetime] = None,
                rng: Optional[random.Random] = None) -> int:
    q = db.query(OutreachMessage).filter(OutreachMessage.status == "draft").order_by(OutreachMessage.id)
    if channel:
        q = q.filter(OutreachMessage.channel == channel)
    drafts = q.all()
    now = _now(now)
    for msg in drafts:
        approve_draft(db, msg, "assisted", now, rng)
        db.flush()
    db.commit()
    return len(drafts)


def discard(db: Session, message_id: int, reason: str = "Discarded by admin") -> OutreachMessage:
    """Throw the text away. If the step is still due, a new draft is written on the next round."""
    msg = _get(db, message_id)
    if msg.status not in ("draft", "approved"):
        raise ValueError("Only drafts and approved messages can be discarded")
    msg.status, msg.error = "discarded", reason
    db.commit()
    return msg


def skip(db: Session, message_id: int) -> OutreachMessage:
    """Drop this message and skip the lead's step for good."""
    msg = discard(db, message_id, "Step skipped by admin")
    if msg.kind == "reply":
        return msg
    try:
        sch.skip_step(db, msg.lead_id, note="Message rejected by admin")
    except (LookupError, ValueError):
        pass
    return msg


def list_messages(db: Session, status: str = "draft", channel: Optional[str] = None,
                  limit: int = 50) -> list[dict]:
    q = db.query(OutreachMessage, Lead).join(Lead, Lead.id == OutreachMessage.lead_id) \
        .filter(OutreachMessage.status == status)
    if channel:
        q = q.filter(OutreachMessage.channel == channel)
    rows = q.order_by(OutreachMessage.created_at).limit(limit).all()

    steps_cache: dict = {}

    def step_label(lead: Lead, key: str) -> str:
        if lead.profile_id not in steps_cache:
            steps_cache[lead.profile_id] = sch.sequence_for(db, lead)
        step = next((st for st in steps_cache[lead.profile_id] if st.key == key), None)
        return (step.label or key) if step else key

    return [{
        "kind": m.kind or "step",
        "their_message": m.inbound.body if m.kind == "reply" and m.inbound else None,
        "label": m.inbound.label if m.kind == "reply" and m.inbound else None,
        "id": m.id, "lead_id": lead.id, "lead_name": lead.name, "city": lead.city,
        "tier": lead.tier.value if lead.tier else None,
        "step_key": m.step_key, "step_label": step_label(lead, m.step_key), "channel": m.channel,
        "to": lead.email if m.channel == "email" else lead.phone,
        "subject": m.subject, "body": m.body, "status": m.status, "mode": m.mode,
        "release_at": m.release_at.isoformat() + "Z" if m.release_at else None,
        "error": m.error, "attempts": m.attempts,
        "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
    } for m, lead in rows]


# ─── Releasing and sending ────────────────────────────────────────────────────

def release_stale_claims(db: Session, now: Optional[datetime] = None) -> int:
    """A WhatsApp message handed out but never reported on goes back to the approved pile."""
    now = _now(now)
    stale = db.query(OutreachMessage).filter(
        OutreachMessage.status == "claimed", OutreachMessage.claimed_at < now - CLAIM_TIMEOUT).all()
    for msg in stale:
        msg.status, msg.release_at = "approved", now
    if stale:
        db.commit()
    return len(stale)


def _step_is_current(db: Session, msg: OutreachMessage) -> bool:
    if msg.kind == "reply":
        return not msg.lead.do_not_contact
    entry = _entry_for(db, msg.lead_id)
    return bool(entry and entry.status == "queued" and entry.sequence_status == sch.ACTIVE
                and entry.next_step_key == msg.step_key)


def ready_messages(db: Session, channel: str, now: Optional[datetime] = None,
                   limit: int = 5) -> list[OutreachMessage]:
    """Approved messages that may go out right now: released, inside the lead's hours, under the daily limit."""
    now = _now(now)
    s = sch.load_settings()
    sch.refresh_stops(db)

    room = s.rules(channel).daily_cap - sch.done_today(db, channel, now) - _in_progress(db, channel, ("claimed",))
    if room <= 0:
        return []

    ready = []
    approved = db.query(OutreachMessage).filter(
        OutreachMessage.channel == channel, OutreachMessage.status == "approved",
        OutreachMessage.release_at <= now).order_by(OutreachMessage.release_at).all()
    for msg in approved:
        if not _step_is_current(db, msg):
            msg.status, msg.error = "discarded", "The lead's step moved on or the sequence stopped"
            continue
        if not sch.in_window(now, sch.lead_tz(msg.lead, s), s):
            msg.release_at = sch.next_window_start(now, sch.lead_tz(msg.lead, s), s)
            continue
        ready.append(msg)
        if len(ready) >= min(limit, room):
            break
    db.commit()
    return ready


def claim(db: Session, msg: OutreachMessage, now: Optional[datetime] = None) -> None:
    msg.status, msg.claimed_at = "claimed", _now(now)
    db.commit()


def mark_sent(db: Session, msg: OutreachMessage, external_id: Optional[str] = None,
              now: Optional[datetime] = None) -> None:
    now = _now(now)
    lead = msg.lead
    msg.status, msg.sent_at, msg.external_id, msg.error = "sent", now, external_id, None

    if msg.channel == "email":
        db.add(EmailLog(lead_id=lead.id, subject=msg.subject, body=msg.body, sent_at=now))
    else:
        db.add(WhatsAppLog(lead_id=lead.id, message_content=msg.body, delivery_status="sent", sent_at=now))
    if lead.pipeline_stage in _EARLY_STAGES:
        lead.pipeline_stage = PipelineStage.EMAIL_SENT if msg.channel == "email" else PipelineStage.WHATSAPP_SENT
    db.commit()

    if msg.kind != "reply":
        sch.complete_step(db, lead.id, mode=msg.mode or "manual", now=now)


def mark_failed(db: Session, msg: OutreachMessage, error: str, now: Optional[datetime] = None,
                permanent: bool = False) -> None:
    now = _now(now)
    msg.attempts = (msg.attempts or 0) + 1
    msg.error = error[:300]
    if permanent or msg.attempts >= MAX_ATTEMPTS:
        msg.status = "failed"
    else:
        msg.status, msg.release_at = "approved", now + timedelta(minutes=5 * msg.attempts)
    db.commit()


def send_due_emails(db: Session, now: Optional[datetime] = None) -> dict:
    """Send every approved email that is due. Returns {"sent": n, "failed": n}."""
    now = _now(now)
    result = {"sent": 0, "failed": 0}
    if not email_sender.is_configured():
        return result

    for msg in ready_messages(db, "email", now):
        lead = msg.lead
        if not lead.email:
            mark_failed(db, msg, "The lead has no email address", now, permanent=True)
            result["failed"] += 1
            continue
        claim(db, msg, now)
        root = thread_root(db, lead.id)
        thread = (msg.inbound.message_key if msg.kind == "reply" and msg.inbound else None) \
            or (root.external_id if root else None)
        try:
            message_id = email_sender.send_email(lead.email, msg.subject or "", msg.body,
                                                 thread, msg.tracking_token)
        except Exception as e:
            logger.error(f"Email to lead {lead.id} failed: {e}")
            mark_failed(db, msg, str(e), now)
            connections.record(db, "email", state="error", error=str(e), now=now)
            result["failed"] += 1
            continue
        mark_sent(db, msg, message_id, now)
        connections.record(db, "email", state="ok", ok=True, now=now)
        result["sent"] += 1
    return result


def claim_whatsapp(db: Session, now: Optional[datetime] = None, limit: int = 1) -> list[dict]:
    """Hand the extension the WhatsApp messages that may go out now."""
    now = _now(now)
    out = []
    for msg in ready_messages(db, "whatsapp", now, limit):
        number = whatsapp_number(msg.lead.phone, msg.lead.country)
        if not number:
            mark_failed(db, msg, "No usable international phone number", now, permanent=True)
            try:
                sch.skip_step(db, msg.lead_id, note="No usable international phone number", now=now)
            except (LookupError, ValueError):
                pass
            continue
        claim(db, msg, now)
        out.append({"message_id": msg.id, "lead_id": msg.lead_id, "lead_name": msg.lead.name,
                    "phone": number, "text": msg.body})
    return out
