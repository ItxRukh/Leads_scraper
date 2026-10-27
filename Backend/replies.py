"""
replies.py — What happens when a lead answers (email or WhatsApp)
-----------------------------------------------------------------
One place records the reply, moves the lead in the CRM, and handles opt-outs, so
both channels behave the same. Stopping the lead's sequence needs no extra step:
the scheduler stops any lead that has a reply on record.
"""

import re
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from database import (
    DNCCrud, EmailLog, EmailStatus, InboundMessage, Lead, LeadCRUD, PipelineStage, WhatsAppLog,
)
from scripts.dnc import check_opt_out_keywords
from utils.domain import extract_domain

_FINISHED_STAGES = (PipelineStage.DNC, PipelineStage.REJECTED, PipelineStage.ONBOARDED,
                    PipelineStage.DEMO_BOOKED)


_STOP_WORDS = {"stop", "unsubscribe", "cancel", "quit"}


def is_opt_out(text: str) -> bool:
    """
    The opt-out phrases the DNC list already knows, plus a bare "STOP": every message
    we send tells people to reply STOP, so a short reply that starts with it must count.
    """
    if check_opt_out_keywords(text):
        return True
    words = re.findall(r"[a-z']+", (text or "").lower())
    return 0 < len(words) <= 3 and words[0] in _STOP_WORDS


def _already_seen(db: Session, key: str) -> bool:
    return db.query(InboundMessage.id).filter(InboundMessage.message_key == key).first() is not None


def record_reply(db: Session, lead: Lead, channel: str, key: str, sender: str, text: str,
                 now: Optional[datetime] = None) -> bool:
    """Returns False (and does nothing) if this exact message was already recorded."""
    if _already_seen(db, key):
        return False
    now = now or datetime.utcnow()
    text = (text or "").strip()

    db.add(InboundMessage(message_key=key, channel=channel, kind="reply", lead_id=lead.id,
                          sender=sender, body=text[:4000], received_at=now))

    if channel == "email":
        log = db.query(EmailLog).filter(EmailLog.lead_id == lead.id).order_by(EmailLog.sent_at.desc()).first()
        if log is None:
            log = EmailLog(lead_id=lead.id)
            db.add(log)
    else:
        log = db.query(WhatsAppLog).filter(WhatsAppLog.lead_id == lead.id) \
            .order_by(WhatsAppLog.sent_at.desc()).first()
        if log is None or log.replied:
            log = WhatsAppLog(lead_id=lead.id, message_content=None)
            db.add(log)
    log.replied, log.replied_at, log.reply_content, log.is_read = True, now, text[:4000], False

    if lead.pipeline_stage not in _FINISHED_STAGES:
        lead.pipeline_stage = PipelineStage.REPLIED
        lead.updated_at = now
    db.commit()

    if is_opt_out(text):
        DNCCrud.add_to_dnc(
            db, email=lead.email, phone=lead.phone, domain=extract_domain(lead.website),
            reason=f"opt_out_keyword: Auto-detected opt-out in {channel} reply: '{text[:100]}'",
        )
        LeadCRUD.mark_do_not_contact(db, lead.id)
        logger.info(f"Lead {lead.id} opted out by {channel} reply")

    logger.info(f"Reply recorded for lead {lead.id} ({lead.name}) via {channel}")
    return True


def record_bounce(db: Session, lead: Lead, key: str, text: str) -> bool:
    """An undeliverable address: mark it invalid so no more email steps are planned for it."""
    if _already_seen(db, key):
        return False
    db.add(InboundMessage(message_key=key, channel="email", kind="bounce", lead_id=lead.id,
                          sender="mail delivery system", body=(text or "")[:2000]))
    lead.email_status = EmailStatus.INVALID
    lead.email_verified = False
    db.commit()
    logger.warning(f"Email to lead {lead.id} ({lead.name}) bounced; address marked invalid")
    return True
