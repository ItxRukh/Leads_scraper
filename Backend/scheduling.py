"""
scheduling.py — When each lead is contacted next
------------------------------------------------
Every queued lead follows its profile's sequence (email, then follow-up
email, WhatsApp, call…). Each lead has its own clock:

  * a step's due time is counted from when the previous step was actually
    done for that lead, not from when leads were collected;
  * the due time is moved into the lead's own working hours (its time zone);
  * sending rules shared by all profiles (hours, gaps, daily caps, and whether
    a channel is manual / assisted / automatic) live in the app settings.

Nothing here sends a message. It decides what is due and records what was done.
All stored times are naive UTC, like the rest of the database.
"""

import random
from datetime import datetime, time, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy.orm import Session

from app_settings import get_app_settings, save_app_settings
from database import (
    EmailStatus, IndustryProfile, Lead, OutreachQueueEntry, OutreachStepLog, PipelineStage,
)
from profile_schema import CHANNELS, ProfileConfig, SequenceStep
from scoring_engine import resolve_profile

UTC = timezone.utc
TIER_RANK = {"cold": 0, "warm": 1, "hot": 2}

ACTIVE, COMPLETED, STOPPED = "active", "completed", "stopped"
HANDED_OFF = "handed_off"          # the lead and its emails are in an Instantly campaign; Instantly sends them


# ─── Sending rules (shared by all profiles) ───────────────────────────────────

class ChannelRules(BaseModel):
    mode: Literal["manual", "assisted", "auto"] = "assisted"
    min_gap_seconds: int = Field(default=90, ge=0, le=86400)
    max_gap_seconds: int = Field(default=240, ge=0, le=86400)
    daily_cap: int = Field(default=40, ge=0, le=5000)

    @model_validator(mode="after")
    def _gap_order(self):
        if self.min_gap_seconds > self.max_gap_seconds:
            raise ValueError("minimum gap cannot be longer than the maximum gap")
        return self


class ScheduleSettings(BaseModel):
    fallback_timezone: str = "America/New_York"   # used when a lead's own zone is unknown
    work_start: str = "09:00"
    work_end: str = "17:00"
    work_days: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])   # Monday = 0
    auto_reply: bool = False          # on: AI replies on an Auto channel go out without approval
    revisit_days: int = Field(default=45, ge=1, le=365)   # a "not now" lead is contacted again after this
    email: ChannelRules = Field(default_factory=lambda: ChannelRules(
        mode="assisted", min_gap_seconds=90, max_gap_seconds=240, daily_cap=40))
    whatsapp: ChannelRules = Field(default_factory=lambda: ChannelRules(
        mode="assisted", min_gap_seconds=60, max_gap_seconds=300, daily_cap=25))
    call: ChannelRules = Field(default_factory=lambda: ChannelRules(
        mode="manual", min_gap_seconds=120, max_gap_seconds=600, daily_cap=20))

    @field_validator("work_start", "work_end")
    @classmethod
    def _clock(cls, v):
        try:
            h, m = v.split(":")
            time(int(h), int(m))
        except ValueError:
            raise ValueError("use 24-hour HH:MM, e.g. 09:00")
        return v

    @field_validator("work_days")
    @classmethod
    def _days(cls, v):
        if not v or any(d not in range(7) for d in v):
            raise ValueError("choose at least one day (0 = Monday … 6 = Sunday)")
        return sorted(set(v))

    @field_validator("fallback_timezone")
    @classmethod
    def _zone(cls, v):
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone '{v}' (use a name like America/New_York)")
        return v

    @model_validator(mode="after")
    def _hours(self):
        if _hm(self.work_start) >= _hm(self.work_end):
            raise ValueError("working hours must end after they start")
        return self

    def rules(self, channel: str) -> ChannelRules:
        return getattr(self, channel)


def _hm(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


def load_settings() -> ScheduleSettings:
    raw = get_app_settings().get("schedule") or {}
    try:
        return ScheduleSettings.model_validate(raw)
    except ValidationError as e:
        logger.warning(f"Saved schedule settings are invalid, using defaults: {e}")
        return ScheduleSettings()


def save_settings(settings: ScheduleSettings) -> ScheduleSettings:
    save_app_settings({"schedule": settings.model_dump()})
    return settings


# ─── Time helpers ─────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.utcnow()


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC)


