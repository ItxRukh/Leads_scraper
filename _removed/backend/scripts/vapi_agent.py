"""
script_07_vapi_agent.py — Phase 7: VAPI Voice AI Agent
---------------------------------------------------------
Places automated, personalized AI voice calls to hot leads via VAPI.

VAPI is a Voice AI platform that:
  - Handles call dialing and phone infrastructure
  - Runs a conversational AI agent during the call
  - Records calls and provides transcripts
  - Sends webhooks with call outcomes

This script:
  1. Fetches all hot leads (score >= HOT threshold) that haven't been called
  2. Builds a personalized system prompt for each lead (using Groq)
  3. Creates a VAPI call via their API
  4. Logs the call attempt to the database
  5. VAPI webhook (in webhooks/ folder) handles the outcome when call ends

Usage:
    python script_07_vapi_agent.py --hot_leads_only
    python script_07_vapi_agent.py --lead_id 42
    python script_07_vapi_agent.py --min_score 70 --dry_run
"""

import argparse
import time
from datetime import datetime
from typing import Optional

import requests
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from utils.console import make_console
from config import settings
from database import (
    Lead, CallLog, PipelineStage,
    SyncSessionLocal, LeadCRUD, init_db
)
from utils.groq_helper import generate_vapi_system_prompt

console = make_console()


# ─── VAPI API Helpers ─────────────────────────────────────────────────────────

def _get_vapi_headers() -> dict:
    """Returns authorization headers for VAPI API calls."""
    return {
        "Authorization": f"Bearer {settings.VAPI_API_KEY}",
        "Content-Type": "application/json",
    }


def create_vapi_call(lead: Lead, system_prompt: str) -> Optional[str]:
    """
    Creates a new outbound call via VAPI API.
    
    VAPI call payload:
      - phoneNumber: Who to call
      - assistant: The AI agent config (voice, prompt, model settings)
    
    Args:
        lead: Lead object with phone number and details
        system_prompt: Personalized AI agent instructions for this specific lead
    
    Returns:
        VAPI call ID (string) if successful, None if failed
    """
    if not settings.VAPI_API_KEY:
        logger.warning("VAPI_API_KEY not set — skipping voice calls")
        return None

    if not lead.phone:
        logger.debug(f"No phone number for lead: {lead.name}")
        return None

    # Build the call payload
    payload = {
        "phoneNumber": {
            "number": lead.phone,
            "twilioAccountSid": settings.TWILIO_ACCOUNT_SID,
            "twilioAuthToken": settings.TWILIO_AUTH_TOKEN,
        },
        "assistant": {
            # Voice settings — using a natural-sounding voice
            "voice": {
                "provider": "11labs",   # ElevenLabs for natural voice
                "voiceId": "adam",      # Professional male voice
            },
            # The AI model that powers the conversation
            "model": {
                "provider": "groq",
                "model": settings.GROQ_MODEL,
                "systemPrompt": system_prompt,
                "temperature": 0.7,
            },
            # Call behavior settings
            "firstMessage": (
                f"Hi, may I please speak with "
                f"{lead.linkedin_name.split()[0] if lead.linkedin_name else 'the owner or manager'}?"
            ),
            "endCallMessage": "Thank you for your time! Have a great day.",
            "maxDurationSeconds": 300,  # Max 5 minute call
            "recordingEnabled": True,   # Record for review
        },
        # Metadata we can use to identify this call in VAPI webhooks
        "metadata": {
            "lead_id": str(lead.id),
            "lead_name": lead.name,
            "platform": "ecomdropship",
        },
    }

    try:
        response = requests.post(
            f"{settings.VAPI_BASE_URL}/call/phone",
            headers=_get_vapi_headers(),
            json=payload,
            timeout=15,
        )
        data = response.json()

        if response.status_code == 201 and data.get("id"):
            logger.info(f"VAPI call created for {lead.name}: call_id={data['id']}")
            return data["id"]
        else:
            logger.error(f"VAPI call failed for {lead.name}: {data}")
            return None

    except Exception as e:
        logger.error(f"VAPI API error for {lead.name}: {e}")
        return None


# ─── Call Logger ─────────────────────────────────────────────────────────────

def log_call_attempt(db, lead: Lead, vapi_call_id: Optional[str]) -> CallLog:
    """
    Saves a call attempt record to the database immediately after dialing.
    The outcome is updated later when the VAPI webhook fires.
    
    Args:
        db: Database session
        lead: The lead being called
        vapi_call_id: VAPI's ID for this call (used to match webhook later)
    
    Returns:
        CallLog database record
    """
    call_log = CallLog(
        lead_id=lead.id,
        vapi_call_id=vapi_call_id,
        called_at=datetime.utcnow(),
        # outcome is None until webhook updates it
    )
    db.add(call_log)

    # Update the lead's pipeline stage
    LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.CALL_PLACED)

    db.commit()
    db.refresh(call_log)
    return call_log


