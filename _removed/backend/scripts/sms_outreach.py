import argparse
import time
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from twilio.rest import Client as TwilioClient

from utils.console import make_console
from config import settings
from database import (
    Lead, WhatsAppLog, EmailLog, CallLog, CallOutcome, PipelineStage,
    SyncSessionLocal, LeadCRUD, init_db
)
from utils.groq_helper import generate_sms_message
from utils.template import apply_template_vars

console = make_console()

# ─── Timing Rules ─────────────────────────────────────────────────────────────
EMAIL_OPEN_WAIT_HOURS    = 48   # How many hours to wait after email is opened
CALL_NO_ANSWER_WAIT_HOURS = 24  # How many hours to wait after a missed call


# ─── Twilio Client ────────────────────────────────────────────────────────────

def _get_twilio_client() -> Optional[TwilioClient]:
    """Creates the Twilio client. Returns None if credentials are not set."""
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio credentials not set — SMS disabled")
        return None
    return TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


# ─── SMS Number Formatter ─────────────────────────────────────────────────────

def _format_phone(raw_phone: str) -> str:
    """
    Converts a phone number to E.164 format.
    Example: "(305) 123-4567" → "+13051234567"

    Unlike WhatsApp, no "whatsapp:" prefix is added here.
    """
    phone = raw_phone.strip()
    if phone.startswith("+"):
        return phone
    # Assume a domestic US number if there's no "+"
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return f"+{digits}"  # Best effort


# ─── SMS Sender ───────────────────────────────────────────────────────────────

def send_sms(lead: Lead, message: str, dry_run: bool = False) -> Optional[str]:
    """
    Sends an SMS to the lead via Twilio.

    WhatsApp vs SMS difference:
      WhatsApp: from_="whatsapp:+14155238886", to="whatsapp:+13051234567"
      SMS:      from_="+14155238886",          to="+13051234567"

    Args:
        lead:    Target lead object
        message: SMS text (160 chars ideal, 1600 max)
        dry_run: If True, only print a preview — don't actually send

    Returns:
        Twilio message SID if successful, otherwise None
    """
    if not lead.phone:
        logger.debug(f"No phone for lead: {lead.name}")
        return None

    to_number = _format_phone(lead.phone)

    if dry_run:
        console.print(f"\n[dim]--- SMS preview for {lead.name} ---[/dim]")
        console.print(f"To:      {to_number}")
        console.print(f"Message: {message}\n")
        return "dry_run_sid"

    client = _get_twilio_client()
    if not client:
        return None

    # SMS sender number — set TWILIO_SMS_NUMBER in .env if you have one
    # Falls back to TWILIO_PHONE_NUMBER (the same number used for calls),
    # since that's a real, owned, SMS-capable number.
    from_number = getattr(settings, "TWILIO_SMS_NUMBER", None)
    if not from_number:
        from_number = settings.TWILIO_PHONE_NUMBER
    if not from_number:
        # Last resort fallback: strip "whatsapp:" off the WhatsApp sandbox number
        # (this is only provisioned for WhatsApp, won't work for real SMS)
        wa_num = getattr(settings, "TWILIO_WHATSAPP_NUMBER", "")
        from_number = wa_num.replace("whatsapp:", "").strip()

    try:
        msg = client.messages.create(
            from_=from_number,
            to=to_number,
            body=message,
        )
        logger.info(f"SMS sent to {lead.name} ({to_number}): SID={msg.sid}")
        return msg.sid

    except Exception as e:
        logger.error(f"Twilio SMS error for {lead.name}: {e}")
        return None


# ─── Database Logging ─────────────────────────────────────────────────────────

def log_sms(db, lead: Lead, message: str, twilio_sid: Optional[str]) -> WhatsAppLog:
    """
    Saves the SMS to the database.
    Reuses the WhatsAppLog table (differentiate by channel = 'sms').
    If you want a separate SMSLog table, add it in database.py.
    """
    log = WhatsAppLog(
        lead_id=lead.id,
        twilio_message_id=twilio_sid,
        message_content=message,
        delivery_status="sent" if twilio_sid else "failed",
        sent_at=datetime.utcnow(),
        # Note: if WhatsAppLog has a 'channel' column, set channel="sms"
    )
    db.add(log)

    if twilio_sid:
        LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.WHATSAPP_SENT)

    db.commit()
    return log


# ─── Eligibility: Email sent, no reply (main funnel step 1 → 2) ──────────────

