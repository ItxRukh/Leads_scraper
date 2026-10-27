"""
script_07_voice_agent.py — Phase 7: Custom Voice AI Agent (Twilio + Groq + ElevenLabs)
-------------------------------------------------------------------------------------------
Places automated, personalized AI voice calls to hot leads — WITHOUT using VAPI.
This is our own voice agent, built entirely on Twilio + Groq + ElevenLabs.

How it connects to webhooks/voice_webhook.py:
  1. This script tells Twilio "call this number" via Twilio's REST API.
  2. Twilio dials the lead's phone.
  3. When they answer, Twilio requests instructions from our own server:
        POST {PUBLIC_BASE_URL}/voice/answer/{lead_id}
     (that endpoint lives in webhooks/voice_webhook.py)
  4. From there, the live conversation is handled entirely by our webhook —
     this script's job is just to KICK OFF the call and log the attempt.

Requirements before running:
  - .env must have TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
  - .env must have PUBLIC_BASE_URL set to your ngrok URL (dev) or real domain (prod)
    e.g. PUBLIC_BASE_URL=https://abc123.ngrok-free.app
  - Your FastAPI server must be running and reachable at that URL.

Usage:
    python script_07_voice_agent.py --hot_leads_only
    python script_07_voice_agent.py --lead_id 42
    python script_07_voice_agent.py --min_score 70 --dry_run
"""

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
    Lead, CallLog, WhatsAppLog, PipelineStage,
    SyncSessionLocal, LeadCRUD, init_db
)

console = make_console()


# ─── Eligibility: SMS sent, no reply (funnel step 2 → 3) ─────────────────────

def get_sms_no_reply_leads(db) -> list:
    """
    Main auto-follow-up rule for calls: leads who got the SMS follow-up
    settings.SMS_FOLLOWUP_WAIT_DAYS+ days ago, with no reply, and haven't
    been called yet. This is the final Email → SMS → Call funnel step —
    only warm+ leads are worth the cost of a call.
    """
    cutoff = datetime.utcnow() - timedelta(days=settings.SMS_FOLLOWUP_WAIT_DAYS)
    called_lead_ids = [c.lead_id for c in db.query(CallLog.lead_id).all()]

    eligible_sms = db.query(WhatsAppLog).filter(
        WhatsAppLog.replied == False,
        WhatsAppLog.sent_at <= cutoff,
        ~WhatsAppLog.lead_id.in_(called_lead_ids),
    ).all()

    # Uses live Settings → Lead Scoring threshold (same source auditor.py /
    # email_verify.py use) instead of the static .env WARM_LEAD_MIN_SCORE,
    # which no longer reflects what's saved on the Settings page.
    from app_settings import get_score_thresholds
    _, warm_threshold = get_score_thresholds()

    leads = []
    seen_ids = set()
    for log in eligible_sms:
        if log.lead_id in seen_ids:
            continue
        lead = db.query(Lead).filter(
            Lead.id == log.lead_id,
            Lead.do_not_contact == False,
            Lead.phone != None,
            Lead.final_score >= warm_threshold,  # don't waste calls on cold leads
        ).first()
        if lead:
            leads.append(lead)
            seen_ids.add(log.lead_id)

    return leads


# ─── Eligibility: No email, hot score — call is the first touch ─────────────

def get_no_email_hot_first_touch_leads(db) -> list:
    """
    Smart First-Touch Routing (call side): a HOT lead with no email and no
    prior contact at all deserves the strongest channel immediately, rather
    than waiting for an SMS-no-reply step that would otherwise never fire
    (there's no email to seed the SMS step from either — see
    scripts.sms_outreach.get_no_email_first_touch_leads for that half).

    Kept separate from get_sms_no_reply_leads: this rule fires on hot score
    alone, with zero prior contact, whereas the normal call step only fires
    after an unanswered SMS.
    """
    from app_settings import get_score_thresholds
    hot_threshold, _ = get_score_thresholds()

    sms_sent_ids = [w.lead_id for w in db.query(WhatsAppLog.lead_id).all()]
    called_ids = [c.lead_id for c in db.query(CallLog.lead_id).all()]
    already_contacted_ids = set(sms_sent_ids) | set(called_ids)

    query = db.query(Lead).filter(
        Lead.do_not_contact == False,
        Lead.phone != None,
        Lead.phone != "",
        (Lead.email == None) | (Lead.email == ""),
        Lead.final_score >= hot_threshold,
    )
    if already_contacted_ids:
        query = query.filter(~Lead.id.in_(already_contacted_ids))

    return query.order_by(Lead.final_score.desc()).all()


