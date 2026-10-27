"""
script_02_linkedin.py — Phase 2: LinkedIn Decision Maker Enrichment
---------------------------------------------------------------------
For each scraped lead, searches LinkedIn for the business owner or
operations manager using PhantomBuster API.

Why decision-maker enrichment?
  - Personalizing outreach with "Hi John" instead of "Hi there" = 3x reply rate
  - Addressing the actual decision-maker avoids gatekeepers
  - LinkedIn title confirms they have authority to make partnership decisions

Bonus: Finding a LinkedIn decision maker adds +15 points to the lead score.

PhantomBuster:
  - Cloud automation tool that can run LinkedIn searches
  - We use their "LinkedIn Search Export" phantom
  - Returns: name, title, LinkedIn URL, company

Usage:
    python script_02_linkedin.py --all
    python script_02_linkedin.py --lead_id 42
    python script_02_linkedin.py --all --dry_run
"""

import argparse
import time
from typing import Optional

import requests
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from utils.console import make_console
from config import settings
from database import Lead, PipelineStage, SyncSessionLocal, LeadCRUD, init_db

console = make_console()

# ─── Job Titles We're Looking For ────────────────────────────────────────────
# PhantomBuster returns multiple results — we pick the most relevant title
PRIORITY_TITLES = [
    "owner", "co-owner", "founder", "co-founder",
    "ceo", "president", "director",
    "operations manager", "ops manager", "general manager",
    "purchasing manager", "procurement", "supply chain",
    "vp", "vice president",
]


def _title_priority(title: str) -> int:
    """
    Returns a priority score for a LinkedIn title.
    Lower number = higher priority (owner > manager > etc.)
    Used to pick the BEST contact when multiple people are found.
    """
    if not title:
        return 999
    title_lower = title.lower()
    for i, keyword in enumerate(PRIORITY_TITLES):
        if keyword in title_lower:
            return i
    return 500  # Low priority if title doesn't match any keyword


# ─── PhantomBuster API ────────────────────────────────────────────────────────

def search_linkedin_phantombuster(business_name: str, city: str) -> Optional[dict]:
    """
    Searches LinkedIn for a decision maker at the given business.
    Uses PhantomBuster's LinkedIn Search Export API.
    
    PhantomBuster works by launching a "phantom" (automation agent) in the cloud.
    We pass the search query, it searches LinkedIn and returns results.
    
    Args:
        business_name: Company name to search for
        city: City to narrow the search
    
    Returns:
        dict with name, title, linkedin_url or None if not found
    """
    if not settings.PHANTOMBUSTER_API_KEY:
        logger.warning("PHANTOMBUSTER_API_KEY not set — skipping LinkedIn enrichment")
        return None

    # Build search query — same format as searching on LinkedIn
    search_query = f"{business_name} {city} owner OR founder OR manager"

    headers = {
        "X-Phantombuster-Key": settings.PHANTOMBUSTER_API_KEY,
        "Content-Type": "application/json",
    }

    # Launch the PhantomBuster agent
    launch_payload = {
        "argument": {
            "searches": [search_query],
            "numberOfProfiles": 5,       # Get top 5 results to pick the best
            "csvName": f"linkedin_{business_name[:20]}",
        }
    }

    try:
        # Step 1: Launch the phantom (starts the LinkedIn search in the cloud)
        launch_url = f"{settings.PHANTOMBUSTER_BASE_URL}/agents/launch"
        launch_response = requests.post(
            launch_url,
            headers=headers,
            json=launch_payload,
            timeout=15,
        )
        launch_data = launch_response.json()
        container_id = launch_data.get("containerId")

        if not container_id:
            logger.error(f"PhantomBuster launch failed: {launch_data}")
            return None

        logger.debug(f"PhantomBuster launched for {business_name}, container: {container_id}")

        # Step 2: Poll for results (phantom takes 10-30 seconds to run)
        # We check every 5 seconds, up to 12 times (60 seconds max)
        for attempt in range(12):
            time.sleep(5)

            status_url = f"{settings.PHANTOMBUSTER_BASE_URL}/containers/fetch-output"
            status_response = requests.get(
                status_url,
                headers=headers,
                params={"id": container_id},
                timeout=10,
            )
            status_data = status_response.json()

            if status_data.get("status") == "finished":
                results = status_data.get("output", [])
                return _pick_best_contact(results)

            if status_data.get("status") == "error":
                logger.error(f"PhantomBuster error for {business_name}: {status_data}")
                return None

            logger.debug(f"Waiting for PhantomBuster... attempt {attempt + 1}/12")

        logger.warning(f"PhantomBuster timed out for {business_name}")
        return None

    except Exception as e:
        logger.error(f"PhantomBuster API error for {business_name}: {e}")
        return None


