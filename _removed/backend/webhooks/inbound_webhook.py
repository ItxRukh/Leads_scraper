"""
webhooks/inbound_webhook.py — Inbound SMS & Call Capture (log-only, email-first)
==================================================================================
Strategy: we want every real conversation to happen over EMAIL (the one channel
that's fully tracked — opens, replies, DNC, all automatic via Instantly).

SMS and Calls are "interest signals", not conversation channels. So when a lead
texts back or calls our number directly, we do NOT try to hold a live back-
and-forth. We just:

  1. Match the caller/texter to a lead by phone number
  2. Log what happened (so it's visible in the admin panel)
  3. Send a short auto-reply telling them we'll follow up by email
  4. Mark the lead so a human (you) can review it and personally reply by email

This keeps SMS/voice lightweight while making sure NO lead reply ever gets
silently lost.

Twilio configuration needed (one-time, in the Twilio Console):
  - Phone Number → Messaging → "A message comes in" →
      Webhook: {PUBLIC_BASE_URL}/webhooks/sms/inbound   (HTTP POST)
  - Phone Number → Voice → "A call comes in" →
      Webhook: {PUBLIC_BASE_URL}/webhooks/voice/inbound (HTTP POST)

{PUBLIC_BASE_URL} is your ngrok URL in dev, or your real domain in production.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import Response
from loguru import logger
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse

from database import (
    Lead, WhatsAppLog, CallLog, CallOutcome,
    SyncSessionLocal,
)

router = APIRouter(prefix="/webhooks", tags=["Inbound (SMS & Call)"])


# ─── Phone number matching ─────────────────────────────────────────────────────

def _find_lead_by_phone(db, raw_phone: str) -> Optional[Lead]:
    """
    Matches an inbound caller/texter's number to a lead.
    Compares the last 10 digits only, so formatting differences
    (+92 vs 0 vs spaces/dashes) don't cause a missed match.
    """
    if not raw_phone:
        return None

    digits = "".join(c for c in raw_phone if c.isdigit())
    last10 = digits[-10:] if len(digits) >= 10 else digits

    candidates = db.query(Lead).filter(Lead.phone.isnot(None)).all()
    for lead in candidates:
        lead_digits = "".join(c for c in (lead.phone or "") if c.isdigit())
        if lead_digits[-10:] == last10:
            return lead
    return None


# ─── Inbound SMS ────────────────────────────────────────────────────────────────

@router.post("/sms/inbound")
async def handle_inbound_sms(
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(None),
):
    """
    Twilio hits this the moment someone replies to (or texts) our SMS number.
    We log the reply and point them to email — we do not hold a live SMS
    conversation here.
    """
    db = SyncSessionLocal()
    try:
        lead = _find_lead_by_phone(db, From)

        if lead:
            # Attach to their most recent outbound SMS log if one exists,
            # otherwise create a fresh log entry so the reply isn't lost.
            log = (
                db.query(WhatsAppLog)
                .filter(WhatsAppLog.lead_id == lead.id)
                .order_by(WhatsAppLog.sent_at.desc())
                .first()
            )
            if not log or log.replied:
                log = WhatsAppLog(lead_id=lead.id, message_content=None)
                db.add(log)

            log.replied = True
            log.reply_content = Body
            log.replied_at = datetime.utcnow()
            log.is_read = False
            db.commit()

            logger.info(f"Inbound SMS logged for lead {lead.id} ({lead.name}): {Body[:80]!r}")
        else:
            logger.warning(f"Inbound SMS from unknown number {From}: {Body[:80]!r}")

        # Auto-reply — sets expectation that a human will follow up by email
        twiml = MessagingResponse()
        twiml.message(
            "Thanks for your reply! We've noted it and will follow up "
            "with full details by email shortly."
        )
        return Response(content=str(twiml), media_type="application/xml")

    finally:
        db.close()


# ─── Inbound Call ───────────────────────────────────────────────────────────────

@router.post("/voice/inbound")
async def handle_inbound_call(
    From: str = Form(...),
    CallSid: str = Form(None),
):
    """
    Twilio hits this when someone calls our Twilio number directly
    (i.e. a callback — not a call WE placed). We do not connect them to
    the live AI voice agent here; we just log the callback and play a
    short message pointing them to email.
    """
    db = SyncSessionLocal()
    try:
        lead = _find_lead_by_phone(db, From)

        if lead:
            call_log = CallLog(
                lead_id=lead.id,
                outcome=CallOutcome.CALLBACK,
                notes="Inbound callback — lead called our number directly.",
                called_at=datetime.utcnow(),
                is_read=False,
            )
            db.add(call_log)
            db.commit()
            logger.info(f"Inbound call logged for lead {lead.id} ({lead.name})")
        else:
            logger.warning(f"Inbound call from unknown number: {From}")

        vr = VoiceResponse()
        vr.say(
            "Thanks for calling ecomdropship dot A I. We've noted your call "
            "and will follow up with full details by email shortly. Goodbye!"
        )
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    finally:
        db.close()