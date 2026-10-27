"""
connections.py — Is each channel working right now?
---------------------------------------------------
Keeps the latest state of the email connection so the
admin panel can show a green / yellow / red chip and warn when messages are
waiting but nothing can send them.
"""

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

import email_sender
from config import settings
from app_settings import get_app_settings
from database import ConnectionStatus, OutreachMessage, OutreachStepLog



def record(db: Session, channel: str, state: Optional[str] = None, detail: Optional[dict] = None,
           ok: Optional[bool] = None, error: Optional[str] = None,
           now: Optional[datetime] = None) -> ConnectionStatus:
    """Note a signal from a channel. `ok=True` clears the last error; `error` stores a new one."""
    now = now or datetime.utcnow()
    row = db.get(ConnectionStatus, channel) or ConnectionStatus(channel=channel)
    row.last_seen = now
    if state is not None:
        row.state = state
    if detail:
        row.detail = {**(row.detail or {}), **detail}
    if ok:
        row.last_ok, row.last_error = now, None
    if error:
        row.last_error = error[:300]
    db.add(row)
    db.commit()
    return row


def _count(db: Session, channel: str, *statuses: str) -> int:
    """Messages a person acts on (the later emails written with a first email are not counted)."""
    return db.query(func.count(OutreachMessage.id)).filter(
        OutreachMessage.channel == channel, OutreachMessage.status.in_(statuses),
        func.coalesce(OutreachMessage.kind, "step") != "followup"
    ).scalar() or 0


def email_summary(db: Session, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    row = db.get(ConnectionStatus, "email")
    instantly = get_app_settings().get("sendProvider") == "instantly"
    if instantly:
        import instantly_client, instantly_handoff
        configured = instantly_client.configured() and bool(instantly_handoff.campaign_id())
    else:
        configured = email_sender.is_configured()
    if not configured:
        state = "not_configured"
    elif row and row.last_error:
        state = "error"
    elif row and row.last_ok:
        state = "ok"
    else:
        state = "unknown"                   # configured, but nothing has used it yet
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "state": state,                     # not_configured | unknown | ok | error
        "online": state in ("ok", "unknown"),
        "provider": "instantly" if instantly else "smtp",
        "address": ("Instantly campaign" if instantly else email_sender.sender_address()) if configured else None,
        "last_ok": row.last_ok.isoformat() + "Z" if row and row.last_ok else None,
        "last_error": row.last_error if row else None,
        "sent_today": db.query(func.count(OutreachStepLog.id)).filter(
            OutreachStepLog.channel == "email", OutreachStepLog.status == "done",
            OutreachStepLog.at >= day_start).scalar() or 0,
        "waiting": _count(db, "email", "approved"),
        "to_approve": _count(db, "email", "draft"),
        "open_tracking": {
            "enabled": bool(settings.EMAIL_OPEN_TRACKING),
            "ready": email_sender.tracking_ready(),       # enabled and PUBLIC_BASE_URL is reachable
            "opened": db.query(func.count(OutreachMessage.id)).filter(OutreachMessage.open_count > 0).scalar() or 0,
        },
    }
