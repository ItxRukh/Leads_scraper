"""
instantly_events.py — What to do when Instantly reports something about a lead
-------------------------------------------------------------------------------
Called by the webhook (webhooks/instantly_webhook.py) with Instantly's JSON. Events (v2 names):

    reply_received        a person answered: record it, label it, and send interested leads to GoHighLevel
    lead_interested       Instantly's own label: send to GoHighLevel
    lead_not_interested   stop writing to them
    lead_unsubscribed     do-not-contact list, for good
    email_bounced         mark the address invalid
    email_sent            a follow-up went out: record it
    email_opened          the lead opened an email: opened_at and the "opened" stage
    lead_meeting_booked   the lead booked a meeting

The lead is found by `lead_id` (a variable we send with the lead; Instantly returns it in every event), else by email.
Anything else is ignored. Every action is safe to repeat, because Instantly may deliver an event twice.
"""

import hashlib
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

import ghl_sync
import replies
import reply_send
import scheduling as sch
from app_settings import get_app_settings
from database import (DNCCrud, EmailLog, InboundMessage, Lead, LeadCRUD, OutreachMessage, OutreachQueueEntry,
                      PipelineStage)
from utils.domain import extract_domain


def find_lead(db: Session, payload: dict) -> Optional[Lead]:
    raw = payload.get("lead_id")
    if str(raw).isdigit():
        lead = db.get(Lead, int(raw))
        if lead:
            return lead
    email = (payload.get("lead_email") or payload.get("email") or "").strip().lower()
    return db.query(Lead).filter(Lead.email.ilike(email)).first() if email else None


def _stop_sequence(db: Session, lead: Lead, reason: str) -> None:
    entry = db.query(OutreachQueueEntry).filter(OutreachQueueEntry.lead_id == lead.id).first()
    if entry is not None and entry.sequence_status != sch.STOPPED:
        sch._stop(entry, reason)
        db.commit()


def _do_not_contact(db: Session, lead: Lead, reason: str) -> None:
    """Instantly already knows (it reported the unsubscribe), so only our side is updated."""
    DNCCrud.add_to_dnc(db, email=lead.email, phone=lead.phone, domain=extract_domain(lead.website), reason=reason)
    LeadCRUD.mark_do_not_contact(db, lead.id)
    _stop_sequence(db, lead, "do not contact")


def _reply_key(lead: Lead, payload: dict, text: str) -> str:
    digest = hashlib.sha1(f"{payload.get('timestamp', '')}|{text}".encode()).hexdigest()[:16]
    return f"instantly:reply:{lead.id}:{digest}"


def _on_reply(db: Session, lead: Lead, payload: dict, now: datetime) -> dict:
    import reply_ai

    text = (payload.get("reply_text") or payload.get("reply_text_snippet") or "").strip()
    if not text:
        return {"status": "ignored", "reason": "empty reply"}
    key = _reply_key(lead, payload, text)
    if not replies.record_reply(db, lead, "email", key, lead.email or "", text, now):
        return {"status": "duplicate"}
    _stop_sequence(db, lead, "replied")

    inbound = db.query(InboundMessage).filter(InboundMessage.message_key == key).one()
    label = reply_ai.label_inbound(db, inbound, now)
    result = {"status": "ok", "label": label}
    # a suggested answer for every reply worth answering, whether or not GoHighLevel is connected
    suggested = ghl_sync._suggest_reply(db, lead, inbound) if label in ("interested", "question") and not lead.do_not_contact else None
    reply_send.remember_reply(lead, inbound, payload, suggested)
    db.commit()
    if label in get_app_settings().get("ghlPushLabels", ["interested", "question"]) and not lead.do_not_contact:
        result["ghl"] = ghl_sync.push(db, lead, label, inbound)["status"]
    return result


def _on_email_sent(db: Session, lead: Lead, payload: dict, now: datetime) -> dict:
    """Instantly sent one of the emails: the first is already recorded; later ones are marked when they go out."""
    step = payload.get("step")
    messages = db.query(OutreachMessage).filter(
        OutreachMessage.lead_id == lead.id, OutreachMessage.channel == "email",
        OutreachMessage.status.in_(("sent", "handed_off"))).order_by(OutreachMessage.id).all()
    index = int(step) - 1 if str(step).isdigit() and int(step) >= 1 else None
    if index is None or index >= len(messages) or index == 0:
        return {"status": "ok"}
    msg = messages[index]
    if msg.status == "handed_off":
        msg.status, msg.sent_at = "sent", now
        db.add(EmailLog(lead_id=lead.id, subject=msg.subject, body=msg.body, sent_at=now))
        db.commit()
    return {"status": "ok"}


def _on_email_opened(db: Session, lead: Lead, payload: dict, now: datetime) -> dict:
    """Instantly saw an email opened: record the first open of that email, and move the lead to 'opened'."""
    step = payload.get("step")
    messages = db.query(OutreachMessage).filter(
        OutreachMessage.lead_id == lead.id, OutreachMessage.channel == "email", OutreachMessage.kind == "step",
        OutreachMessage.status.in_(("sent", "handed_off"))).order_by(OutreachMessage.id).all()
    index = int(step) - 1 if str(step).isdigit() and int(step) >= 1 else 0
    if messages:
        msg = messages[min(index, len(messages) - 1)]
        msg.open_count = (msg.open_count or 0) + 1
        msg.opened_at = msg.opened_at or now

    log = db.query(EmailLog).filter(EmailLog.lead_id == lead.id).order_by(EmailLog.sent_at.desc()).first()
    if log is not None and not log.email_opened:
        log.email_opened, log.opened_at = True, now
    if lead.pipeline_stage in (PipelineStage.EMAIL_SENT, PipelineStage.EMAIL_VERIFIED, PipelineStage.AUDITED):
        lead.pipeline_stage = PipelineStage.EMAIL_OPENED
    db.commit()
    return {"status": "ok"}


def handle_event(db: Session, payload: dict, now: Optional[datetime] = None) -> dict:
    now = now or datetime.utcnow()
    event = payload.get("event_type") or payload.get("event") or ""
    lead = find_lead(db, payload)
    if lead is None:
        return {"status": "ignored", "reason": "no matching lead"}

    if event in ("reply_received", "email_replied"):
        return _on_reply(db, lead, payload, now)

    if event == "lead_interested":
        if lead.do_not_contact:
            return {"status": "ignored", "reason": "do not contact"}
        _stop_sequence(db, lead, "interested")
        return {"status": "ok", "ghl": ghl_sync.push(db, lead, "interested")["status"]}

    if event == "lead_not_interested":
        LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.REJECTED)
        _stop_sequence(db, lead, "not interested")
        return {"status": "ok"}

    if event in ("lead_unsubscribed", "unsubscribed"):
        _do_not_contact(db, lead, "Unsubscribed via Instantly")
        return {"status": "ok"}

    if event == "email_bounced":
        replies.record_bounce(db, lead, f"instantly:bounce:{lead.id}:{payload.get('timestamp', '')}", "Bounced (reported by Instantly)")
        _stop_sequence(db, lead, "email bounced")
        return {"status": "ok"}

    if event == "email_opened":
        return _on_email_opened(db, lead, payload, now)

    if event == "email_sent":
        return _on_email_sent(db, lead, payload, now)

    if event == "lead_meeting_booked":
        LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.DEMO_BOOKED)
        _stop_sequence(db, lead, "meeting booked")
        return {"status": "ok", "ghl": ghl_sync.push(db, lead, "meeting_booked")["status"]}

    logger.debug(f"[instantly] event '{event}' ignored")
    return {"status": "ignored", "reason": f"event '{event}' needs no action"}