def _naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None)


def lead_tz(lead: Lead, s: ScheduleSettings) -> ZoneInfo:
    for name in (lead.timezone, s.fallback_timezone):
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            continue
    return ZoneInfo("UTC")


def in_window(now: datetime, tz: ZoneInfo, s: ScheduleSettings) -> bool:
    """Is this moment (naive UTC) inside the working hours of a lead in `tz`?"""
    local = _aware(now).astimezone(tz)
    return local.weekday() in s.work_days and _hm(s.work_start) <= local.time() < _hm(s.work_end)


def next_window_start(after: datetime, tz: ZoneInfo, s: ScheduleSettings) -> datetime:
    """The first moment at or after `after` (naive UTC) that is inside working hours."""
    local = _aware(after).astimezone(tz)
    start, end = _hm(s.work_start), _hm(s.work_end)
    for i in range(15):
        day = local.date() + timedelta(days=i)
        if day.weekday() not in s.work_days:
            continue
        opens = datetime.combine(day, start, tzinfo=tz)
        closes = datetime.combine(day, end, tzinfo=tz)
        candidate = max(local, opens)
        if candidate < closes:
            return _naive(candidate)
    return after  # unreachable with at least one working day; never block a lead


# ─── Sequence and stop rules ──────────────────────────────────────────────────

def sequence_for(db: Session, lead: Lead) -> list[SequenceStep]:
    profile = resolve_profile(db, lead)
    config = ProfileConfig.model_validate(profile.config) if profile else ProfileConfig()
    return config.sequence.steps


def _skip_reason(step: SequenceStep, lead: Lead) -> Optional[str]:
    if step.channel != "email":
        return "SMS, WhatsApp and calls are handled in GoHighLevel after a reply"
    tier = lead.tier.value if lead.tier else "cold"
    if TIER_RANK.get(tier, 0) < TIER_RANK[step.min_tier]:
        return f"lead is below the {step.min_tier} tier"
    if step.channel == "email":
        if not lead.email:
            return "no email address"
        if lead.email_status == EmailStatus.INVALID:
            return "email address is invalid"
    elif not lead.phone:
        return "no phone number"
    return None


def stop_reason(lead: Lead, since: Optional[datetime] = None) -> Optional[str]:
    """
    Why this lead must not be contacted any more, or None. `since` is when the sequence was
    last reopened: replies from before that no longer count.
    """
    if lead.do_not_contact:
        return "do not contact"
    if lead.pipeline_stage in (PipelineStage.DNC, PipelineStage.REJECTED, PipelineStage.ONBOARDED):
        return f"lead is {lead.pipeline_stage.value}"
    if lead.pipeline_stage in (PipelineStage.REPLIED, PipelineStage.DEMO_BOOKED):
        return "replied" if lead.pipeline_stage == PipelineStage.REPLIED else "meeting booked"
    for log in (*lead.email_logs, *lead.whatsapp_logs):
        if log.replied and (since is None or (log.replied_at and log.replied_at > since)):
            return "replied"
    return None


def _stop(entry: OutreachQueueEntry, reason: str) -> None:
    entry.sequence_status, entry.stop_reason = STOPPED, reason
    entry.next_step_key = entry.next_channel = entry.next_due_at = None


def refresh_stops(db: Session) -> int:
    """Stop the sequence of every active lead that has replied or become do-not-contact."""
    stopped = 0
    for entry in db.query(OutreachQueueEntry).filter(OutreachQueueEntry.sequence_status == ACTIVE).all():
        reason = stop_reason(entry.lead, entry.reopened_at)
        if reason:
            _stop(entry, reason)
            stopped += 1
    if stopped:
        db.commit()
    return stopped


# ─── Planning the next step ───────────────────────────────────────────────────

