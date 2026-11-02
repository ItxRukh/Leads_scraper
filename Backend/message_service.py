"""
message_service.py — From a due step to a sent message
------------------------------------------------------
  1. ensure_drafts      writes the text for steps that are due (AI)
  2. approve            a person approves a draft; channels in Auto mode approve by themselves
  3. release time       every approved message gets a planned send time, spaced by the
                        channel's random gap and moved into the lead's working hours
  4. send               email is handed to Instantly (or sent by this server over SMTP)
  5. mark_sent / failed records the result and plans the lead's next step

Channels in Manual mode never get drafts: the person does the step and presses Done.
Email is the only channel: SMS and calls happen in GoHighLevel after a lead replies.
"""

import random
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

import connections
import email_sender
import email_verification
import instantly_client
import instantly_handoff
import scheduling as sch
from app_settings import get_app_settings
from database import (
    EmailLog, EmailStatus, Lead, LeadTier, OutreachMessage, OutreachQueueEntry, OutreachStepLog,
    PipelineStage,
)
from message_writer import write_message
from profile_schema import ProfileConfig
from scoring_engine import resolve_profile

DISPATCH_CHANNELS = ("email",)
LIVE = ("draft", "approved", "claimed")          # a message in one of these is still in progress
MAX_ATTEMPTS = 3
CLAIM_TIMEOUT = timedelta(minutes=10)            # an email picked up for sending must finish within this
MAX_DRAFTS_PER_TICK = 10
FOLLOWUP = "followup"      # an email written together with the first one, sent later by Instantly (kind column)
HANDED_OFF = "handed_off"  # status of a follow-up that now sits in an Instantly campaign

_EARLY_STAGES = (PipelineStage.SCRAPED, PipelineStage.LINKEDIN_ENRICHED,
                 PipelineStage.AUDITED, PipelineStage.EMAIL_VERIFIED)


def send_provider() -> str:
    """
    'ghl' (the first email is prepared here and the lead goes to GoHighLevel, which sends), 'instantly' (hand leads
    and their emails to an Instantly campaign) or 'smtp' (send from the Gmail account).
    """
    provider = get_app_settings().get("sendProvider")
    return provider if provider in ("ghl", "instantly") else "smtp"


def _not_followup():
    """Filter: the messages a person reviews and the scheduler plans (a follow-up rides along with its first email)."""
    return func.coalesce(OutreachMessage.kind, "step") != FOLLOWUP


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
        OutreachMessage.channel == channel, OutreachMessage.status.in_(statuses), _not_followup())
    if exclude_id is not None:
        q = q.filter(OutreachMessage.id != exclude_id)
    return q.scalar() or 0


def ensure_drafts(db: Session, now: Optional[datetime] = None, limit: int = MAX_DRAFTS_PER_TICK,
                  rng: Optional[random.Random] = None) -> int:
    """Write a message for every due step on channels in Assisted or Auto mode. Returns how many."""
    now = _now(now)
    if send_provider() == "ghl":
        return 0                                           # the first emails are written when an import is checked
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

            if channel == "email" and send_provider() == "instantly":
                if _draft_sequence(db, lead, entry, steps, ProfileConfig.model_validate(profile.config), rules, now, rng):
                    created += 1
                    room -= 1
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


def _draft_sequence(db: Session, lead: Lead, entry: OutreachQueueEntry, steps: list, config: ProfileConfig,
                    rules, now: datetime, rng: Optional[random.Random]) -> bool:
    """
    Instantly mode: write every remaining email of the lead's sequence at once, so a person can review the
    whole conversation and Instantly can send it. The first email is the message the scheduler plans; the
    others are 'followup' rows that ride along. Nothing is saved unless all of them could be written.
    """
    email_steps = [st for st in steps[entry.step_index or 0:] if st.channel == "email" and sch._skip_reason(st, lead) is None]
    if not email_steps:
        return False

    drafted: list[OutreachMessage] = []
    previous: list[str] = []
    for step in email_steps:
        draft = write_message(lead, step, steps, config, previous)
        if draft is None:
            logger.warning(f"No message could be written for lead {lead.id} ({step.key}); will retry")
            return False
        subject = draft.subject if not drafted else "Re: " + (drafted[0].subject or "").removeprefix("Re: ")
        drafted.append(OutreachMessage(
            lead_id=lead.id, step_key=step.key, channel="email", subject=subject, body=draft.body, status="draft",
            kind="step" if not drafted else FOLLOWUP,
            tracking_token=email_sender.new_tracking_token() if not drafted else None))
        previous.append(draft.body)

    db.add_all(drafted)
    db.flush()
    if rules.mode == "auto":
        approve_draft(db, drafted[0], "auto", now, rng)
    db.commit()
    return True