def get_email_sent_no_reply_leads(db) -> list:
    """
    Main auto-follow-up rule: leads whose email was SENT (opened or not)
    settings.EMAIL_FOLLOWUP_WAIT_DAYS+ days ago, with no reply, and no SMS
    sent yet. This is the primary Email → SMS funnel step.
    """
    from config import settings as _settings
    cutoff = datetime.utcnow() - timedelta(days=_settings.EMAIL_FOLLOWUP_WAIT_DAYS)
    sms_sent_ids = [w.lead_id for w in db.query(WhatsAppLog.lead_id).all()]

    eligible_logs = db.query(EmailLog).filter(
        EmailLog.replied == False,
        EmailLog.sent_at <= cutoff,
        ~EmailLog.lead_id.in_(sms_sent_ids),
    ).all()

    leads = []
    seen_ids = set()
    for log in eligible_logs:
        if log.lead_id in seen_ids:
            continue
        lead = db.query(Lead).filter(
            Lead.id == log.lead_id,
            Lead.do_not_contact == False,
            Lead.phone != None,
        ).first()
        if lead:
            leads.append((lead, "email_no_reply"))
            seen_ids.add(log.lead_id)

    return leads


# ─── Eligibility: Email opened, no reply ──────────────────────────────────────

def get_email_opened_no_reply_leads(db) -> list:
    """
    Leads who opened the email but didn't reply (48h+ ago).
    Secondary signal — kept for leads who open fast but the 3-day
    "sent" rule above hasn't caught yet (opened+engaged is a stronger sign).
    """
    cutoff = datetime.utcnow() - timedelta(hours=EMAIL_OPEN_WAIT_HOURS)
    sms_sent_ids = [w.lead_id for w in db.query(WhatsAppLog.lead_id).all()]

    eligible_logs = db.query(EmailLog).filter(
        EmailLog.email_opened == True,
        EmailLog.replied == False,
        EmailLog.opened_at <= cutoff,
        ~EmailLog.lead_id.in_(sms_sent_ids),
    ).all()

    leads = []
    for log in eligible_logs:
        lead = db.query(Lead).filter(
            Lead.id == log.lead_id,
            Lead.do_not_contact == False,
            Lead.phone != None,
        ).first()
        if lead:
            leads.append((lead, "email_followup"))

    return leads


# ─── Eligibility: Call no answer ──────────────────────────────────────────────

def get_call_no_answer_leads(db) -> list:
    """
    Leads who were called but got No Answer/Voicemail (24h+ ago).
    """
    cutoff = datetime.utcnow() - timedelta(hours=CALL_NO_ANSWER_WAIT_HOURS)
    sms_sent_ids = [w.lead_id for w in db.query(WhatsAppLog.lead_id).all()]

    eligible_calls = db.query(CallLog).filter(
        CallLog.outcome.in_([CallOutcome.NO_ANSWER, CallOutcome.VOICEMAIL]),
        CallLog.called_at <= cutoff,
        ~CallLog.lead_id.in_(sms_sent_ids),
    ).all()

    leads = []
    for call in eligible_calls:
        lead = db.query(Lead).filter(
            Lead.id == call.lead_id,
            Lead.do_not_contact == False,
            Lead.phone != None,
        ).first()
        if lead:
            leads.append((lead, "call_no_answer"))

    return leads


# ─── Eligibility: No email at all — SMS is the first touch ──────────────────

def get_no_email_first_touch_leads(db) -> list:
    """
    Smart First-Touch Routing: leads that have NO email on file but DO have
    a phone number were previously stuck forever — the Email → SMS → Call
    funnel only triggers off an email being sent, so a phone-only lead
    never got contacted at all (see scripts/outreach_reason.py's old
    "stuck_no_trigger" status).

    This rule finds exactly those leads and sends them a first-touch SMS
    directly, instead of waiting on an email that will never be sent.
    Only warm+ leads are contacted, same bar as every other outreach channel.
    """
    from app_settings import get_score_thresholds
    _, warm_threshold = get_score_thresholds()

    sms_sent_ids = [w.lead_id for w in db.query(WhatsAppLog.lead_id).all()]
    called_ids = [c.lead_id for c in db.query(CallLog.lead_id).all()]
    already_contacted_ids = set(sms_sent_ids) | set(called_ids)

    query = db.query(Lead).filter(
        Lead.do_not_contact == False,
        Lead.phone != None,
        Lead.phone != "",
        (Lead.email == None) | (Lead.email == ""),
        Lead.final_score >= warm_threshold,
    )
    if already_contacted_ids:
        query = query.filter(~Lead.id.in_(already_contacted_ids))

    leads = query.all()

    return [(lead, "no_email_first_touch") for lead in leads]


# ─── Main Runner ──────────────────────────────────────────────────────────────