def schedule_entry(db: Session, entry: OutreachQueueEntry, now: Optional[datetime] = None,
                   recompute: bool = False) -> OutreachQueueEntry:
    """
    Work out the lead's next actionable step and when it is due. Steps the lead
    cannot receive (no email, below the tier…) are logged as skipped. Does nothing
    if the next step is already planned, unless `recompute` is set.
    """
    now = now or utcnow()
    lead = entry.lead
    s = load_settings()

    reason = stop_reason(lead, entry.reopened_at)
    if reason:
        _stop(entry, reason)
        db.commit()
        return entry
    if entry.sequence_status != ACTIVE:
        return entry
    if entry.next_due_at is not None and not recompute:
        return entry

    steps = sequence_for(db, lead)
    idx = entry.step_index or 0
    while idx < len(steps):
        why = _skip_reason(steps[idx], lead)
        if why is None:
            break
        db.add(OutreachStepLog(lead_id=lead.id, step_key=steps[idx].key,
                               channel=steps[idx].channel, status="skipped", note=why, at=now))
        idx += 1
    else:
        entry.sequence_status = COMPLETED
        entry.step_index, entry.next_step_key, entry.next_channel, entry.next_due_at = idx, None, None, None
        db.commit()
        return entry

    step = steps[idx]
    # The first thing ever done for a lead is due straight away; later steps wait
    # for their delay after the previous one was done.
    base = entry.last_done_at + timedelta(days=step.delay_days) if entry.last_done_at else now
    entry.step_index = idx
    entry.next_step_key, entry.next_channel = step.key, step.channel
    entry.next_due_at = next_window_start(base, lead_tz(lead, s), s)
    db.commit()
    return entry


def _entry_for(db: Session, lead_id: int) -> OutreachQueueEntry:
    entry = db.query(OutreachQueueEntry).filter(OutreachQueueEntry.lead_id == lead_id).first()
    if entry is None:
        raise LookupError(f"Lead {lead_id} is not in the queue")
    return entry


def _finish_step(db: Session, lead_id: int, status: str, mode: Optional[str], note: Optional[str],
                 now: Optional[datetime]) -> OutreachQueueEntry:
    now = now or utcnow()
    entry = _entry_for(db, lead_id)
    if entry.sequence_status != ACTIVE or not entry.next_step_key:
        raise ValueError("No step is waiting for this lead")

    db.add(OutreachStepLog(lead_id=lead_id, step_key=entry.next_step_key, channel=entry.next_channel,
                           status=status, mode=mode, note=note, at=now))
    if status == "done":
        entry.last_done_at = now
    entry.step_index = (entry.step_index or 0) + 1
    entry.next_step_key = entry.next_channel = entry.next_due_at = None
    db.commit()
    return schedule_entry(db, entry, now)


def complete_step(db: Session, lead_id: int, mode: str = "manual", note: Optional[str] = None,
                  now: Optional[datetime] = None) -> OutreachQueueEntry:
    """Record that the lead's current step was carried out, and plan the next one."""
    return _finish_step(db, lead_id, "done", mode, note, now)


def skip_step(db: Session, lead_id: int, note: Optional[str] = None,
              now: Optional[datetime] = None) -> OutreachQueueEntry:
    """Skip the lead's current step without counting it as done."""
    return _finish_step(db, lead_id, "skipped", None, note or "Skipped by admin", now)


def reopen_snoozed(db: Session, now: Optional[datetime] = None) -> int:
    """
    Leads that said "not now" are contacted again once their revisit date has come: the
    sequence starts over, and only replies after this moment stop it again.
    """
    now = now or utcnow()
    due = db.query(OutreachQueueEntry).filter(
        OutreachQueueEntry.revisit_at.isnot(None), OutreachQueueEntry.revisit_at <= now,
        OutreachQueueEntry.status == "queued").all()
    reopened = 0
    for entry in due:
        entry.revisit_at = None
        lead = entry.lead
        if lead.do_not_contact or lead.pipeline_stage in (PipelineStage.DNC, PipelineStage.REJECTED,
                                                           PipelineStage.ONBOARDED, PipelineStage.DEMO_BOOKED):
            continue
        if lead.pipeline_stage == PipelineStage.REPLIED:
            lead.pipeline_stage = PipelineStage.EMAIL_SENT
        entry.reopened_at = now
        entry.sequence_status, entry.stop_reason = ACTIVE, None
        entry.step_index, entry.last_done_at = 0, None
        entry.next_step_key = entry.next_channel = entry.next_due_at = None
        db.commit()
        schedule_entry(db, entry, now)
        reopened += 1
    db.commit()
    return reopened


# ─── What is due now ──────────────────────────────────────────────────────────

