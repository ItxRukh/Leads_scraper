"""
script_03_auditor.py — Phase 3: Lead Scoring & Audit
Calculates base score + bonus score for every lead.
Usage:
    python script_03_auditor.py --all
    python script_03_auditor.py --lead_id 42
"""

import argparse
import sys
from datetime import datetime
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from utils.console import make_console
from config import settings
from database import Lead, LeadTier, DNCList, SyncSessionLocal, LeadCRUD, DNCCrud, init_db
from profile_schema import ProfileConfig
from queue_service import enqueue_if_eligible
from scoring_engine import apply_result, resolve_profile, score_lead
from utils.groq_helper import analyze_website, answer_questions

console = make_console()


# ─── Scoring Logic ────────────────────────────────────────────────────────────
# The rules themselves live in the lead's Industry Profile and are run by
# scoring_engine.py; this wrapper keeps the original return shape.

def calculate_score(lead: Lead, db) -> tuple[int, int, str, bool, Optional[str]]:
    """
    Returns: (base_score, bonus_score, audit_summary, score_incomplete, score_incomplete_reason)
    """
    result = score_lead(db, lead)
    return result.base, result.bonus, result.summary, result.incomplete, result.incomplete_reason


# ─── Claude Enhancement ───────────────────────────────────────────────────────

def enhance_with_claude(lead: Lead) -> dict:
    """
    Use the AI model (Groq) to re-analyze website text for better accuracy.
    Fills in any gaps from basic regex crawling.
    """
    if not lead.crawled_text or not settings.GROQ_API_KEY:
        return {}

    logger.debug(f"Claude analyzing: {lead.name}")
    result = analyze_website(lead.name, lead.crawled_text)

    if result:
        # Override with Claude's findings if more accurate
        if result.get("dropship_mentioned") and not lead.dropship_mentioned:
            lead.dropship_mentioned = True
            logger.info(f"Claude found dropship mention missed by regex: {lead.name}")

        if result.get("processing_fast") and not lead.processing_fast:
            lead.processing_fast = True
            lead.processing_days_mentioned = result.get("processing_time")

        if result.get("delivery_fast") and not lead.delivery_fast:
            lead.delivery_fast = True
            lead.delivery_days_mentioned = result.get("delivery_time")

        if result.get("sells_on_amazon"): lead.sells_on_amazon = True
        if result.get("sells_on_etsy"): lead.sells_on_etsy = True
        if result.get("sells_on_shopify"): lead.sells_on_shopify = True

        lead.sells_on_marketplace = any([
            lead.sells_on_amazon, lead.sells_on_etsy, lead.sells_on_shopify
        ])

    return result


def answer_profile_questions(lead: Lead, config: ProfileConfig) -> None:
    """
    Answer the profile's "AI question" rules from the lead's website text and
    keep the answers in lead.signals. Questions already answered are not asked again.
    """
    ai_rules = [r for r in config.scoring.rules if r.kind == "ai"]
    if not ai_rules or not lead.crawled_text or not settings.GROQ_API_KEY:
        return

    signals = dict(lead.signals or {})
    pending = {r.key: r.params["question"] for r in ai_rules if signals.get(r.key) is None}
    if not pending:
        return

    for key, answer in answer_questions(lead.name, lead.crawled_text, pending).items():
        if answer is not None:
            signals[key] = answer
    lead.signals = signals


# ─── Main Audit Runner ────────────────────────────────────────────────────────

def audit_lead(lead: Lead, db, use_claude: bool = True) -> Lead:
    """Audit a single lead: crawl if needed, score, save."""

    # If website not crawled yet, do it now
    if lead.website and not lead.website_crawled:
        from utils.crawler import crawl_website
        crawl_data = crawl_website(lead.website)
        if crawl_data:
            for key, val in crawl_data.items():
                if hasattr(lead, key):
                    setattr(lead, key, val)
            if crawl_data.get("emails_found") and not lead.email:
                lead.email = crawl_data["emails_found"][0]
        db.commit()

    # AI help: the dropshipping analysis only for profiles that use it, plus
    # whatever "AI question" rules the lead's profile defines
    if use_claude and lead.crawled_text:
        profile = resolve_profile(db, lead)
        config = ProfileConfig.model_validate(profile.config) if profile else None
        if config is None or any(r.kind == "builtin" for r in config.scoring.rules):
            enhance_with_claude(lead)
        if config is not None:
            answer_profile_questions(lead, config)
        db.commit()

    # Score with the lead's Industry Profile, then queue it if it qualifies
    result = score_lead(db, lead)
    apply_result(lead, result)
    db.commit()
    enqueue_if_eligible(db, lead)

    return lead


