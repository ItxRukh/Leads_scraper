"""
utils/outreach_reason.py — "Why hasn't this lead been contacted?" helper
---------------------------------------------------------------------------
Looks at a lead's actual current state (email present/verified, phone
present, final score, do_not_contact flag) plus its real EmailLog /
WhatsAppLog (SMS reuses this table — see scripts/sms_outreach.py) / CallLog
history, and produces a human-readable status + reason per channel.

This is a READ-ONLY diagnostic layer. It mirrors the eligibility rules
already enforced by scripts/email_outreach.py, scripts/sms_outreach.py and
scripts/voice_agent.py — it does not decide who actually gets contacted,
it only explains what those scripts would decide and why, so the UI can
show it instead of a lead just silently sitting there with no outreach.

Nothing here is hardcoded per-lead — every reason is derived from the
lead's real DB fields, its real log rows, and the live Settings/config
values (warm/hot thresholds, follow-up wait days) at call time.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import desc

from config import settings
from database import Lead, EmailLog, WhatsAppLog, CallLog


def _days_ago(dt: Optional[datetime]) -> int:
    if not dt:
        return 0
    return max((datetime.utcnow() - dt).days, 0)


def _compute_for_lead(lead: Lead, latest_email: Optional[EmailLog],
                       latest_sms: Optional[WhatsAppLog], latest_call: Optional[CallLog],
                       warm_threshold: int, hot_threshold: int) -> dict:
    """Pure function — no DB access. All log rows are passed in so this can
    be reused both for a single lead and for a batch of leads without
    re-querying per row."""

    # ── EMAIL channel ────────────────────────────────────────────────────
    if lead.do_not_contact:
        email_status, email_reason = "blocked", "On the Do-Not-Contact list — all outreach blocked."
    elif latest_email and latest_email.unsubscribed:
        email_status, email_reason = "blocked", "Unsubscribed from email — will not be emailed again."
    elif latest_email and latest_email.replied:
        email_status, email_reason = "replied", "Replied to an email — no further automatic email needed."
    elif latest_email:
        email_status = "sent"
        email_reason = f"Email sent {_days_ago(latest_email.sent_at)}d ago, no reply yet."
    elif not lead.email:
        email_status, email_reason = "no_email", "No email address on file for this lead."
    elif not lead.email_verified:
        email_status, email_reason = "unverified", "Email found but not yet verified as deliverable."
    elif lead.final_score < warm_threshold:
        email_status = "score_low"
        email_reason = f"Email verified, but score ({lead.final_score}) is below the outreach threshold ({warm_threshold})."
    else:
        email_status, email_reason = "ready", "Eligible for email outreach — waiting for the next send batch."

    # ── SMS channel (funnel step: email → N days no reply → sms) ────────
    if lead.do_not_contact:
        sms_status, sms_reason = "blocked", "On the Do-Not-Contact list — all outreach blocked."
    elif not lead.phone:
        sms_status, sms_reason = "no_phone", "No phone number on file — SMS follow-up is not possible."
    elif latest_sms and latest_sms.replied:
        sms_status, sms_reason = "replied", "Replied to the SMS follow-up."
    elif latest_sms:
        sms_status = "sent"
        sms_reason = f"SMS follow-up sent {_days_ago(latest_sms.sent_at)}d ago, no reply yet."
    elif not latest_email:
        if not lead.email:
            # Feature 4 — Smart First-Touch Routing: no email on file means
            # the normal email→SMS funnel can never start, so this lead is
            # picked up directly by get_no_email_first_touch_leads() instead.
            if lead.final_score < warm_threshold:
                sms_status = "score_low"
                sms_reason = (
                    f"No email on file, and score ({lead.final_score}) is below the "
                    f"warm threshold ({warm_threshold}) — first-touch SMS is reserved for warm+ leads."
                )
            else:
                sms_status = "due"
                sms_reason = "No email on file — queued for an automatic first-touch SMS on the next scheduler run."
        else:
            sms_status = "waiting"
            sms_reason = "Has an email on file but none has been sent yet — SMS follow-up will trigger once it is."
    elif latest_email.replied:
        sms_status, sms_reason = "not_needed", "Lead already replied to email — SMS follow-up skipped."
    else:
        wait_days = settings.EMAIL_FOLLOWUP_WAIT_DAYS
        elapsed = _days_ago(latest_email.sent_at)
        if elapsed < wait_days:
            sms_status = "waiting"
            sms_reason = f"Waiting on the email reply window — SMS follow-up triggers in {wait_days - elapsed}d if there's no reply."
        else:
            sms_status, sms_reason = "due", "Due for an SMS follow-up on the next scheduler run."

    # ── CALL channel (funnel step: sms → N days no reply → call, warm+) ──
    if lead.do_not_contact:
        call_status, call_reason = "blocked", "On the Do-Not-Contact list — all outreach blocked."
    elif not lead.phone:
        call_status, call_reason = "no_phone", "No phone number on file — a voice call is not possible."
    elif latest_call:
        outcome = latest_call.outcome.value if latest_call.outcome else "unknown"
        call_status, call_reason = "called", f"Already called — outcome: {outcome}."
    elif lead.final_score < warm_threshold:
        call_status = "score_low"
        call_reason = f"Score ({lead.final_score}) is below the warm threshold ({warm_threshold}) — calls are reserved for warm+ leads."
    elif not latest_sms:
        if not lead.email and lead.final_score >= hot_threshold:
            # Feature 4 — Smart First-Touch Routing (call side): hot lead,
            # no email, zero contact — picked up by
            # get_no_email_hot_first_touch_leads() instead of waiting on SMS.
            call_status = "due"
            call_reason = "No email on file, hot score — queued for an automatic first-touch call on the next scheduler run."
        else:
            call_status = "stuck_no_trigger"
            call_reason = "No SMS follow-up has gone out yet, so the automatic call step has nothing to follow up from."
    elif latest_sms.replied:
        call_status, call_reason = "not_needed", "Lead already replied to SMS — call follow-up skipped."
    else:
        wait_days = settings.SMS_FOLLOWUP_WAIT_DAYS
        elapsed = _days_ago(latest_sms.sent_at)
        if elapsed < wait_days:
            call_status = "waiting"
            call_reason = f"Waiting on the SMS reply window — call follow-up triggers in {wait_days - elapsed}d if there's no reply."
        else:
            call_status, call_reason = "due", "Due for a follow-up call on the next scheduler run."

    # ── Single best-summary line for a compact table badge ───────────────
    if lead.do_not_contact:
        primary = "On DNC list — all outreach blocked"
    elif email_status == "no_email" and sms_status == "no_phone":
        primary = "No email or phone found — cannot be contacted"
    elif email_status == "no_email" and sms_status == "due":
        primary = "No email found — SMS/call first-touch queued automatically (Smart First-Touch Routing)"
    elif email_status == "unverified":
        primary = email_reason
    elif email_status == "score_low":
        primary = email_reason
    elif email_status in ("replied",) or sms_status == "replied":
        primary = "Replied — in conversation, no automatic outreach needed"
    elif email_status == "sent" and sms_status == "waiting":
        primary = sms_reason
    elif email_status == "sent" and sms_status == "due":
        primary = "Due for an SMS follow-up"
    elif sms_status == "sent" and call_status == "waiting":
        primary = call_reason
    elif sms_status == "sent" and call_status == "due":
        primary = "Due for a follow-up call"
    elif call_status == "called":
        primary = call_reason
    elif email_status == "ready":
        primary = "Ready — awaiting the next outreach batch"
    elif email_status == "blocked":
        primary = email_reason
    else:
        primary = email_reason

    return {
        "email": {"status": email_status, "reason": email_reason},
        "sms":   {"status": sms_status,   "reason": sms_reason},
        "call":  {"status": call_status,  "reason": call_reason},
        "primary_reason": primary,
    }


def get_outreach_status(lead: Lead, db) -> dict:
    """Single-lead version — used by the lead detail endpoint."""
    from app_settings import get_score_thresholds
    hot_threshold, warm_threshold = get_score_thresholds()

    latest_email = (
        db.query(EmailLog).filter(EmailLog.lead_id == lead.id)
        .order_by(desc(EmailLog.sent_at)).first()
    )
    latest_sms = (
        db.query(WhatsAppLog).filter(WhatsAppLog.lead_id == lead.id)
        .order_by(desc(WhatsAppLog.sent_at)).first()
    )
    latest_call = (
        db.query(CallLog).filter(CallLog.lead_id == lead.id)
        .order_by(desc(CallLog.called_at)).first()
    )

    return _compute_for_lead(lead, latest_email, latest_sms, latest_call, warm_threshold, hot_threshold)


def get_outreach_status_bulk(leads: list, db) -> dict:
    """
    Batch version — used by the /leads list endpoint so a page of (say)
    100 leads costs 3 extra queries total instead of 300. Returns
    {lead_id: status_dict}.
    """
    if not leads:
        return {}

    from app_settings import get_score_thresholds
    hot_threshold, warm_threshold = get_score_thresholds()

    lead_ids = [l.id for l in leads]

    # One query per log table for the whole page, newest-first, then keep
    # only the first (latest) row seen per lead_id.
    latest_email_by_lead: dict = {}
    for log in db.query(EmailLog).filter(EmailLog.lead_id.in_(lead_ids)).order_by(desc(EmailLog.sent_at)).all():
        latest_email_by_lead.setdefault(log.lead_id, log)

    latest_sms_by_lead: dict = {}
    for log in db.query(WhatsAppLog).filter(WhatsAppLog.lead_id.in_(lead_ids)).order_by(desc(WhatsAppLog.sent_at)).all():
        latest_sms_by_lead.setdefault(log.lead_id, log)

    latest_call_by_lead: dict = {}
    for log in db.query(CallLog).filter(CallLog.lead_id.in_(lead_ids)).order_by(desc(CallLog.called_at)).all():
        latest_call_by_lead.setdefault(log.lead_id, log)

    result = {}
    for lead in leads:
        result[lead.id] = _compute_for_lead(
            lead,
            latest_email_by_lead.get(lead.id),
            latest_sms_by_lead.get(lead.id),
            latest_call_by_lead.get(lead.id),
            warm_threshold,
            hot_threshold,
        )
    return result