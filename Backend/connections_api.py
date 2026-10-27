"""
connections_api.py — Status of the email connection (Instantly or Gmail)
--------------------------------------------------------------------
    GET  /connections              the email status, for the status chip and warning banner
    POST /connections/email/test   log in to the mail server now and report what happened
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import connections
import email_inbox
import email_sender
from database import get_db

router = APIRouter(prefix="/connections", tags=["Connections"])


@router.get("")
def get_connections(db: Session = Depends(get_db)):
    return {"email": connections.email_summary(db)}


@router.post("/email/test")
def test_email(db: Session = Depends(get_db)):
    smtp_ok, smtp_error = email_sender.test_smtp()
    imap_ok, imap_error = email_inbox.test_imap()
    error = smtp_error or imap_error
    if error:
        connections.record(db, "email", state="error", error=error)
    else:
        connections.record(db, "email", state="ok", ok=True)
    return {"ok": smtp_ok and imap_ok,
            "sending": {"ok": smtp_ok, "error": smtp_error},
            "reading_replies": {"ok": imap_ok, "error": imap_error}}
