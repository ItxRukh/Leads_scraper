"""
lead_journey.py — Where is this lead, and what happens next?
-------------------------------------------------------------
One short answer per lead for the Leads page, so nobody has to guess what the system is doing:

    {"key": "draft", "label": "Draft waiting for you", "next": "Review it on the Outreach queue page."}

journeys() works on a whole page of leads with a handful of queries.
"""

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from database import EmailStatus, InboundMessage, Lead, LeadTier, OutreachMessage, OutreachQueueEntry

_REPLY_LABELS = {"interested": "Replied: interested", "question": "Replied: has a question",
                 "not_now": "Replied: not now", "other": "Replied"}


def _when(dt) -> str:
    return dt.strftime("%b %d, %H:%M UTC") if dt else "the planned time"


def _answer(key: str, label: str, nxt: str = "") -> dict:
    return {"key": key, "label": label, "next": nxt}


def _one(lead: Lead, entry, messages: list, replies: list, now: datetime) -> dict:
    attrs = lead.attributes or {}
    ghl = (attrs.get("ghl") or {}).get("status")
    check = (attrs.get("email_check") or {}).get("status")
    statuses = {m.status for m in messages}

    if lead.do_not_contact:
        return _answer("dnc", "Do not contact", "Blocked: nothing is sent to this lead.")
    if ghl == "sent":
        return _answer("ghl", "In GoHighLevel", "Your team continues there: SMS, call and booking.")
    if replies:
        label = replies[0].label or "other"
        text = _REPLY_LABELS.get(label, "Replied")
        if (attrs.get("ghl") or {}).get("status") == "failed":
            return _answer("replied", text, "GoHighLevel did not accept it: open the lead and send it again.")
        answered = (attrs.get("reply_sent") or {}).get("for") == replies[0].id
        if answered:
            return _answer("replied", text, "You answered it. The conversation continues in Instantly.")
        if label in ("interested", "question"):
            return _answer("replied", text, "Open the lead: a suggested answer is ready to edit and send.")
        return _answer("replied", text, "Nothing to do: the sequence has stopped.")
    if "handed_off" in statuses or (entry is not None and entry.sequence_status == "handed_off"):
        return _answer("instantly", "With Instantly", "Instantly sends the follow-ups and stops if the lead replies.")
    if "sent" in statuses:
        step = entry.next_step_key if entry is not None and entry.next_step_key else None
        return _answer("emailing", "Emailing", f"Next: {step}, on {_when(entry.next_due_at)}." if step else "The sequence has finished.")
    if "approved" in statuses or "claimed" in statuses:
        release = max((m.release_at for m in messages if m.status == "approved" and m.release_at), default=None)
        return _answer("approved", "Approved", f"Goes out on {_when(release)}, inside the lead's working hours.")
    if "draft" in statuses:
        return _answer("draft", "Draft waiting for you", "Review it on the Outreach queue page.")
    if entry is not None and entry.status == "queued" and entry.sequence_status == "active":
        return _answer("queued", "In the queue", "The AI writes the emails when the lead is due.")
    if entry is not None and entry.sequence_status == "stopped":
        return _answer("stopped", f"Stopped: {entry.stop_reason or 'sequence ended'}", "")

    if lead.audited_at is None:
        if not lead.website:
            return _answer("no_site", "No website to audit", "Add a website to check what it is missing.")
        return _answer("to_audit", "Waiting for the website audit", "Open the Import page and press \"Check websites and emails\".")
    if lead.tier == LeadTier.COLD:
        return _answer("cold", "Cold: not contacted", "There is little to fix on this site, so no emails are sent. Only Hot and Warm leads are contacted.")
    if not lead.email:
        return _answer("no_email", "Audited: no email address", "Add an email address to be able to contact this lead.")
    if lead.email_status == EmailStatus.INVALID or check == "invalid":
        return _answer("bad_email", "Email address not valid", "Find another address for this business.")
    if check is None:
        return _answer("to_check", "Audited: email not checked", "Open the Import page and press \"Check websites and emails\".")
    if check == "catch-all":
        return _answer("catch_all", "Email is a catch-all address", "Turn on \"send to catch-all addresses\" on the Integrations page to include it.")
    return _answer("ready", "Ready for the queue", "It joins the Outreach queue on the next round.")


def journeys(db: Session, leads: Iterable[Lead], now: datetime = None) -> dict[int, dict]:
    leads = list(leads)
    ids = [lead.id for lead in leads] or [0]
    now = now or datetime.utcnow()

    entries = {e.lead_id: e for e in db.query(OutreachQueueEntry).filter(OutreachQueueEntry.lead_id.in_(ids))}
    messages: dict[int, list] = {}
    for m in db.query(OutreachMessage).filter(OutreachMessage.lead_id.in_(ids), OutreachMessage.kind != "reply"):
        messages.setdefault(m.lead_id, []).append(m)
    replies: dict[int, list] = {}
    for r in db.query(InboundMessage).filter(InboundMessage.lead_id.in_(ids), InboundMessage.kind == "reply").order_by(InboundMessage.id.desc()):
        replies.setdefault(r.lead_id, []).append(r)

    return {lead.id: _one(lead, entries.get(lead.id), messages.get(lead.id, []), replies.get(lead.id, []), now) for lead in leads}
