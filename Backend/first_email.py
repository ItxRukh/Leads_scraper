"""
first_email.py — Write the first email of every lead that is ready
--------------------------------------------------------------------
Only the first email is written for each lead, from the industry's knowledge base and the lead's (checked) weakness.
The follow-ups are short templates kept in GoHighLevel. The email is saved as a draft and waits on the Approve emails
page; nothing is sent from here.

A lead is ready when it has an email address that is not known to be bad, it is not on the do-not-contact list, and
its address has been checked.
"""

from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

import scheduling as sch
from database import EmailStatus, Lead, OutreachMessage
from message_writer import write_message
from profile_schema import ProfileConfig
from scoring_engine import resolve_profile

DRAFT_STATES = ("draft", "approved", "claimed", "sent", "failed", "discarded")   # any of these: the lead already has its email


def is_ready(lead: Lead) -> bool:
    if lead.do_not_contact or not lead.email:
        return False
    check = ((lead.attributes or {}).get("email_check") or {}).get("status")
    return check is not None and check != "invalid" and lead.email_status != EmailStatus.INVALID


def has_email_row(db: Session, lead_id: int) -> bool:
    return db.query(OutreachMessage.id).filter(
        OutreachMessage.lead_id == lead_id, OutreachMessage.channel == "email",
        OutreachMessage.kind != "reply", OutreachMessage.status.in_(DRAFT_STATES)).first() is not None


def write_for(db: Session, lead: Lead) -> Optional[OutreachMessage]:
    """Write and save the first email for one lead. None when it could not be written (the AI is down, no profile)."""
    profile = resolve_profile(db, lead)
    steps = sch.sequence_for(db, lead)
    step = next((st for st in steps if st.channel == "email" and sch._skip_reason(st, lead) is None), None)
    if profile is None or step is None:
        return None
    draft = write_message(lead, step, steps, ProfileConfig.model_validate(profile.config), [])
    if draft is None:
        logger.warning(f"[first email] nothing written for lead {lead.id}")
        return None
    msg = OutreachMessage(lead_id=lead.id, step_key=step.key, channel="email", subject=draft.subject, body=draft.body,
                          status="draft", kind="step")
    db.add(msg)
    db.commit()
    return msg


def rewrite(db: Session, msg: OutreachMessage) -> OutreachMessage:
    """Replace a draft by a freshly written one. The old draft is thrown away only when the new one exists."""
    if msg.status != "draft":
        raise ValueError("Only drafts can be rewritten")
    new = write_for(db, msg.lead)
    if new is None:
        raise ValueError("The AI could not write a new email right now. The old one was kept.")
    msg.status, msg.error = "discarded", "Rewritten"
    db.commit()
    return new


def write_batch(db: Session, batch_id: Optional[int] = None, limit: int = 500) -> int:
    """Write the first email for every ready lead (of one batch) that has none yet. Returns how many were written."""
    q = db.query(Lead)
    if batch_id is not None:
        q = q.filter(Lead.import_batch_id == batch_id)
    written = 0
    for lead in q.order_by(Lead.id):
        if written >= limit:
            break
        if not is_ready(lead) or has_email_row(db, lead.id):
            continue
        try:
            written += write_for(db, lead) is not None
        except Exception as e:                                   # one failing lead must not stop the others
            logger.error(f"[first email] lead {lead.id}: {e}")
            db.rollback()
    return written


def counts(db: Session, batch_id: int) -> dict:
    """For the progress panel: how many leads are ready for an email, and how many already have one."""
    ready = [lead for lead in db.query(Lead).filter(Lead.import_batch_id == batch_id) if is_ready(lead)]
    return {"ready": len(ready), "written": sum(has_email_row(db, lead.id) for lead in ready)}
