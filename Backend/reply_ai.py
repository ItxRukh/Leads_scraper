"""
reply_ai.py — Understand what a lead answered, and prepare the right response
-----------------------------------------------------------------------------
Runs from the dispatcher. For every new reply:

  1. The AI labels it: interested | question | not_now | stop | other, with a one-line summary.
  2. The label decides what happens:
       interested  an answer draft with the next step (the profile's booking link)
       question    an answer draft built only from the profile's offer and FAQ
       not_now     a short polite acknowledgement, and the lead is contacted again after
                   "revisit days" (Sending rules)
       stop        the lead is added to the DNC list
       other       nothing (auto-replies, wrong person, unclear)
  3. Drafts go through the same approval as other messages. A channel in Manual mode gets no
     draft (you reply yourself). With "AI replies without approval" on, an Auto channel sends them.
"""

import json
import random
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

import email_sender
import message_service as ms
import scheduling as sch
from database import (
    DNCCrud, InboundMessage, Lead, LeadCRUD, OutreachMessage, OutreachQueueEntry,
)
from message_writer import write_reply
from profile_schema import ProfileConfig
from scoring_engine import resolve_profile
from utils.domain import extract_domain
from utils.groq_helper import _call_groq

LABELS = ("interested", "question", "not_now", "stop", "other")
DRAFT_LABELS = ("interested", "question", "not_now")
DRAFT_WINDOW = timedelta(days=3)         # a reply older than this is no longer answered automatically

_SYSTEM_PROMPT = (
    "You read a business owner's reply to a sales outreach message and decide what they want.\n"
    "Labels:\n"
    "- interested: wants to know more, asks for a call, demo or price, or says yes\n"
    "- question: asks something specific before deciding\n"
    "- not_now: busy or bad timing, maybe later; not a refusal\n"
    "- stop: refuses, asks not to be contacted, or is annoyed\n"
    "- other: automatic reply, wrong person, or unclear\n"
    'Return ONLY JSON: {"label": "<one label>", "summary": "<one short sentence about what they said>"}'
)


def classify_reply(lead: Lead, text: str) -> Optional[tuple[str, str]]:
    """(label, summary), or None when the AI is unavailable or its answer cannot be read."""
    raw = _call_groq(system_prompt=_SYSTEM_PROMPT,
                     user_prompt=f"Business: {lead.name}\nTheir reply:\n{text[:1500]}\n\nJSON:",
                     max_tokens=120, temperature=0.0)
    if not raw:
        return None
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        data = json.loads(cleaned.strip(), strict=False)
        label = str(data.get("label", "")).strip().lower()
        summary = str(data.get("summary", "")).strip()[:300]
    except (json.JSONDecodeError, AttributeError):
        logger.error(f"Could not read the AI's label for a reply from lead {lead.id}")
        return None
    return (label, summary) if label in LABELS else None


def _apply_label(db: Session, lead: Lead, inbound: InboundMessage, label: str, now: datetime) -> None:
    if label == "stop" and not lead.do_not_contact:
        DNCCrud.add_to_dnc(db, email=lead.email, phone=lead.phone, domain=extract_domain(lead.website),
                           reason=f"opt_out_keyword: AI read the {inbound.channel} reply as a refusal: "
                                  f"'{(inbound.body or '')[:100]}'")
        LeadCRUD.mark_do_not_contact(db, lead.id)
    elif label == "not_now":
        entry = db.query(OutreachQueueEntry).filter(OutreachQueueEntry.lead_id == lead.id).first()
        if entry is not None:
            entry.revisit_at = now + timedelta(days=sch.load_settings().revisit_days)


def _wants_draft(db: Session, inbound: InboundMessage, now: datetime) -> bool:
    if inbound.label not in DRAFT_LABELS or not inbound.received_at or now - inbound.received_at > DRAFT_WINDOW:
        return False
    return db.query(OutreachMessage.id).filter(
        OutreachMessage.kind == "reply", OutreachMessage.in_reply_to_id == inbound.id).first() is None


def _draft_answer(db: Session, lead: Lead, inbound: InboundMessage, now: datetime,
                  rng: Optional[random.Random]) -> bool:
    s = sch.load_settings()
    rules = s.rules(inbound.channel)
    if rules.mode == "manual":
        return False                                    # the person answers by hand
    profile = resolve_profile(db, lead)
    if profile is None:
        return False

    draft = write_reply(lead, inbound.channel, inbound.body or "", inbound.label,
                        ProfileConfig.model_validate(profile.config), ms.previous_texts(db, lead.id))
    if draft is None:
        return False

    subject = None
    if inbound.channel == "email":
        root = ms.thread_root(db, lead.id)
        subject = "Re: " + (root.subject or "your message").removeprefix("Re: ") if root else "Re: your message"

    msg = OutreachMessage(lead_id=lead.id, step_key=f"reply_{inbound.id}", channel=inbound.channel,
                          subject=subject, body=draft.body, status="draft", kind="reply",
                          in_reply_to_id=inbound.id,
                          tracking_token=email_sender.new_tracking_token() if inbound.channel == "email" else None)
    db.add(msg)
    db.flush()
    if s.auto_reply and rules.mode == "auto":
        ms.approve_draft(db, msg, "auto", now, rng)
    db.commit()
    logger.info(f"Reply drafted for lead {lead.id} ({inbound.label})")
    return True


def process_new_replies(db: Session, now: Optional[datetime] = None, limit: int = 10,
                        rng: Optional[random.Random] = None) -> int:
    """Label new replies and prepare answers. Returns how many replies were handled this round."""
    now = now or sch.utcnow()
    handled = 0

    unlabeled = db.query(InboundMessage).filter(
        InboundMessage.kind == "reply", InboundMessage.label.is_(None), InboundMessage.lead_id.isnot(None)
    ).order_by(InboundMessage.id).limit(limit).all()
    for inbound in unlabeled:
        lead = db.get(Lead, inbound.lead_id)
        result = classify_reply(lead, inbound.body or "") if lead else None
        if result is None:
            continue                                    # tried again next round
        inbound.label, inbound.summary = result
        db.commit()
        _apply_label(db, lead, inbound, inbound.label, now)
        db.commit()
        handled += 1

    recent = db.query(InboundMessage).filter(
        InboundMessage.kind == "reply", InboundMessage.label.in_(DRAFT_LABELS),
        InboundMessage.received_at >= now - DRAFT_WINDOW).order_by(InboundMessage.id).all()
    for inbound in recent:
        lead = db.get(Lead, inbound.lead_id) if inbound.lead_id else None
        if lead and not lead.do_not_contact and _wants_draft(db, inbound, now):
            _draft_answer(db, lead, inbound, now, rng)
    return handled