def _followups(db: Session, msg: OutreachMessage) -> list[OutreachMessage]:
    """The later emails written together with this first one, in order."""
    return db.query(OutreachMessage).filter(
        OutreachMessage.lead_id == msg.lead_id, OutreachMessage.kind == FOLLOWUP,
        OutreachMessage.status.in_(("draft", "approved")), OutreachMessage.id > msg.id).order_by(OutreachMessage.id).all()


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
    if msg.kind == FOLLOWUP:
        return                                       # sent by Instantly with its first email: no release time of its own
    if send_provider() == "ghl":
        return                                       # approved emails wait to be released to GoHighLevel
    _assign_release(db, msg, now, rng)
    for later in _followups(db, msg):                # approving the first email approves the whole sequence
        later.status, later.mode, later.approved_at, later.error = "approved", mode, now, None


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
    later = _followups(db, msg) if msg.kind != FOLLOWUP else []
    msg.status, msg.error = "discarded", reason
    for follow in later:                             # the follow-ups belong to this first email
        follow.status, follow.error = "discarded", reason
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
        .filter(OutreachMessage.status == status, _not_followup())
    if channel:
        q = q.filter(OutreachMessage.channel == channel)
    rows = q.order_by(OutreachMessage.created_at).limit(limit).all()

    # the later emails written with each first email, shown under it
    followups: dict[int, list] = {}
    for later in db.query(OutreachMessage).filter(
            OutreachMessage.kind == FOLLOWUP, OutreachMessage.status.in_((status, "approved", "draft")),
            OutreachMessage.lead_id.in_([lead.id for _, lead in rows] or [0])).order_by(OutreachMessage.id):
        followups.setdefault(later.lead_id, []).append(
            {"id": later.id, "step_key": later.step_key, "subject": later.subject, "body": later.body, "status": later.status})

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
        "industry": lead.niche, "website": lead.website,
        "weakness": (lead.attributes or {}).get("weakness"),
        "weakness_edited": bool((lead.attributes or {}).get("weakness_edited")),
        "weakness_note": (lead.attributes or {}).get("weakness_note"),
        "email_check": ((lead.attributes or {}).get("email_check") or {}).get("status"),
        "step_key": m.step_key, "step_label": step_label(lead, m.step_key), "channel": m.channel,
        "to": lead.email if m.channel == "email" else lead.phone,
        "subject": m.subject, "body": m.body, "status": m.status, "mode": m.mode,
        "release_at": m.release_at.isoformat() + "Z" if m.release_at else None,
        "followups": followups.get(lead.id, []) if (m.kind or "step") == "step" else [],
        "error": m.error, "attempts": m.attempts,
        "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
    } for m, lead in rows]


# ─── Releasing and sending ────────────────────────────────────────────────────

def release_stale_claims(db: Session, now: Optional[datetime] = None) -> int:
    """An email picked up for sending but never finished (a crash) goes back to the approved pile."""
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
        OutreachMessage.channel == channel, OutreachMessage.status == "approved", _not_followup(),
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

    db.add(EmailLog(lead_id=lead.id, subject=msg.subject, body=msg.body, sent_at=now))
    if lead.pipeline_stage in _EARLY_STAGES:
        lead.pipeline_stage = PipelineStage.EMAIL_SENT
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


def hand_off_due_emails(db: Session, now: Optional[datetime] = None) -> dict:
    """
    Instantly mode: add every approved lead whose time has come to the Instantly campaign, with all its emails.
    Returns {"sent": n, "failed": n, "held": n}; 'held' leads wait (missing postal address, address not yet checked...).
    """
    now = _now(now)
    result = {"sent": 0, "failed": 0, "held": 0}
    campaign = instantly_handoff.campaign_id()
    if not instantly_client.configured() or not campaign:
        return result
    cfg = get_app_settings()
    _, address = instantly_handoff.company()
    if not address:
        logger.warning("[instantly] hand-off paused: add the company's postal address on the Integrations page")
        return result

    for msg in ready_messages(db, "email", now):
        if (msg.kind or "step") != "step":
            continue                                     # replies are answered by a person, in GoHighLevel or Instantly
        lead = msg.lead
        if not lead.email:
            mark_failed(db, msg, "The lead has no email address", now, permanent=True)
            result["failed"] += 1
            continue

        if cfg.get("verifyBeforeSend", True) and not (lead.attributes or {}).get("email_check"):
            email_verification.verify_lead(db, lead)
        if lead.email_status == EmailStatus.INVALID:
            mark_failed(db, msg, "The email address is not valid", now, permanent=True)
            sch.schedule_entry(db, _entry_for(db, lead.id), now, recompute=True)
            result["failed"] += 1
            continue
        if lead.email_status == EmailStatus.CATCH_ALL and not cfg.get("sendToCatchAll", False):
            mark_failed(db, msg, "Catch-all address: turn on 'send to catch-all addresses' to include it", now, permanent=True)
            result["held"] += 1
            continue

        later = [f for f in _followups(db, msg) if f.status == "approved"]
        claim(db, msg, now)
        try:
            instantly_id = instantly_client.create_lead(
                campaign, lead.email, custom_variables=instantly_handoff.variables(lead, [msg, *later]),
                **instantly_handoff.lead_fields(lead))
        except instantly_client.InstantlyError as e:
            logger.error(f"Instantly hand-off for lead {lead.id} failed: {e}")
            mark_failed(db, msg, str(e), now)
            connections.record(db, "email", state="error", error=str(e), now=now)
            result["failed"] += 1
            continue

        mark_sent(db, msg, instantly_id, now)            # step 1 is done: it is Instantly's job from here
        for follow in later:
            follow.status, follow.external_id = HANDED_OFF, instantly_id
        entry = _entry_for(db, lead.id)
        if entry is not None:
            entry.sequence_status, entry.next_step_key, entry.next_channel, entry.next_due_at = sch.HANDED_OFF, None, None, None
        db.commit()
        connections.record(db, "email", state="ok", ok=True, now=now)
        result["sent"] += 1
    return result


def send_due_emails(db: Session, now: Optional[datetime] = None) -> dict:
    """Send every approved email that is due. Returns {"sent": n, "failed": n}."""
    now = _now(now)
    if send_provider() == "ghl":
        return {"sent": 0, "failed": 0}                  # GoHighLevel sends; nothing leaves from here
    if send_provider() == "instantly":
        return hand_off_due_emails(db, now)
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
