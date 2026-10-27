"""
whatsapp_api.py — The contract between the backend and the WhatsApp Chrome extension
------------------------------------------------------------------------------------
The backend decides what is sent and when; the extension only delivers.

    POST /whatsapp/heartbeat   "I am alive" every ~30 s, with the state of WhatsApp Web
    GET  /whatsapp/queue       messages the extension may send right now (it claims them)
    POST /whatsapp/report      the result of one send: sent | failed | not_on_whatsapp
    POST /whatsapp/inbound     a message a lead wrote to us
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import connections
import message_service as ms
import replies
import scheduling as sch
from database import OutreachMessage, get_db
from phones import find_lead_by_phone

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp extension"])


class Heartbeat(BaseModel):
    state: Literal["connected", "loading", "logged_out"]
    number: Optional[str] = None             # the WhatsApp number that is logged in, if known
    extension_version: Optional[str] = None


class Report(BaseModel):
    message_id: int
    status: Literal["sent", "failed", "not_on_whatsapp"]
    error: Optional[str] = Field(default=None, max_length=300)
    external_id: Optional[str] = Field(default=None, max_length=300)


class Inbound(BaseModel):
    phone: str = Field(min_length=5, max_length=30)
    text: str = Field(max_length=8000)
    message_id: str = Field(min_length=1, max_length=200)


@router.post("/heartbeat")
def heartbeat(body: Heartbeat, db: Session = Depends(get_db)):
    connections.record(db, "whatsapp", state=body.state,
                       detail={"number": body.number, "version": body.extension_version})
    return connections.whatsapp_summary(db)


@router.get("/queue")
def queue(limit: int = Query(1, ge=1, le=5), db: Session = Depends(get_db)):
    ms.release_stale_claims(db)
    return {"messages": ms.claim_whatsapp(db, limit=limit)}


@router.post("/report")
def report(body: Report, db: Session = Depends(get_db)):
    msg = db.get(OutreachMessage, body.message_id)
    if msg is None or msg.channel != "whatsapp":
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.status not in ("claimed", "approved"):
        return {"status": msg.status, "note": "Already handled"}      # a repeated report is harmless

    if body.status == "sent":
        ms.mark_sent(db, msg, body.external_id)
    elif body.status == "not_on_whatsapp":
        ms.mark_failed(db, msg, "This number is not on WhatsApp", permanent=True)
        try:
            sch.skip_step(db, msg.lead_id, note="Number is not on WhatsApp")
        except (LookupError, ValueError):
            pass
    else:
        ms.mark_failed(db, msg, body.error or "The extension could not send it")
    return {"status": msg.status}


@router.post("/inbound")
def inbound(body: Inbound, db: Session = Depends(get_db)):
    lead = find_lead_by_phone(db, body.phone)
    if lead is None:
        return {"matched": False, "recorded": False}
    recorded = replies.record_reply(db, lead, "whatsapp", f"wa:{body.message_id}", body.phone, body.text)
    return {"matched": True, "recorded": recorded, "lead_id": lead.id}
