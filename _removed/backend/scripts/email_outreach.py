"""
script_05_email_outreach.py — Phase 5: Cold Email Outreach via Instantly
Generates personalized emails with Claude, pushes to Instantly campaign.
Usage:
    python script_05_email_outreach.py --min_score 40
    python script_05_email_outreach.py --min_score 80 --dry_run
"""

import argparse
import json
from datetime import datetime
from typing import List, Optional

import requests
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from utils.console import make_console
from config import settings
from database import (
    Lead, EmailLog, PipelineStage,
    SyncSessionLocal, LeadCRUD, init_db
)
from utils.groq_helper import generate_cold_email
from utils.template import apply_template_vars

console = make_console()


# ─── Instantly API Helpers ────────────────────────────────────────────────────

def get_instantly_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.INSTANTLY_API_KEY}",
        "Content-Type": "application/json",
    }


def create_or_get_campaign(campaign_name: str) -> Optional[str]:
    """Create a new Instantly campaign or return existing one by name"""
    if not settings.INSTANTLY_API_KEY:
        logger.warning("INSTANTLY_API_KEY not set")
        return None

    # List existing campaigns
    url = f"{settings.INSTANTLY_BASE_URL}/campaign/list"
    try:
        response = requests.get(url, headers=get_instantly_headers(),
                                params={"limit": 100}, timeout=10)
        campaigns = response.json()

        for camp in campaigns.get("data", []):
            if camp.get("name") == campaign_name:
                logger.info(f"Reusing existing Instantly campaign: {camp['id']}")
                return camp["id"]

        # Create new campaign
        create_url = f"{settings.INSTANTLY_BASE_URL}/campaign/create"
        payload = {
            "name": campaign_name,
            "daily_limit": settings.EMAILS_PER_DAY,
            "sending_speed": 5,
        }
        resp = requests.post(create_url, headers=get_instantly_headers(),
                             json=payload, timeout=10)
        campaign_id = resp.json().get("id")
        logger.info(f"Created new Instantly campaign: {campaign_id}")
        return campaign_id

    except Exception as e:
        logger.error(f"Instantly campaign error: {e}")
        return None


def push_lead_to_instantly(campaign_id: str, lead: Lead, email_data: dict) -> Optional[str]:
    """
    Add a single lead to Instantly campaign with personalized email.
    Returns Instantly lead ID or None on failure.
    """
    url = f"{settings.INSTANTLY_BASE_URL}/lead/add"
    payload = {
        "campaign_id": campaign_id,
        "email": lead.email,
        "first_name": (lead.linkedin_name or lead.name).split()[0],
        "last_name": " ".join((lead.linkedin_name or lead.name).split()[1:]),
        "company_name": lead.name,
        "custom_variables": {
            "city": lead.city or "",
            "niche": lead.niche or "",
            "website": lead.website or "",
            "lead_score": str(lead.final_score),
            "email_subject": email_data.get("subject", ""),
            "email_body": email_data.get("body", ""),
        }
    }

    try:
        response = requests.post(url, headers=get_instantly_headers(),
                                 json=payload, timeout=10)
        data = response.json()
        if data.get("status") == "success":
            return data.get("lead_id")
        else:
            logger.error(f"Instantly push failed for {lead.name}: {data}")
            return None
    except Exception as e:
        logger.error(f"Instantly API error for {lead.name}: {e}")
        return None


# ─── Main Outreach Runner ─────────────────────────────────────────────────────