# ─── Twilio Call Trigger ───────────────────────────────────────────────────────

def _get_twilio_client() -> Optional[TwilioClient]:
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio credentials not set — cannot place calls")
        return None
    return TwilioClient(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)


def create_twilio_call(lead: Lead) -> Optional[str]:
    """
    Places an outbound call via Twilio, pointing it at our own voice webhook
    (not VAPI). Twilio will fetch instructions from /voice/answer/{lead_id}
    the moment the call connects.

    Returns:
        Twilio Call SID if successful, None if failed.
    """
    if not lead.phone:
        logger.debug(f"No phone number for lead: {lead.name}")
        return None

    if not settings.PUBLIC_BASE_URL:
        logger.error(
            "PUBLIC_BASE_URL not set in .env — Twilio needs a public URL "
            "(ngrok in dev) to reach your voice webhook. Aborting call."
        )
        return None

    client = _get_twilio_client()
    if not client:
        return None

    answer_url = f"{settings.PUBLIC_BASE_URL}/voice/answer/{lead.id}"
    status_url = f"{settings.PUBLIC_BASE_URL}/voice/status/{lead.id}"

    logger.info(f"[DEBUG] PUBLIC_BASE_URL = '{settings.PUBLIC_BASE_URL}'")
    logger.info(f"[DEBUG] answer_url = '{answer_url}'")
    logger.info(f"[DEBUG] status_url = '{status_url}'")

    try:
        call = client.calls.create(
            to=lead.phone,
            from_=settings.TWILIO_PHONE_NUMBER,
            url=answer_url,                # Twilio fetches TwiML instructions from here
            status_callback=status_url,    # Twilio reports call outcome here
            status_callback_event=["completed"],
            timeout=25,                    # ring for 25s before giving up
        )
        logger.info(f"Twilio call created for {lead.name}: call_sid={call.sid}")
        return call.sid

    except Exception as e:
        logger.error(f"Twilio call error for {lead.name}: {e}")
        return None


# ─── Call Logger ─────────────────────────────────────────────────────────────

def log_call_attempt(db, lead: Lead, call_sid: Optional[str]) -> CallLog:
    """
    Saves a call attempt record immediately after dialing.
    The outcome is filled in later when /voice/status/{lead_id} fires.
    Reuses the existing 'vapi_call_id' column to store the Twilio Call SID
    (no database migration needed).
    """
    call_log = CallLog(
        lead_id=lead.id,
        vapi_call_id=call_sid,   # repurposed field: now holds Twilio's Call SID
        called_at=datetime.utcnow(),
    )
    db.add(call_log)

    LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.CALL_PLACED)

    db.commit()
    db.refresh(call_log)
    return call_log


# ─── Main Runner ──────────────────────────────────────────────────────────────

