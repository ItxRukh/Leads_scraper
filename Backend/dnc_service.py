"""
dnc_service.py — Putting a lead (or an address) on the do-not-contact list, completely
---------------------------------------------------------------------------------------
Blocking a lead does four things, whichever way it happens (a STOP reply, an unsubscribe event, or you pressing
"Do not contact"):

  1. the email, phone and website domain go on the DNC list, so no import or scrape can bring them back;
  2. the lead is marked do-not-contact and leaves every list of leads to contact;
  3. its place in the Outreach queue stops and unsent drafts are thrown away;
  4. if the lead was already handed to Instantly, it is removed from the campaign so no further email goes out.

Step 4 is best effort: if Instantly cannot be reached, the result says so and the lead is still blocked here.
"""

import re
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

import instantly_client
import scheduling as sch
from database import DNCCrud, DNCList, Lead, LeadCRUD, OutreachMessage, OutreachQueueEntry
from phones import phone_key
from utils.domain import extract_domain

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def instantly_lead_id(db: Session, lead: Lead) -> Optional[str]:
    row = db.query(OutreachMessage.external_id).filter(
        OutreachMessage.lead_id == lead.id, OutreachMessage.status.in_(("sent", "handed_off")),
        OutreachMessage.external_id.isnot(None)).order_by(OutreachMessage.id.desc()).all()
    return next((r[0] for r in row if _UUID.match(r[0] or "")), None)


def remove_from_instantly(db: Session, lead: Lead) -> str:
    """'removed', 'not_in_instantly', 'not_configured' or 'failed: <reason>'."""
    lead_id = instantly_lead_id(db, lead)
    if lead_id is None:
        return "not_in_instantly"
    if not instantly_client.configured():
        return "not_configured"
    try:
        instantly_client.delete_lead(lead_id)
        return "removed"
    except instantly_client.InstantlyError as e:
        logger.error(f"[dnc] could not remove lead {lead.id} from Instantly: {e}")
        return f"failed: {e}"


def block_lead(db: Session, lead: Lead, reason: str) -> dict:
    DNCCrud.add_to_dnc(db, email=lead.email, phone=lead.phone, domain=extract_domain(lead.website), reason=reason)
    LeadCRUD.mark_do_not_contact(db, lead.id)

    entry = db.query(OutreachQueueEntry).filter(OutreachQueueEntry.lead_id == lead.id).first()
    if entry is not None and entry.sequence_status != sch.STOPPED:
        sch._stop(entry, "do not contact")
    for msg in db.query(OutreachMessage).filter(OutreachMessage.lead_id == lead.id, OutreachMessage.status.in_(("draft", "approved"))):
        msg.status, msg.error = "discarded", "The lead is on the do-not-contact list"
    db.commit()
    return {"lead_id": lead.id, "instantly": remove_from_instantly(db, lead)}


def block_address(db: Session, email: Optional[str], phone: Optional[str], domain: Optional[str], reason: str) -> dict:
    """Add an address to the list and block every lead that matches it. Returns what happened."""
    matches: dict[int, Lead] = {}
    if email:
        for lead in db.query(Lead).filter(Lead.email.ilike(email.strip())):
            matches[lead.id] = lead
    if phone and len(phone_key(phone)) >= 10:
        for lead in db.query(Lead).filter(Lead.phone.isnot(None)):
            if phone_key(lead.phone) == phone_key(phone):
                matches[lead.id] = lead
    if domain:
        wanted = domain.strip().lower().removeprefix("www.")
        for lead in db.query(Lead).filter(Lead.website.isnot(None)):
            if extract_domain(lead.website) == wanted:
                matches[lead.id] = lead

    if not matches:
        DNCCrud.add_to_dnc(db, email=email, phone=phone, domain=domain, reason=reason)
    return {"blocked_leads": [block_lead(db, lead, reason) for lead in matches.values()]}


def unblock_address(db: Session, email: Optional[str], phone: Optional[str], domain: Optional[str]) -> int:
    """Take an entry off the list and free the leads that were blocked only by it. Returns how many leads were freed."""
    from database import PipelineStage

    freed = 0
    for lead in db.query(Lead).filter(Lead.do_not_contact == True):                 # noqa: E712
        same = (email and (lead.email or "").lower() == email.strip().lower()) or \
               (phone and phone_key(lead.phone) == phone_key(phone)) or \
               (domain and extract_domain(lead.website) == domain.strip().lower().removeprefix("www."))
        still_listed = DNCCrud.is_on_dnc(db, email=lead.email, phone=lead.phone, domain=extract_domain(lead.website))
        if same and not still_listed:
            lead.do_not_contact = False
            lead.pipeline_stage = PipelineStage.AUDITED if lead.audited_at else PipelineStage.SCRAPED
            freed += 1
    db.commit()
    return freed