def run_sms_followup(
    lead_id: Optional[int] = None,
    trigger: str = "auto",
    dry_run: bool = False,
    message: Optional[str] = None,
) -> None:
    """
    Runs SMS follow-ups for eligible leads.

    Args:
        lead_id: Send SMS to only one specific lead
        trigger: "auto" = check all eligibility rules
        dry_run: If True, preview only — don't actually send
        message: Settings → Templates → "Custom" mode on the SMS page
            compose modal. If set, this exact template (with
            {{name}}/{{business}}/{{city}}/{{niche}} filled in per lead)
            is sent instead of a Groq-generated SMS. Only meaningful
            together with lead_id (a one-off custom send) — batch/auto
            follow-ups always use the Groq-generated message.
    """
    console.rule("[bold cyan]Phase 8B: SMS Follow-Up")

    db = SyncSessionLocal()
    leads_with_context: list = []

    if lead_id:
        lead = LeadCRUD.get_by_id(db, lead_id)
        if not lead:
            console.print(f"[red]Lead ID {lead_id} not found[/red]")
            db.close()
            return
        if lead.do_not_contact:
            console.print(f"[red]Lead {lead.name} is on the Do-Not-Contact list — refusing to send.[/red]")
            db.close()
            return
        if not lead.phone:
            console.print(f"[red]Lead {lead.name} has no phone on file — cannot send SMS.[/red]")
            db.close()
            return
        # Manual "Contact Now" send — use whichever context actually applies
        # instead of hardcoding "email_followup" (which would produce a
        # message referencing an email that may never have been sent).
        manual_context = "no_email_first_touch" if not lead.email else "manual"
        leads_with_context.append((lead, manual_context))

    elif trigger == "auto":
        console.print("Checking eligibility rules...")
        sent_leads   = get_email_sent_no_reply_leads(db)   # main funnel: email → 3d → sms
        email_leads  = get_email_opened_no_reply_leads(db)  # secondary: opened but no reply, 48h
        call_leads   = get_call_no_answer_leads(db)         # secondary: missed call, 24h
        first_touch_leads = get_no_email_first_touch_leads(db)  # smart routing: no email, phone only

        # Combine + dedupe (a lead might match more than one rule — send once)
        seen_ids = set()
        leads_with_context = []
        for lead, context in sent_leads + email_leads + call_leads + first_touch_leads:
            if lead.id in seen_ids:
                continue
            leads_with_context.append((lead, context))
            seen_ids.add(lead.id)

        console.print(f"  → Email sent, no reply ({settings.EMAIL_FOLLOWUP_WAIT_DAYS}d+): [bold]{len(sent_leads)}[/bold] leads")
        console.print(f"  → Email opened, no reply (48h+):     [bold]{len(email_leads)}[/bold] leads")
        console.print(f"  → Call no answer (24h+):             [bold]{len(call_leads)}[/bold] leads")
        console.print(f"  → No email, phone only (first touch):[bold]{len(first_touch_leads)}[/bold] leads")
        console.print(f"  → Total (deduped):                   [bold]{len(leads_with_context)}[/bold] leads")

    if not leads_with_context:
        console.print("[yellow]No leads eligible for SMS right now.[/yellow]")
        return

    console.print(f"\n[bold]{len(leads_with_context)}[/bold] leads — sending SMS...")

    if dry_run:
        console.print("[bold yellow]DRY RUN — preview only, nothing will be sent[/bold yellow]")

    sent_count = failed_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Sending SMS...", total=len(leads_with_context))

        for lead, context in leads_with_context:
            progress.update(task, description=f"SMS: {lead.name[:35]}")

            lead_dict = {
                "name": lead.name,
                "linkedin_name": lead.linkedin_name,
                "niche": lead.niche,
            }

            # Use the user's own Settings → Templates text if a custom
            # message was passed in (SMS page → "Custom" mode); otherwise
            # fall back to the existing Groq-generated message, exactly
            # as before. (Loop uses `final_message` — not `message` — so
            # the custom-template check below still sees the original
            # parameter on every iteration instead of the last lead's
            # generated text.)
            if message:
                final_message = apply_template_vars(message, lead.name, city="", niche=lead.niche, decision_maker=lead.linkedin_name)
            else:
                final_message = generate_sms_message(lead_dict, context=context)

            # Send the SMS
            sid = send_sms(lead, final_message, dry_run=dry_run)

            # Log to database
            if not dry_run:
                log_sms(db, lead, final_message, sid)

            if sid:
                sent_count += 1
            else:
                failed_count += 1

            time.sleep(0.5)  # Twilio rate limit
            progress.advance(task)

    console.rule("[bold green]Phase 8B Complete!")
    console.print(f"  ✅ SMS sent:  [bold green]{sent_count}[/bold green]")
    console.print(f"  ❌ Failed:    [bold red]{failed_count}[/bold red]")

    db.close()


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ecomdropship.ai SMS Follow-Up")
    parser.add_argument("--trigger", type=str, default="auto",
                        choices=["auto"],
                        help="Trigger mode")
    parser.add_argument("--lead_id", type=int, help="Send SMS to a specific lead")
    parser.add_argument("--dry_run", action="store_true",
                        help="Preview only, don't send")
    args = parser.parse_args()

    try:
        init_db()
    except Exception:
        pass

    run_sms_followup(
        lead_id=args.lead_id,
        trigger=args.trigger,
        dry_run=args.dry_run,
    )