def due_entries(db: Session, now: Optional[datetime] = None,
                channel: Optional[str] = None) -> list[tuple[OutreachQueueEntry, Lead]]:
    """
    Queued leads whose next step is due and who are inside their working hours
    right now. Follow-ups come before first contacts; higher scores first.
    """
    now = now or utcnow()
    refresh_stops(db)
    s = load_settings()

    q = db.query(OutreachQueueEntry).filter(
        OutreachQueueEntry.status == "queued",
        OutreachQueueEntry.sequence_status == ACTIVE,
        OutreachQueueEntry.next_due_at.isnot(None),
        OutreachQueueEntry.next_due_at <= now,
    )
    if channel:
        q = q.filter(OutreachQueueEntry.next_channel == channel)

    rows = [(e, e.lead) for e in q.all() if in_window(now, lead_tz(e.lead, s), s)]
    rows.sort(key=lambda r: (0 if r[0].last_done_at else 1, -(r[0].score or 0), r[0].queued_at))
    return rows


def _local_midnight_utc(now: datetime, s: ScheduleSettings) -> datetime:
    tz = ZoneInfo(s.fallback_timezone)
    local = _aware(now).astimezone(tz)
    return _naive(datetime.combine(local.date(), time(0, 0), tzinfo=tz))


def done_today(db: Session, channel: str, now: Optional[datetime] = None) -> int:
    now = now or utcnow()
    since = _local_midnight_utc(now, load_settings())
    return db.query(OutreachStepLog).filter(
        OutreachStepLog.channel == channel, OutreachStepLog.status == "done",
        OutreachStepLog.at >= since,
    ).count()


def build_send_plan(db: Session, now: Optional[datetime] = None,
                    rng: Optional[random.Random] = None) -> dict:
    """
    For each channel: how many sends are left today, and the due leads with a
    release time spaced by random gaps inside the configured range. Leads whose
    release time would fall outside their working hours are left for the next round.
    """
    now = now or utcnow()
    rng = rng or random.Random()
    s = load_settings()
    plan = {}

    for channel in CHANNELS:
        rules = s.rules(channel)
        cap_left = max(0, rules.daily_cap - done_today(db, channel, now))

        last = db.query(OutreachStepLog.at).filter(
            OutreachStepLog.channel == channel, OutreachStepLog.status == "done"
        ).order_by(OutreachStepLog.at.desc()).first()
        release = now if last is None else max(now, last[0] + timedelta(seconds=rules.min_gap_seconds))

        items = []
        for entry, lead in due_entries(db, now, channel)[:cap_left]:
            if not in_window(release, lead_tz(lead, s), s):
                continue
            items.append({
                "lead_id": lead.id, "name": lead.name, "tier": entry.tier, "score": entry.score,
                "step_key": entry.next_step_key, "channel": channel,
                "release_at": release.isoformat() + "Z", "mode": rules.mode,
                "approval_required": rules.mode != "auto",
            })
            release = release + timedelta(seconds=rng.uniform(rules.min_gap_seconds, rules.max_gap_seconds))
        plan[channel] = {"mode": rules.mode, "daily_cap": rules.daily_cap,
                         "cap_left": cap_left, "items": items}
    return plan


# ─── Display helper ───────────────────────────────────────────────────────────

def schedule_info(entry: OutreachQueueEntry, lead: Lead, s: ScheduleSettings,
                  steps: list[SequenceStep], now: Optional[datetime] = None) -> dict:
    now = now or utcnow()
    tz = lead_tz(lead, s)
    local_now = _aware(now).astimezone(tz)
    step = next((st for st in steps if st.key == entry.next_step_key), None)
    return {
        "sequence_status": entry.sequence_status, "stop_reason": entry.stop_reason,
        "revisit_at": entry.revisit_at.isoformat() + "Z" if entry.revisit_at else None,
        "step_number": (entry.step_index or 0) + 1, "step_total": len(steps),
        "next_step_key": entry.next_step_key,
        "next_step_label": (step.label or step.key) if step else None,
        "next_channel": entry.next_channel,
        "next_due_at": entry.next_due_at.isoformat() + "Z" if entry.next_due_at else None,
        "due_now": bool(entry.next_due_at and entry.next_due_at <= now and entry.status == "queued"
                        and entry.sequence_status == ACTIVE),
        "lead_local_time": local_now.strftime("%a %H:%M"),
        "in_working_hours": in_window(now, tz, s),
        "mode": s.rules(entry.next_channel).mode if entry.next_channel else None,
    }
