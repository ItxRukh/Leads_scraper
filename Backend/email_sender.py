"""
email_sender.py — Sends email through the configured Gmail account (SMTP)
-------------------------------------------------------------------------
Uses an App Password, so there is no Google Cloud setup. Every message carries a
List-Unsubscribe header, and follow-ups are threaded under the first email.
"""

import html
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional

from config import settings


class EmailNotConfigured(Exception):
    pass


def is_configured() -> bool:
    return bool(settings.EMAIL_ADDRESS and settings.EMAIL_APP_PASSWORD)


def sender_address() -> str:
    return settings.EMAIL_ADDRESS


def new_tracking_token() -> str:
    return secrets.token_urlsafe(16)


def public_base_url() -> str:
    return (settings.PUBLIC_BASE_URL or "").rstrip("/")


def is_public_address(url: str) -> bool:
    """Open tracking only works if the lead's mail app can reach the address."""
    host = url.split("://", 1)[-1].split("/", 1)[0].split(":")[0].lower()
    return bool(host) and host not in ("localhost", "0.0.0.0") and not host.startswith(("127.", "192.168.", "10.")) \
        and not host.endswith(".local")


def tracking_ready() -> bool:
    """Tracking is switched on and PUBLIC_BASE_URL is an address that can be reached from outside."""
    return bool(settings.EMAIL_OPEN_TRACKING and is_public_address(public_base_url()))


def _html_body(body: str, token: str) -> str:
    paragraphs = "".join(f"<p>{html.escape(part).replace(chr(10), '<br>')}</p>" for part in body.split("\n\n"))
    pixel = f'<img src="{public_base_url()}/track/open/{token}.gif" width="1" height="1" alt="" style="border:0">'
    return f"<html><body>{paragraphs}{pixel}</body></html>"


def build_message(to: str, subject: str, body: str, reply_to_message_id: Optional[str] = None,
                  tracking_token: Optional[str] = None) -> EmailMessage:
    """
    Plain-text message. `reply_to_message_id` threads it under an earlier email. With a
    tracking token (and tracking switched on and reachable) an HTML copy with the pixel is added.
    """
    msg = EmailMessage()
    msg["From"] = formataddr((settings.EMAIL_FROM_NAME, settings.EMAIL_ADDRESS))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings.EMAIL_ADDRESS.split("@")[-1])
    msg["List-Unsubscribe"] = f"<mailto:{settings.EMAIL_ADDRESS}?subject=STOP>"
    if reply_to_message_id:
        msg["In-Reply-To"] = reply_to_message_id
        msg["References"] = reply_to_message_id
    msg.set_content(body)
    if tracking_token and tracking_ready():
        msg.add_alternative(_html_body(body, tracking_token), subtype="html")
    return msg


def _connect() -> smtplib.SMTP:
    if not is_configured():
        raise EmailNotConfigured("Set EMAIL_ADDRESS and EMAIL_APP_PASSWORD in Backend/.env")
    server = smtplib.SMTP(settings.EMAIL_SMTP_HOST, settings.EMAIL_SMTP_PORT, timeout=30)
    try:
        server.starttls(context=ssl.create_default_context())
        server.login(settings.EMAIL_ADDRESS, settings.EMAIL_APP_PASSWORD)
    except Exception:
        server.close()
        raise
    return server


def send_email(to: str, subject: str, body: str, reply_to_message_id: Optional[str] = None,
               tracking_token: Optional[str] = None) -> str:
    """Sends one email and returns its Message-ID."""
    msg = build_message(to, subject, body, reply_to_message_id, tracking_token)
    server = _connect()
    try:
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except smtplib.SMTPException:
            server.close()
    return msg["Message-ID"]


def test_smtp() -> tuple[bool, Optional[str]]:
    """Logs in without sending anything. Returns (ok, error text)."""
    try:
        server = _connect()
    except EmailNotConfigured as e:
        return False, str(e)
    except smtplib.SMTPAuthenticationError:
        return False, "Gmail refused the login. Check the address and the App Password."
    except Exception as e:
        return False, f"Could not reach the mail server: {e}"
    server.quit()
    return True, None