def run_auditor(lead_id: Optional[int] = None, use_claude: bool = True,
                 force_reaudit: bool = False, niche: Optional[str] = None,
                 city: Optional[str] = None) -> None:
    """
    Audit all unaudited leads, a single lead by ID, or (only if explicitly
    requested) force a re-audit of every lead in the database.

    Args:
        force_reaudit: if True, re-audits EVERY lead regardless of whether
                       it was already audited — burns Groq/Claude API
                       credits per lead, so this must be asked for
                       explicitly. Defaults to False: if there's nothing
                       new to audit, the function does nothing and returns.
                       (Previously this silently re-audited the entire
                       database every single time it was called with no
                       new leads — expensive and wasteful once the leads
                       table grows past a handful of rows.)
        niche, city:   if given, only audits unaudited leads matching this
                       niche/city (used by post_scrape_pipeline.py to scope
                       the chained audit → verify → send run to just the
                       batch that was scraped, instead of touching every
                       unaudited lead sitting in the database from other
                       scrapes). Leave both None for the old global
                       behaviour (used by the standalone "Audit" button /
                       /pipeline/audit endpoint).
    """

    console.rule("[bold cyan]Phase 3: Lead Scoring & Audit")

    db = SyncSessionLocal()

    if lead_id:
        leads = [db.query(Lead).filter(Lead.id == lead_id).first()]
        if not leads[0]:
            console.print(f"[red]Lead ID {lead_id} not found![/red]")
            return
    else:
        query = db.query(Lead).filter(Lead.audited_at == None)
        if niche:
            query = query.filter(Lead.niche == niche)
        if city:
            query = query.filter(Lead.city == city)
        leads = query.all()
        if not leads:
            if force_reaudit:
                console.print("[yellow]No unaudited leads found — force_reaudit=True, re-scoring everyone.[/yellow]")
                requery = db.query(Lead)
                if niche:
                    requery = requery.filter(Lead.niche == niche)
                if city:
                    requery = requery.filter(Lead.city == city)
                leads = requery.all()
            else:
                console.print("[green]No unaudited leads found. All leads already audited — nothing to do.[/green]")
                db.close()
                return

    console.print(f"Auditing [bold]{len(leads)}[/bold] leads...")

    hot_count = warm_count = cold_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Scoring leads...", total=len(leads))

        for lead in leads:
            progress.update(task, description=f"Auditing: {lead.name[:35]}")
            audit_lead(lead, db, use_claude=use_claude)

            if lead.tier and lead.tier.value == "hot": hot_count += 1
            elif lead.tier and lead.tier.value == "warm": warm_count += 1
            else: cold_count += 1

            progress.advance(task)

    # ── Display Summary ────────────────────────────────────────────────────
    table = Table(title="Audit Results — Top 20 Leads by Score", show_lines=True)
    table.add_column("Rank", style="dim", justify="center")
    table.add_column("Business Name", style="bold cyan", max_width=30)
    table.add_column("City", style="green")
    table.add_column("Base", justify="center", style="blue")
    table.add_column("Bonus", justify="center", style="magenta")
    table.add_column("Final", justify="center", style="bold")
    table.add_column("Tier", justify="center")

    top_leads = db.query(Lead).order_by(Lead.final_score.desc()).limit(20).all()
    tier_colors = {"hot": "bold red", "warm": "bold yellow", "cold": "bold blue", "unscored": "dim"}
    tier_labels = {"hot": "🔴 HOT", "warm": "🟡 WARM", "cold": "🔵 COLD", "unscored": "⬜ N/A"}

    for i, lead in enumerate(top_leads, 1):
        tier_val = lead.tier.value if lead.tier else "unscored"
        table.add_row(
            str(i),
            lead.name[:30],
            lead.city or "",
            str(lead.base_score),
            f"+{lead.bonus_score}",
            str(lead.final_score),
            tier_labels.get(tier_val, tier_val),
        )

    console.print(table)

    console.rule("[bold green]Phase 3 Complete!")
    console.print(f"  🔴 Hot Leads:  [bold red]{hot_count}[/bold red]")
    console.print(f"  🟡 Warm Leads: [bold yellow]{warm_count}[/bold yellow]")
    console.print(f"  🔵 Cold Leads: [bold blue]{cold_count}[/bold blue]")
    console.print(f"\n[dim]Next step: python script_04_email_verify.py --all[/dim]")

    db.close()


# ─── CLI Entry ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ecomdropship.ai Lead Auditor")
    parser.add_argument("--all", action="store_true", help="Audit all unscored leads")
    parser.add_argument("--lead_id", type=int, help="Audit a specific lead by ID")
    parser.add_argument("--no-claude", action="store_true",
                        help="Skip Claude API analysis (faster, less accurate)")
    parser.add_argument("--force_reaudit", action="store_true",
                        help="Re-score EVERY lead, even ones already audited (costs API credits — use deliberately)")
    args = parser.parse_args()

    try:
        init_db()
    except Exception:
        pass

    run_auditor(
        lead_id=args.lead_id,
        use_claude=not args.no_claude,
        force_reaudit=args.force_reaudit,
    )