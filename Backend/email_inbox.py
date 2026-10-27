"""
email_inbox.py — Reads replies and bounces from the Gmail inbox (IMAP)
----------------------------------------------------------------------
Run every few minutes. The first run looks at the last 14 days; after that only
mail newer than the last one seen (tracked by IMAP UID). Mail that is not from a
lead, and automatic out-of-office answers, are ignored.
"""

import imaplib
import re
from datetime import datetime, timedelta
from email import message_from_bytes
from email.message import Message
from email.utils import parseaddr
from typing import Optional

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

import connections
import replies
from config import settings
from database import ConnectionStatus, Lead, OutreachMessage

FIRST_RUN_DAYS = 14
MAX_PER_POLL = 200
_ADDRESS_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")


def _connect() -> imaplib.IMAP4_SSL:
    imap = imaplib.IMAP4_SSL(settings.EMAIL_IMAP_HOST, timeout=30)
    try:
        imap.login(settings.EMAIL_ADDRESS, settings.EMAIL_APP_PASSWORD)
    except Exception:
        try:
            imap.logout()
        except Exception:
            pass
        raise
    return imap


def test_imap() -> tuple[bool, Optional[str]]:
    if not (settings.EMAIL_ADDRESS and settings.EMAIL_APP_PASSWORD):
        return False, "Set EMAIL_ADDRESS and EMAIL_APP_PASSWORD in Backend/.env"
    try:
        imap = _connect()
    except imaplib.IMAP4.error:
        return False, "Gmail refused the login. Check the address and the App Password (and that IMAP is on)."
    except Exception as e:
        return False, f"Could not reach the mail server: {e}"
    imap.logout()
    return True, None


# ─── Reading one message ──────────────────────────────────────────────────────

def _body_text(msg: Message) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if part.get_content_type() == "text/plain" and not part.get_filename():
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    return ""


def strip_quoted(text: str) -> str:
    """Keep what the person wrote, drop the quoted earlier message and signature separators."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(">") or re.match(r"^On .+ wrote:$", stripped) or stripped == "--":
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _is_bounce(msg: Message) -> bool:
    sender = parseaddr(msg.get("From", ""))[1].lower()
    return ("mailer-daemon" in sender or "postmaster" in sender
            or msg.get_content_type() == "multipart/report")


def _is_auto_reply(msg: Message) -> bool:
    if msg.get("Auto-Submitted", "no").lower() != "no" or msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return True
    subject = (msg.get("Subject") or "").lower()
    return subject.startswith(("automatic reply", "auto:", "out of office", "autoreply"))


def _failed_recipient(msg: Message, text: str) -> Optional[str]:
    header = msg.get("X-Failed-Recipients")
    if header:
        return parseaddr(header)[1].lower() or None
    own = settings.EMAIL_ADDRESS.lower()
    for address in _ADDRESS_RE.findall(text):
        if address.lower() != own and "mailer-daemon" not in address.lower():
            return address.lower()
    return None


def _lead_by_email(db: Session, address: str) -> Optional[Lead]:
    if not address:
        return None
    return db.query(Lead).filter(func.lower(Lead.email) == address.lower()).first()


def _lead_by_thread(db: Session, msg: Message) -> Optional[Lead]:
    refs = f"{msg.get('In-Reply-To', '')} {msg.get('References', '')}".split()
    if not refs:
        return None
    sent = db.query(OutreachMessage).filter(OutreachMessage.external_id.in_(refs)).first()
    return sent.lead if sent else None


def handle_message(db: Session, msg: Message, now: Optional[datetime] = None) -> Optional[str]:
    """Returns "reply", "bounce", or None when the message is not about one of our leads."""
    key = (msg.get("Message-ID") or "").strip()
    if not key:
        return None

    if _is_bounce(msg):
        text = _body_text(msg) + " " + " ".join(
            p.as_string() for p in msg.walk() if p.get_content_type() == "message/delivery-status")
        address = _failed_recipient(msg, text)
        lead = _lead_by_email(db, address) if address else None
        return "bounce" if lead and replies.record_bounce(db, lead, key, text) else None

    if _is_auto_reply(msg):
        return None

    sender = parseaddr(msg.get("From", ""))[1].lower()
    lead = _lead_by_email(db, sender) or _lead_by_thread(db, msg)
    if lead is None:
        return None
    text = strip_quoted(_body_text(msg)) or (msg.get("Subject") or "")
    return "reply" if replies.record_reply(db, lead, "email", key, sender, text, now) else None


# ─── Polling ──────────────────────────────────────────────────────────────────

def poll_inbox(db: Session, now: Optional[datetime] = None) -> dict:
    """Checks the inbox once. Returns {"replies": n, "bounces": n}; failures are recorded, not raised."""
    now = now or datetime.utcnow()
    counts = {"replies": 0, "bounces": 0}
    if not (settings.EMAIL_ADDRESS and settings.EMAIL_APP_PASSWORD):
        return counts

    state = db.get(ConnectionStatus, "email")
    last_uid = (state.detail or {}).get("imap_last_uid") if state else None

    try:
        imap = _connect()
        try:
            imap.select("INBOX", readonly=True)
            if last_uid is None:
                since = (now - timedelta(days=FIRST_RUN_DAYS)).strftime("%d-%b-%Y")
                _, data = imap.uid("search", None, "SINCE", since)
            else:
                _, data = imap.uid("search", None, "UID", f"{int(last_uid) + 1}:*")
            uids = sorted(int(u) for u in data[0].split() if last_uid is None or int(u) > int(last_uid))
            uids = uids[-MAX_PER_POLL:]

            for uid in uids:
                _, fetched = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
                if raw is None:
                    continue
                outcome = handle_message(db, message_from_bytes(raw), now)
                if outcome == "reply":
                    counts["replies"] += 1
                elif outcome == "bounce":
                    counts["bounces"] += 1
        finally:
            imap.logout()
    except Exception as e:
        logger.error(f"Inbox check failed: {e}")
        connections.record(db, "email", state="error", error=f"Inbox check failed: {e}", now=now)
        return counts

    newest = max(uids) if uids else last_uid
    connections.record(db, "email", state="ok", ok=True, now=now,
                       detail={"imap_last_uid": newest} if newest is not None else None)
    if counts["replies"] or counts["bounces"]:
        logger.info(f"Inbox: {counts['replies']} repl(ies), {counts['bounces']} bounce(s)")
    return counts