def run_voice_agent(
    lead_id: Optional[int] = None,
    min_score: int = None,
    hot_leads_only: bool = True,
    dry_run: bool = False,
    trigger: str = "score",
) -> None:
    """
    Main function — places calls to eligible leads using our own
    Twilio + Groq + ElevenLabs voice agent (no VAPI).

    Args:
        trigger: "score"    = old behaviour, call by score threshold (manual button)
                 "followup" = auto funnel mode — only call leads whose SMS
                              follow-up went unanswered (Email → SMS → Call)
                 "no_email_first_touch" = auto mode — hot leads with no email
                              and zero prior contact, called immediately
                              instead of waiting on a funnel that can't start
    """
    console.rule("[bold cyan]Phase 7: Custom Voice Agent (Twilio + Groq + ElevenLabs)")

    db = SyncSessionLocal()

    if lead_id:
        lead = LeadCRUD.get_by_id(db, lead_id)
        if not lead:
            console.print(f"[red]Lead ID {lead_id} not found[/red]")
            db.close()
            return
        if lead.do_not_contact:
            console.print(f"[red]Lead {lead.name} is on the Do-Not-Contact list — refusing to call.[/red]")
            db.close()
            return
        if not lead.phone:
            console.print(f"[red]Lead {lead.name} has no phone on file — cannot call.[/red]")
            db.close()
            return
        leads = [lead]
        threshold_label = f"lead #{lead_id}"

    elif trigger == "followup":
        leads = get_sms_no_reply_leads(db)
        threshold_label = f"SMS no-reply {settings.SMS_FOLLOWUP_WAIT_DAYS}d+, warm/hot only"

    elif trigger == "no_email_first_touch":
        leads = get_no_email_hot_first_touch_leads(db)
        threshold_label = "no email on file, hot score, first contact"

    else:
        # Uses live Settings → Lead Scoring thresholds instead of the static
        # .env HOT_LEAD_MIN_SCORE / WARM_LEAD_MIN_SCORE, which no longer
        # reflect what's saved on the Settings page.
        from app_settings import get_score_thresholds
        hot_threshold, warm_threshold = get_score_thresholds()
        threshold = min_score if min_score else (
            hot_threshold if hot_leads_only else warm_threshold
        )

        called_lead_ids = [c.lead_id for c in db.query(CallLog.lead_id).all()]

        leads = (
            db.query(Lead)
            .filter(
                Lead.final_score >= threshold,
                Lead.phone != None,
                Lead.phone != "",
                Lead.do_not_contact == False,
                ~Lead.id.in_(called_lead_ids),
            )
            .order_by(Lead.final_score.desc())
            .all()
        )
        threshold_label = f"score ≥ {min_score}" if min_score else ("hot leads only" if hot_leads_only else "warm+")

    if not leads:
        console.print("[yellow]No eligible leads found for voice calls.[/yellow]")
        return

    console.print(f"Found [bold]{len(leads)}[/bold] leads to call ({threshold_label})")

    if dry_run:
        console.print("[bold yellow]DRY RUN — No calls will actually be placed[/bold yellow]")

    if not settings.PUBLIC_BASE_URL and not dry_run:
        console.print(
            "[bold red]PUBLIC_BASE_URL is not set in .env![/bold red]\n"
            "[dim]Start ngrok (ngrok http 8000), copy the https URL it gives you, "
            "and set PUBLIC_BASE_URL=that_url in your .env file.[/dim]"
        )
        return

    called_count = failed_count = 0

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
        BarColumn(), TextColumn("{task.completed}/{task.total}"), console=console,
    ) as progress:
        task = progress.add_task("Placing calls...", total=len(leads))

        for lead in leads:
            progress.update(task, description=f"Calling: {lead.name[:35]}")

            if dry_run:
                console.print(f"\n[dim]Would call {lead.name} at {lead.phone}[/dim]")
                console.print(f"[dim]Webhook URL: {settings.PUBLIC_BASE_URL}/voice/answer/{lead.id}[/dim]")
                called_count += 1
                progress.advance(task)
                continue

            call_sid = create_twilio_call(lead)
            log_call_attempt(db, lead, call_sid)

            if call_sid:
                called_count += 1
                logger.info(f"✅ Called: {lead.name} ({lead.phone}) → Call SID: {call_sid}")
            else:
                failed_count += 1
                logger.warning(f"❌ Call failed: {lead.name}")

            time.sleep(2)  # small gap between calls
            progress.advance(task)

    console.rule("[bold green]Phase 7 Complete!")
    console.print(f"  ✅ Calls placed:  [bold green]{called_count}[/bold green]")
    console.print(f"  ❌ Failed:        [bold red]{failed_count}[/bold red]")
    console.print(f"\n[dim]Call outcomes are updated automatically via /voice/status webhook.[/dim]")
    console.print(f"[dim]Next step: python script_08_whatsapp.py --trigger auto[/dim]")

    db.close()


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ecomdropship.ai Custom Voice Agent")
    parser.add_argument("--hot_leads_only", action="store_true",
                        help="Only call hot leads (score >= HOT threshold)")
    parser.add_argument("--lead_id", type=int, help="Call a specific lead by ID")
    parser.add_argument("--min_score", type=int, help="Minimum score override")
    parser.add_argument("--dry_run", action="store_true",
                        help="Preview without placing calls")
    args = parser.parse_args()

    try:
        init_db()
    except Exception:
        pass

    run_voice_agent(
        lead_id=args.lead_id,
        min_score=args.min_score,
        hot_leads_only=args.hot_leads_only,
        dry_run=args.dry_run,
    )