def _pick_best_contact(results: list) -> Optional[dict]:
    """
    From a list of LinkedIn search results, picks the most relevant contact.
    Prioritizes: Owner > Founder > CEO > Manager > etc.
    
    Args:
        results: List of dicts from PhantomBuster output
    
    Returns:
        Best contact dict or None if results are empty
    """
    if not results:
        return None

    # Sort by title priority — lower index = more relevant
    sorted_results = sorted(
        results,
        key=lambda r: _title_priority(r.get("title", "") or r.get("jobTitle", ""))
    )

    best = sorted_results[0]
    return {
        "linkedin_name": (best.get("fullName") or best.get("name") or "").strip(),
        "linkedin_title": (best.get("title") or best.get("jobTitle") or "").strip(),
        "linkedin_url": (best.get("profileUrl") or best.get("linkedinUrl") or "").strip(),
    }


# ─── Main Runner ─────────────────────────────────────────────────────────────

def run_linkedin_enrichment(lead_id: Optional[int] = None, dry_run: bool = False) -> None:
    """
    Main function — enriches leads with LinkedIn decision maker data.
    
    Args:
        lead_id: Enrich a single lead only
        dry_run: Print what would be searched without calling PhantomBuster
    """
    console.rule("[bold cyan]Phase 2: LinkedIn Decision Maker Enrichment")

    db = SyncSessionLocal()

    if lead_id:
        lead = LeadCRUD.get_by_id(db, lead_id)
        leads = [lead] if lead else []
    else:
        # All leads that haven't been enriched yet
        leads = db.query(Lead).filter(Lead.linkedin_found == False).all()

    if not leads:
        console.print("[yellow]No leads found for LinkedIn enrichment.[/yellow]")
        return

    console.print(f"Enriching [bold]{len(leads)}[/bold] leads via LinkedIn...")

    if dry_run:
        console.print("[bold yellow]DRY RUN — No PhantomBuster calls will be made[/bold yellow]")

    found_count = not_found_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Searching LinkedIn...", total=len(leads))

        for lead in leads:
            progress.update(task, description=f"Searching: {lead.name[:35]}")

            if dry_run:
                console.print(f"  Would search: '{lead.name} {lead.city} owner OR founder OR manager'")
                progress.advance(task)
                continue

            # Search LinkedIn for this lead
            contact = search_linkedin_phantombuster(lead.name, lead.city or "")

            if contact and contact.get("linkedin_name"):
                # Update lead with LinkedIn data
                lead.linkedin_found  = True
                lead.linkedin_name   = contact["linkedin_name"]
                lead.linkedin_title  = contact["linkedin_title"]
                lead.linkedin_url    = contact["linkedin_url"]

                # Apply +15 bonus points
                lead.bonus_score  = (lead.bonus_score or 0) + 15
                lead.final_score  = (lead.base_score or 0) + lead.bonus_score

                # Re-check tier after bonus
                from config import settings as cfg
                if lead.final_score >= cfg.HOT_LEAD_MIN_SCORE:
                    from database import LeadTier
                    lead.tier = LeadTier.HOT
                elif lead.final_score >= cfg.WARM_LEAD_MIN_SCORE:
                    from database import LeadTier
                    lead.tier = LeadTier.WARM

                # Update pipeline stage
                LeadCRUD.update_pipeline_stage(db, lead.id, PipelineStage.LINKEDIN_ENRICHED)

                db.commit()
                found_count += 1
                logger.info(f"✅ LinkedIn found: {lead.name} → {contact['linkedin_name']} ({contact['linkedin_title']})")

            else:
                not_found_count += 1
                logger.debug(f"No LinkedIn found: {lead.name}")

            # Rate limit — PhantomBuster and LinkedIn have strict rate limits
            # Wait 3 seconds between searches to avoid being blocked
            time.sleep(3)
            progress.advance(task)

    console.rule("[bold green]Phase 2 Complete!")
    console.print(f"  ✅ Decision makers found: [bold green]{found_count}[/bold green]")
    console.print(f"  ❌ Not found:             [bold]{not_found_count}[/bold]")
    console.print(f"\n[dim]Next step: python script_03_auditor.py --all[/dim]")

    db.close()


# ─── CLI Entry Point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn Enrichment via PhantomBuster")
    parser.add_argument("--all", action="store_true", help="Enrich all un-enriched leads")
    parser.add_argument("--lead_id", type=int, help="Enrich a specific lead by ID")
    parser.add_argument("--dry_run", action="store_true",
                        help="Print search queries without calling PhantomBuster")
    args = parser.parse_args()

    try:
        init_db()
    except Exception:
        pass

    run_linkedin_enrichment(
        lead_id=args.lead_id,
        dry_run=args.dry_run,
    )