# ─── Main Runner ──────────────────────────────────────────────────────────────

def run_vapi_agent(
    lead_id: Optional[int] = None,
    min_score: int = None,
    hot_leads_only: bool = True,
    dry_run: bool = False,
) -> None:
    """
    Main function — places VAPI calls to eligible leads.
    
    Args:
        lead_id: Call a specific lead only
        min_score: Minimum score for leads to call (overrides hot_leads_only)
        hot_leads_only: If True, only call Hot leads (score >= HOT threshold)
        dry_run: If True, generate prompts but don't actually dial
    """
    console.rule("[bold cyan]Phase 7: VAPI Voice AI Agent")

    db = SyncSessionLocal()

    # ── Get leads to call ──────────────────────────────────────────────────
    if lead_id:
        lead = LeadCRUD.get_by_id(db, lead_id)
        leads = [lead] if lead else []
    else:
        # Determine score threshold
        threshold = min_score if min_score else (
            settings.HOT_LEAD_MIN_SCORE if hot_leads_only else settings.WARM_LEAD_MIN_SCORE
        )

        # Get leads that:
        # 1. Meet the score threshold
        # 2. Have a phone number
        # 3. Are not on DNC
        # 4. Haven't been called yet (no CallLog entry)
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

    if not leads:
        console.print("[yellow]No eligible leads found for voice calls.[/yellow]")
        return

    threshold_label = f"score ≥ {min_score}" if min_score else ("hot leads only" if hot_leads_only else "warm+")
    console.print(f"Found [bold]{len(leads)}[/bold] leads to call ({threshold_label})")

    if dry_run:
        console.print("[bold yellow]DRY RUN — Prompts generated but no calls will be placed[/bold yellow]")

    called_count = failed_count = skipped_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Placing calls...", total=len(leads))

        for lead in leads:
            progress.update(task, description=f"Calling: {lead.name[:35]}")

            # Build lead dict for Groq prompt generator
            lead_dict = {
                "name": lead.name,
                "city": lead.city,
                "niche": lead.niche,
                "linkedin_name": lead.linkedin_name,
                "dropship_mentioned": lead.dropship_mentioned,
                "sells_on_marketplace": lead.sells_on_marketplace,
            }

            # Generate personalized system prompt for this specific lead
            system_prompt = generate_vapi_system_prompt(lead_dict)

            if dry_run:
                console.print(f"\n[dim]--- Prompt preview for {lead.name} ---[/dim]")
                console.print(f"[dim]{system_prompt[:300]}...[/dim]\n")
                called_count += 1
                progress.advance(task)
                continue

            # Place the actual call
            vapi_call_id = create_vapi_call(lead, system_prompt)

            # Log the attempt
            log_call_attempt(db, lead, vapi_call_id)

            if vapi_call_id:
                called_count += 1
                logger.info(f"✅ Called: {lead.name} ({lead.phone}) → VAPI ID: {vapi_call_id}")
            else:
                failed_count += 1
                logger.warning(f"❌ Call failed: {lead.name}")

            # Rate limit: VAPI and phone carriers need a small gap between calls
            # 2 seconds between calls to avoid triggering spam detection
            time.sleep(2)
            progress.advance(task)

    console.rule("[bold green]Phase 7 Complete!")
    console.print(f"  ✅ Calls placed:  [bold green]{called_count}[/bold green]")
    console.print(f"  ❌ Failed:        [bold red]{failed_count}[/bold red]")
    console.print(f"\n[dim]Call outcomes are updated automatically when VAPI webhooks fire.[/dim]")
    console.print(f"[dim]Next step: python script_08_whatsapp.py --trigger auto[/dim]")

    db.close()


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ecomdropship.ai VAPI Voice Agent")
    parser.add_argument("--hot_leads_only", action="store_true",
                        help="Only call hot leads (score >= HOT threshold)")
    parser.add_argument("--lead_id", type=int, help="Call a specific lead by ID")
    parser.add_argument("--min_score", type=int, help="Minimum score override")
    parser.add_argument("--dry_run", action="store_true",
                        help="Generate prompts without placing calls")
    args = parser.parse_args()

    try:
        init_db()
    except Exception:
        pass

    run_vapi_agent(
        lead_id=args.lead_id,
        min_score=args.min_score,
        hot_leads_only=args.hot_leads_only,
        dry_run=args.dry_run,
    )