def run_email_outreach(min_score: int = 40, dry_run: bool = False,
                       niche: str = None, city: str = None,
                       lead_id: int = None,
                       subject: Optional[str] = None,
                       body: Optional[str] = None) -> None:
    """Generate personalized emails and push to Instantly.

    Args:
        lead_id: if set, sends to ONLY this lead — used by the manual
            "Contact Now" button. This bypasses the min_score, email_verified,
            and "already emailed" filters (the human explicitly asked for
            this one, right now) but NEVER bypasses do_not_contact or the
            lead having an email at all — those stay hard requirements.
        subject, body: if both are set (Settings → Templates → "custom"
            mode, loaded via the compose modal on the Email Outreach page),
            every lead in this batch gets this exact template — with
            {{name}}/{{business}}/{{city}}/{{niche}} filled in per lead —
            instead of a Groq-generated email. Leaving either blank falls
            back to the existing Groq-generated email, unchanged.
    """

    console.rule("[bold cyan]Phase 5: Email Outreach via Instantly")

    db = SyncSessionLocal()

    if lead_id:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            console.print(f"[red]Lead ID {lead_id} not found[/red]")
            db.close()
            return
        if lead.do_not_contact:
            console.print(f"[red]Lead {lead.name} is on the Do-Not-Contact list — refusing to send.[/red]")
            db.close()
            return
        if not lead.email:
            console.print(f"[red]Lead {lead.name} has no email on file — cannot send.[/red]")
            db.close()
            return
        leads = [lead]
    else:
        # Get eligible leads
        query = db.query(Lead).filter(
            Lead.final_score >= min_score,
            Lead.email_verified == True,
            Lead.do_not_contact == False,
            Lead.email != None,
        )
        if niche:
            query = query.filter(Lead.niche == niche)
        if city:
            query = query.filter(Lead.city == city)

        # Only leads that haven't been emailed yet
        emailed_lead_ids = [e.lead_id for e in db.query(EmailLog.lead_id).all()]
        leads = query.filter(~Lead.id.in_(emailed_lead_ids)).order_by(
            Lead.final_score.desc()
        ).all()

    if not leads:
        console.print("[yellow]No eligible leads found for email outreach.[/yellow]")
        return

    console.print(f"Found [bold]{len(leads)}[/bold] leads eligible for outreach (score ≥ {min_score})")

    if dry_run:
        console.print("[bold yellow]DRY RUN MODE — Emails generated but NOT pushed to Instantly[/bold yellow]")

    # Get/create Instantly campaign
    campaign_name = f"ecomdropship_{niche or 'all'}_{city or 'all'}"
    campaign_id = None
    if not dry_run:
        campaign_id = create_or_get_campaign(campaign_name)
        if not campaign_id and not dry_run:
            console.print("[red]Failed to create Instantly campaign. Check API key.[/red]")
            return

    sent_count = failed_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Generating & sending emails...", total=len(leads))

        for lead in leads:
            progress.update(task, description=f"Processing: {lead.name[:35]}")

            # Build lead dict for Claude
            lead_dict = {
                "name": lead.name,
                "city": lead.city,
                "niche": lead.niche,
                "linkedin_name": lead.linkedin_name,
                "linkedin_title": lead.linkedin_title,
                "dropship_mentioned": lead.dropship_mentioned,
                "sells_on_marketplace": lead.sells_on_marketplace,
                "final_score": lead.final_score,
                "business_summary": lead.audit_summary[:200] if lead.audit_summary else "",
            }

            # Use the user's own Settings → Templates text if a custom
            # subject/body was passed in (Email Outreach page → "Custom"
            # mode); otherwise fall back to the existing Groq-generated
            # email, exactly as before.
            if subject and body:
                email_data = {
                    "subject": apply_template_vars(subject, lead.name, lead.city, lead.niche, lead.linkedin_name),
                    "body":    apply_template_vars(body,    lead.name, lead.city, lead.niche, lead.linkedin_name),
                }
            else:
                email_data = generate_cold_email(lead_dict)
            if not email_data.get("subject") or not email_data.get("body"):
                logger.warning(f"Email generation failed for {lead.name}")
                failed_count += 1
                progress.advance(task)
                continue

            if dry_run:
                console.print(f"\n[dim]--- Preview for {lead.name} ---[/dim]")
                console.print(f"[bold]Subject:[/bold] {email_data['subject']}")
                console.print(f"[dim]{email_data['body'][:200]}...[/dim]")

                # Still log to DB in dry_run mode (just without an Instantly
                # push). This lets you test webhooks/notifications locally
                # before you have a paid Instantly key — without this, the
                # Instantly reply webhook has nothing to match against.
                email_log = EmailLog(
                    lead_id=lead.id,
                    campaign_id="dry_run",
                    instantly_lead_id=None,
                    subject=email_data.get("subject"),
                    body=email_data.get("body"),
                    personalized_for=lead.linkedin_name or lead.name,
                    sent_at=datetime.utcnow(),
                )
                db.add(email_log)
                LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.EMAIL_SENT)
                db.commit()

                sent_count += 1
            else:
                # Push to Instantly
                instantly_lead_id = push_lead_to_instantly(campaign_id, lead, email_data)

                if instantly_lead_id:
                    # Only log + advance the pipeline stage on a CONFIRMED send.
                    # If we logged this even on failure, the lead would look
                    # "already emailed" on the next run (run_email_outreach
                    # filters out any lead with an EmailLog row) and would
                    # never be retried — even though no email actually went out.
                    email_log = EmailLog(
                        lead_id=lead.id,
                        campaign_id=campaign_id,
                        instantly_lead_id=instantly_lead_id,
                        subject=email_data.get("subject"),
                        body=email_data.get("body"),
                        personalized_for=lead.linkedin_name or lead.name,
                        sent_at=datetime.utcnow(),
                    )
                    db.add(email_log)
                    LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.EMAIL_SENT)
                    db.commit()
                    sent_count += 1
                else:
                    # Don't write anything — leave the lead untouched so it's
                    # picked up and retried on the next outreach run instead
                    # of being silently stuck forever.
                    logger.warning(f"Instantly push failed for {lead.name} — will retry next run")
                    failed_count += 1

            progress.advance(task)

    console.rule("[bold green]Phase 5 Complete!")
    console.print(f"  ✅ Emails sent/queued: [bold green]{sent_count}[/bold green]")
    console.print(f"  ❌ Failed:             [bold red]{failed_count}[/bold red]")
    if not dry_run:
        console.print(f"\n[dim]Campaign: '{campaign_name}'[/dim]")
        console.print(f"[dim]Next step: python script_07_vapi_agent.py --hot_leads_only[/dim]")

    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email Outreach via Instantly")
    parser.add_argument("--min_score", type=int, default=40, help="Minimum lead score (default: 40)")
    parser.add_argument("--niche", type=str, help="Filter by niche")
    parser.add_argument("--city", type=str, help="Filter by city")
    parser.add_argument("--dry_run", action="store_true",
                        help="Preview emails without sending")
    args = parser.parse_args()

    try:
        init_db()
    except Exception:
        pass

    run_email_outreach(
        min_score=args.min_score,
        dry_run=args.dry_run,
        niche=args.niche,
        city=args.city,
    )