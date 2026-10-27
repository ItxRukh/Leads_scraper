"""
reply_send.py — Answer a lead's reply from the Leads page, through Instantly
-----------------------------------------------------------------------------
When a lead replies, Instantly's event tells us which email to answer (`email_id`) and from which mailbox
(`email_account`). We keep those on the lead (`reply_context`), together with the AI's suggested answer, and
"Send now" posts the (possibly edited) text back to Instantly as a reply in the same thread.

Nothing is ever sent without a person pressing the button.
"""

import html
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

import instantly_client
from database import InboundMessage, Lead, OutreachMessage


class ReplyError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def remember_reply(lead: Lead, inbound: InboundMessage, payload: dict, suggested: Optional[str]) -> None:
    """Keep what is needed to answer this reply, and the suggestion for it."""
    attrs = dict(lead.attributes or {})
    attrs["reply_context"] = {"inbound_id": inbound.id, "email_id": payload.get("email_id"),
                              "eaccount": payload.get("email_account"),
                              "subject": payload.get("reply_subject") or payload.get("email_subject"),
                              "unibox_url": payload.get("unibox_url")}
    if suggested:
        attrs["suggested_reply"] = suggested
        attrs["suggested_reply_for"] = inbound.id
    lead.attributes = attrs


def latest_reply(db: Session, lead: Lead) -> Optional[InboundMessage]:
    return db.query(InboundMessage).filter(InboundMessage.lead_id == lead.id, InboundMessage.kind == "reply") \
        .order_by(InboundMessage.id.desc()).first()


def state(db: Session, lead: Lead) -> Optional[dict]:
    """What the lead page needs to show the reply, the suggestion and the buttons. None when the lead never replied."""
    inbound = latest_reply(db, lead)
    if inbound is None:
        return None
    attrs = lead.attributes or {}
    ctx = attrs.get("reply_context") or {}
    sent = attrs.get("reply_sent") or {}
    can_send = bool(ctx.get("email_id") and ctx.get("eaccount")) and instantly_client.configured() \
        and not lead.do_not_contact and inbound.label != "stop"
    return {"label": inbound.label, "summary": inbound.summary, "text": inbound.body,
            "at": inbound.received_at.isoformat() + "Z" if inbound.received_at else None,
            "suggestion": attrs.get("suggested_reply") if attrs.get("suggested_reply_for") in (None, inbound.id) else None,
            "answered": sent.get("for") == inbound.id, "sent": sent if sent.get("for") == inbound.id else None,
            "can_send": can_send, "unibox_url": ctx.get("unibox_url")}


def _subject(ctx: dict) -> str:
    subject = (ctx.get("subject") or "").strip() or "Your reply"
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def send(db: Session, lead: Lead, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ReplyError("Write the reply first.", 422)
    if lead.do_not_contact:
        raise ReplyError("This lead is on the do-not-contact list.", 409)
    inbound = latest_reply(db, lead)
    if inbound is None:
        raise ReplyError("This lead has not replied.", 409)
    if inbound.label == "stop":
        raise ReplyError("This lead asked us to stop. Nothing can be sent.", 409)
    if not instantly_client.configured():
        raise ReplyError("INSTANTLY_API_KEY is not set in Backend/.env", 409)

    attrs = lead.attributes or {}
    ctx = attrs.get("reply_context") or {}
    if (attrs.get("reply_sent") or {}).get("for") == inbound.id:
        raise ReplyError("This reply was already answered. Open the conversation in Instantly to continue.", 409)
    if not ctx.get("email_id") or not ctx.get("eaccount") or ctx.get("inbound_id") != inbound.id:
        raise ReplyError("Instantly did not tell us which email to answer, so answer this one in Instantly.", 409)

    body_html = html.escape(text).replace("\r\n", "\n").replace("\n", "<br/>")
    try:
        sent = instantly_client.send_reply(ctx["email_id"], ctx["eaccount"], _subject(ctx), text, body_html)
    except instantly_client.InstantlyError as e:
        logger.error(f"[reply] lead {lead.id}: {e}")
        raise ReplyError(str(e), 502) from e

    now = datetime.utcnow()
    # no external_id here: Instantly's id for this email is not the lead's id, and the DNC removal looks for the latter
    db.add(OutreachMessage(lead_id=lead.id, step_key="reply", channel="email", kind="reply", status="sent",
                           subject=_subject(ctx), body=text, sent_at=now, in_reply_to_id=inbound.id))
    lead.attributes = {**attrs, "reply_sent": {"for": inbound.id, "at": now.isoformat() + "Z", "text": text}}
    db.commit()
    logger.info(f"[reply] lead {lead.id} answered through Instantly")
    return {"status": "sent", "at": now.isoformat() + "Z"}
