"""
webhooks/instantly_webhook.py — Instantly Email Event Webhook
==============================================================
Instantly calls this endpoint automatically when:
  - A lead opens our email       → event: "email_opened"
  - A lead replies to our email  → event: "email_replied"
  - A lead unsubscribes          → event: "unsubscribed"

How webhooks work:
  - We register this URL in Instantly dashboard settings
  - When an email event happens, Instantly sends a POST request here
  - We update our database with the event data
  - Frontend shows real-time email tracking in the CRM

Run this alongside the main app — it shares the same database.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime
from typing import Optional

from database import (
    EmailLog, Lead, PipelineStage,
    SyncSessionLocal, LeadCRUD, DNCCrud
)
from scripts.dnc import scan_replies_for_opt_outs, check_opt_out_keywords
from utils.domain import extract_domain

router = APIRouter()


class InstantlyWebhookPayload(BaseModel):
    """
    Shape of the JSON body Instantly sends to our webhook.
    (Also used to test this endpoint manually from Swagger /docs.)
    """
    event: str
    lead_email: str
    campaign_id: Optional[str] = None
    reply_text: Optional[str] = None


def get_db():
    """Opens and closes a DB session for each webhook call."""
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.post("/webhooks/instantly", tags=["Webhooks"])
async def handle_instantly_webhook(payload: InstantlyWebhookPayload):
    """
    Receives all email events from Instantly.

    Instantly sends a JSON payload like:
    {
        "event": "email_opened",
        "lead_email": "owner@example.com",
        "campaign_id": "abc123",
        "reply_text": "..."   ← only on reply events
    }
    """
    event      = payload.event
    lead_email = payload.lead_email
    reply_text = payload.reply_text or ""

    if not event or not lead_email:
        raise HTTPException(status_code=400, detail="Missing event or lead_email")

    db = SyncSessionLocal()

    try:
        # Find the email log for this lead
        log = (
            db.query(EmailLog)
            .join(Lead)
            .filter(Lead.email == lead_email)
            .order_by(EmailLog.sent_at.desc())
            .first()
        )

        if not log:
            # Webhook received but no matching lead — log and ignore
            return {"status": "ignored", "reason": "no matching lead found"}

        lead = db.query(Lead).filter(Lead.id == log.lead_id).first()

        # ── Handle each event type ────────────────────────────────────────
        if event == "email_opened":
            log.email_opened = True
            log.opened_at    = datetime.utcnow()
            LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.EMAIL_OPENED)

        elif event == "email_replied":
            log.replied       = True
            log.replied_at    = datetime.utcnow()
            log.reply_content = reply_text
            log.is_read       = False
            LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.REPLIED)

            # Auto-add to DNC if reply contains opt-out keywords
            if check_opt_out_keywords(reply_text):
                domain = extract_domain(lead.website)
                DNCCrud.add_to_dnc(
                    db,
                    email=lead.email,
                    phone=lead.phone,
                    domain=domain,
                    reason=f"Auto opt-out from email reply: '{reply_text[:100]}'",
                )
                LeadCRUD.mark_do_not_contact(db, lead.id)

        elif event == "unsubscribed":
            log.unsubscribed = True
            domain = extract_domain(lead.website)
            DNCCrud.add_to_dnc(
                db,
                email=lead.email,
                phone=lead.phone,
                domain=domain,
                reason="Unsubscribed via Instantly",
            )
            LeadCRUD.mark_do_not_contact(db, lead.id)

        db.commit()
        return {"status": "ok", "event": event, "lead_email": lead_email}

    finally:
        db.close()