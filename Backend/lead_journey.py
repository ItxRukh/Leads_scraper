"""
lead_journey.py — Where is this lead, and what happens next?
-------------------------------------------------------------
One short answer per lead for the Leads page, so nobody has to guess what the system is doing:

    {"key": "draft", "label": "Draft waiting for you", "next": "Review it on the Approve emails page."}

journeys() works on a whole page of leads with a handful of queries.
"""

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from database import EmailStatus, InboundMessage, Lead, OutreachMessage

_REPLY_LABELS = {"interested": "Replied: interested", "question": "Replied: has a question",
                 "not_now": "Replied: not now", "other": "Replied"}


def _answer(key: str, label: str, nxt: str = "") -> dict:
    return {"key": key, "label": label, "next": nxt}


def _one(lead: Lead, messages: list, replies: list) -> dict:
    attrs = lead.attributes or {}
    check = (attrs.get("email_check") or {}).get("status")
    statuses = {m.status for m in messages}

    if lead.do_not_contact:
        return _answer("dnc", "Do not contact", "Blocked: nothing is sent to this lead.")
    if (attrs.get("ghl_contact") or {}).get("status") == "sent":
        return _answer("in_ghl", "In GoHighLevel", "GoHighLevel sends the emails and handles the replies.")
    if replies:
        label = _REPLY_LABELS.get(replies[0].label or "other", "Replied")
        answered = (attrs.get("reply_sent") or {}).get("for") == replies[0].id
        return _answer("replied", label, "You answered it." if answered else "Open the lead to read the reply.")
    if "sent" in statuses:
        return _answer("sent", "Email sent", "")
    if "approved" in statuses or "claimed" in statuses:
        return _answer("approved", "Approved", "It goes to GoHighLevel when it is released.")
    if "draft" in statuses:
        return _answer("draft", "Draft waiting for you", "Review it on the Approve emails page.")
    if statuses == {"discarded"}:
        return _answer("discarded", "Email discarded", "")

    if not lead.email:
        return _answer("no_email", "No email address", "Add an email address to be able to contact this lead.")
    if lead.email_status == EmailStatus.INVALID or check == "invalid":
        return _answer("bad_email", "Email address not valid", "Find another address for this business.")
    if lead.website and lead.audited_at is None:
        return _answer("to_audit", "Waiting for the website check", "Press \"Check websites and emails\" on the Import page.")
    if check is None:
        return _answer("to_check", "Email not checked", "Press \"Check websites and emails\" on the Import page.")
    return _answer("to_write", "Ready for its first email", "The first email is written next.")


def journeys(db: Session, leads: Iterable[Lead], now: datetime = None) -> dict[int, dict]:
    leads = list(leads)
    ids = [lead.id for lead in leads] or [0]

    messages: dict[int, list] = {}
    for m in db.query(OutreachMessage).filter(OutreachMessage.lead_id.in_(ids), OutreachMessage.kind != "reply"):
        messages.setdefault(m.lead_id, []).append(m)
    replies: dict[int, list] = {}
    for r in db.query(InboundMessage).filter(InboundMessage.lead_id.in_(ids), InboundMessage.kind == "reply").order_by(InboundMessage.id.desc()):
        replies.setdefault(r.lead_id, []).append(r)

    return {lead.id: _one(lead, messages.get(lead.id, []), replies.get(lead.id, [])) for lead in leads}
