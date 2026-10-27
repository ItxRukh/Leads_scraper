"""
messages_api.py — Review and approve the messages the system prepared
---------------------------------------------------------------------
    GET  /outreach-messages                  list (?status=draft|approved|claimed|sent|failed &channel=)
    PUT  /outreach-messages/{id}             edit a draft
    POST /outreach-messages/{id}/approve     approve a draft (it is released at its planned time)
    POST /outreach-messages/{id}/discard     throw it away; a new draft is written if the step is still due
    POST /outreach-messages/{id}/skip        throw it away and skip the lead's step
    POST /outreach-messages/approve-all      approve every draft (?channel=)
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

import message_service as ms
from database import get_db

router = APIRouter(prefix="/outreach-messages", tags=["Outreach Messages"])


class DraftEdit(BaseModel):
    subject: Optional[str] = None
    body: str


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("")
def list_messages(
    status: str = Query("draft", pattern="^(draft|approved|claimed|sent|failed|discarded)$"),
    channel: Optional[str] = Query(None, pattern="^(email|whatsapp)$"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    return {"items": ms.list_messages(db, status, channel, limit)}


@router.post("/approve-all")
def approve_all(channel: Optional[str] = Query(None, pattern="^(email|whatsapp)$"),
                db: Session = Depends(get_db)):
    return {"approved": ms.approve_all(db, channel)}


@router.put("/{message_id}")
def edit(message_id: int, body: DraftEdit, db: Session = Depends(get_db)):
    msg = _call(ms.update_draft, db, message_id, body.subject, body.body)
    return {"id": msg.id, "status": msg.status}


@router.post("/{message_id}/approve")
def approve(message_id: int, db: Session = Depends(get_db)):
    msg = _call(ms.approve, db, message_id)
    return {"id": msg.id, "status": msg.status,
            "release_at": msg.release_at.isoformat() + "Z" if msg.release_at else None}


@router.post("/{message_id}/discard")
def discard(message_id: int, db: Session = Depends(get_db)):
    return {"id": message_id, "status": _call(ms.discard, db, message_id).status}


@router.post("/{message_id}/skip")
def skip(message_id: int, db: Session = Depends(get_db)):
    return {"id": message_id, "status": _call(ms.skip, db, message_id).status}
