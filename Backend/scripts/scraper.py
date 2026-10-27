"""
script_01_scraper.py — Phase 1: Lead Scraping
Sources: Google Places API, SerpAPI (Google Search), Yelp Fusion API
Usage:
    python script_01_scraper.py --niche "toys" --city "Miami FL"
    python script_01_scraper.py --niche "toys" --city "Miami FL" --min_rating 4.0 --min_reviews 20
"""

import argparse
import time
import sys
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote

import requests
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from utils.console import make_console
from config import settings
from database import (
    ImportBatch, IndustryProfile, Lead, LeadSource, PipelineStage, ScrapingJob,
    SyncSessionLocal, LeadCRUD, init_db
)
from geo import infer_geo, us_state
from industries import industry_label, industry_options, normalize_industry
from profile_service import DEFAULT_QUERY_TEMPLATE, build_query, resolve_profile_by_id
from utils.crawler import crawl_website
from utils.exporters import export_leads_to_csv

console = make_console()


# ─── Google Places Scraper ────────────────────────────────────────────────────

def scrape_google_places(niche: str, city: str, min_rating: float, min_reviews: int,
                         query_template: str = DEFAULT_QUERY_TEMPLATE) -> tuple:
    """
    Search Google Places API for businesses by niche + city.
    Returns (leads, filtered_out_count) — filtered_out_count is how many
    matches were dropped for being below min_rating/min_reviews, so the
    post-scrape summary can explain why the numbers don't just add up.
    """
    if not settings.GOOGLE_PLACES_API_KEY:
        logger.warning("GOOGLE_PLACES_API_KEY not set — skipping Google Places")
        return [], 0

    leads = []
    filtered_out = 0
    query = build_query(query_template, niche, city)
    url = f"{settings.GOOGLE_PLACES_BASE_URL}/textsearch/json"

    params = {
        "query": query,
        "key": settings.GOOGLE_PLACES_API_KEY,
        "type": "store|establishment",
        "region": "us",
    }

    page_count = 0
    next_page_token = None

    logger.info(f"Google Places: searching '{query}'")

    while page_count < 3:  # Max 3 pages = ~60 results
        if next_page_token:
            params = {"pagetoken": next_page_token, "key": settings.GOOGLE_PLACES_API_KEY}
            time.sleep(2)  # Google requires delay before using next page token

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get("status") not in ["OK", "ZERO_RESULTS"]:
            logger.error(f"Google Places error: {data.get('status')} — {data.get('error_message', '')}")
            break

        for place in data.get("results", []):
            rating = place.get("rating", 0)
            review_count = place.get("user_ratings_total", 0)

            # Apply filters
            if rating < min_rating or review_count < min_reviews:
                filtered_out += 1
                continue

            # Get detailed info (phone, website require a details call)
            details = get_google_place_details(place["place_id"])

            lead = {
                "name": place.get("name", ""),
                "address": place.get("formatted_address", ""),
                "city": city,
                "rating": rating,
                "review_count": review_count,
                "google_place_id": place["place_id"],
                "source": LeadSource.GOOGLE_PLACES,
                "niche": niche,
                "phone": details.get("phone", ""),
                "website": details.get("website", ""),
                "email": "",  # Google Places doesn't provide emails
            }
            leads.append(lead)

        next_page_token = data.get("next_page_token")
        if not next_page_token:
            break
        page_count += 1

    logger.info(f"Google Places: found {len(leads)} leads (after filters, {filtered_out} filtered out)")
    return leads, filtered_out


def get_google_place_details(place_id: str) -> dict:
    """Fetch phone + website for a specific Google Place ID"""
    if not settings.GOOGLE_PLACES_API_KEY:
        return {}

    url = f"{settings.GOOGLE_PLACES_BASE_URL}/details/json"
    params = {
        "place_id": place_id,
        "fields": "formatted_phone_number,website",
        "key": settings.GOOGLE_PLACES_API_KEY,
    }
    try:
        response = requests.get(url, params=params, timeout=10)
        result = response.json().get("result", {})
        return {
            "phone": result.get("formatted_phone_number", ""),
            "website": result.get("website", ""),
        }
    except Exception as e:
        logger.debug(f"Google Place details failed for {place_id}: {e}")
        return {}


# ─── SerpAPI Scraper ──────────────────────────────────────────────────────────

def scrape_serpapi(niche: str, city: str, min_rating: float, min_reviews: int,
                   query_template: str = DEFAULT_QUERY_TEMPLATE) -> tuple:
    """
    Use SerpAPI to search Google for wholesale suppliers.
    Returns (leads, filtered_out_count). SerpAPI results don't carry a
    rating/review_count, so no rating filter applies here — filtered_out_count
    is always 0, kept only so the caller can treat all three sources the same way.
    """
    if not settings.SERPAPI_KEY:
        logger.warning("SERPAPI_KEY not set — skipping SerpAPI")
        return [], 0

    leads = []
    query = build_query(query_template, niche, city)
    logger.info(f"SerpAPI: searching '{query}'")

    params = {
        "engine": "google",
        "q": query,
        "location": city + ", USA",
        "gl": "us",
        "hl": "en",
        "num": 100,
        "api_key": settings.SERPAPI_KEY,
    }

    try:
        response = requests.get(settings.SERPAPI_BASE_URL, params=params, timeout=15)
        data = response.json()

        for result in data.get("organic_results", []):
            lead = {
                "name": result.get("title", ""),
                "website": result.get("link", ""),
                "address": result.get("displayed_link", ""),
                "city": city,
                "niche": niche,
                "source": LeadSource.SERPAPI,
                "rating": 0.0,
                "review_count": 0,
                "phone": "",
                "email": "",
                "google_place_id": None,
            }
            # Try to extract rating from snippet if available
            snippet = result.get("snippet", "")
            leads.append(lead)

    except Exception as e:
        logger.error(f"SerpAPI error: {e}")

    logger.info(f"SerpAPI: found {len(leads)} leads")
    return leads, 0


# ─── Yelp Scraper ─────────────────────────────────────────────────────────────

def scrape_yelp(niche: str, city: str, min_rating: float, min_reviews: int,
                query_template: str = DEFAULT_QUERY_TEMPLATE) -> tuple:
    """
    Use Yelp Fusion API to search businesses by niche + location.
    Returns (leads, filtered_out_count) — same convention as scrape_google_places.
    """
    if not settings.YELP_API_KEY:
        logger.warning("YELP_API_KEY not set — skipping Yelp")
        return [], 0

    leads = []
    filtered_out = 0
    url = f"{settings.YELP_BASE_URL}/businesses/search"
    headers = {"Authorization": f"Bearer {settings.YELP_API_KEY}"}

    params = {
        "term": build_query(query_template, niche, ""),
        "location": city + " USA",
        "limit": 50,
        "sort_by": "rating",
        "categories": "wholesale_stores,manufacturers",
    }

    logger.info(f"Yelp: searching '{niche}' in '{city}'")

    try:
        # Fetch first page
        response = requests.get(url, headers=headers, params=params, timeout=10)
        data = response.json()

        if "error" in data:
            logger.error(f"Yelp API error: {data['error']['description']}")
            return [], 0

        for biz in data.get("businesses", []):
            rating = biz.get("rating", 0)
            review_count = biz.get("review_count", 0)

            if rating < min_rating or review_count < min_reviews:
                filtered_out += 1
                continue

            location = biz.get("location", {})
            address_parts = location.get("display_address", [])
            address = ", ".join(address_parts)

            lead = {
                "name": biz.get("name", ""),
                "phone": biz.get("phone", ""),
                "website": biz.get("url", ""),  # Yelp page URL
                "address": address,
                "city": city,
                "niche": niche,
                "source": LeadSource.YELP,
                "rating": rating,
                "review_count": review_count,
                "yelp_business_id": biz.get("id", ""),
                "google_place_id": None,
                "email": "",
            }
            leads.append(lead)

    except Exception as e:
        logger.error(f"Yelp scraping error: {e}")

    logger.info(f"Yelp: found {len(leads)} leads (after filters, {filtered_out} filtered out)")
    return leads, filtered_out


# ─── Deduplication ────────────────────────────────────────────────────────────

def deduplicate_leads(leads: List[dict]) -> tuple:
    """
    Remove duplicate leads across all sources.
    Dedup by: business name similarity + phone number
    Returns (unique_leads, removed_count).
    """
    seen_phones = set()
    seen_names = set()
    unique = []

    for lead in leads:
        phone = (lead.get("phone") or "").strip().replace(" ", "").replace("-", "")
        name = (lead.get("name") or "").strip().lower()

        # Skip if exact phone match
        if phone and phone in seen_phones:
            continue
        # Skip if exact name match
        if name and name in seen_names:
            continue

        if phone:
            seen_phones.add(phone)
        if name:
            seen_names.add(name)

        unique.append(lead)

    removed = len(leads) - len(unique)
    logger.info(f"Deduplication: removed {removed} duplicates → {len(unique)} unique leads")
    return unique, removed


# ─── Save to Database ─────────────────────────────────────────────────────────

def save_leads_to_db(leads: List[dict], db, profile_id: int = None, batch_id: int = None,
                     industry_key: str = None) -> tuple:
    """Save scraped leads to the database. Skip duplicates.
    Returns (saved_leads, skipped_count) — skipped_count is how many of
    this batch already existed in the DB from an earlier scrape."""
    saved = []
    skipped = 0

    for lead_data in leads:
        # Check duplicate
        existing = LeadCRUD.check_duplicate(db, lead_data["name"], lead_data.get("phone"))
        if existing:
            skipped += 1
            continue

        country, timezone = infer_geo(lead_data.get("city"), lead_data.get("address"))
        state = us_state(lead_data.get("city"), lead_data.get("address"))
        lead = Lead(
            profile_id=profile_id, country=country, timezone=timezone, import_batch_id=batch_id,
            attributes={"state": state, "industry_key": industry_key, "scraped_niche": lead_data.get("niche")}
                       if industry_key else {"state": state},
            name=lead_data.get("name", ""),
            phone=lead_data.get("phone", ""),
            email=lead_data.get("email", ""),
            address=lead_data.get("address", ""),
            city=lead_data.get("city", ""),
            website=lead_data.get("website", ""),
            niche=(industry_label(industry_key) if industry_key else None) or lead_data.get("niche", ""),
            rating=lead_data.get("rating", 0.0),
            review_count=lead_data.get("review_count", 0),
            source=lead_data.get("source", LeadSource.GOOGLE_PLACES),
            google_place_id=lead_data.get("google_place_id"),
            yelp_business_id=lead_data.get("yelp_business_id"),
            pipeline_stage=PipelineStage.SCRAPED,
            scraped_at=datetime.utcnow(),
        )
        db.add(lead)
        saved.append(lead)

    db.commit()
    logger.info(f"Saved {len(saved)} new leads | Skipped {skipped} duplicates")
    return saved, skipped


# ─── Website Crawling ─────────────────────────────────────────────────────────

def crawl_lead_websites(leads: List[Lead], db) -> None:
    """Crawl websites for all leads that have a URL. Updates DB in place."""

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Crawling websites...", total=len(leads))

        for lead in leads:
            progress.update(task, description=f"Crawling: {lead.name[:40]}")

            if not lead.website:
                progress.advance(task)
                continue

            crawl_data = crawl_website(lead.website)
            if not crawl_data:
                progress.advance(task)
                continue

            # Update lead with crawl results
            lead.website_crawled = crawl_data.get("website_crawled", False)
            lead.crawled_text = crawl_data.get("crawled_text", "")
            lead.dropship_mentioned = crawl_data.get("dropship_mentioned", False)
            lead.processing_days_mentioned = crawl_data.get("processing_days_mentioned")
            lead.delivery_days_mentioned = crawl_data.get("delivery_days_mentioned")
            lead.processing_fast = crawl_data.get("processing_fast", False)
            lead.delivery_fast = crawl_data.get("delivery_fast", False)
            lead.sells_on_amazon = crawl_data.get("sells_on_amazon", False)
            lead.sells_on_etsy = crawl_data.get("sells_on_etsy", False)
            lead.sells_on_shopify = crawl_data.get("sells_on_shopify", False)
            lead.sells_on_marketplace = crawl_data.get("sells_on_marketplace", False)
            lead.marketplace_urls = crawl_data.get("marketplace_urls", "[]")

            # Use first found email if lead has no email
            emails_found = crawl_data.get("emails_found", [])
            if emails_found and not lead.email:
                lead.email = emails_found[0]

            db.commit()
            progress.advance(task)


# ─── Display Results ──────────────────────────────────────────────────────────

def display_results_table(leads: List[Lead]) -> None:
    """Pretty print results in terminal"""
    table = Table(title=f"Scraped Leads ({len(leads)} total)", show_lines=True)
    table.add_column("Name", style="bold cyan", max_width=30)
    table.add_column("City", style="green")
    table.add_column("Phone", style="white")
    table.add_column("Email", style="yellow")
    table.add_column("Rating", style="magenta", justify="center")
    table.add_column("Reviews", justify="center")
    table.add_column("Dropship?", justify="center")
    table.add_column("Marketplace?", justify="center")

    for lead in leads[:50]:  # Show first 50
        table.add_row(
            lead.name[:30],
            lead.city or "",
            lead.phone or "—",
            lead.email[:25] if lead.email else "—",
            str(lead.rating) if lead.rating else "—",
            str(lead.review_count),
            "✅" if lead.dropship_mentioned else "❌",
            "✅" if lead.sells_on_marketplace else "❌",
        )

    console.print(table)


# ─── Main Runner ──────────────────────────────────────────────────────────────

def run_scraper(niche: str, city: str, min_rating: float = 3.5,
                min_reviews: int = 10, skip_crawl: bool = False,
                max_leads: int = 50, job_id: int = None, profile_id: int = None) -> None:
    """
    Main function — runs full Phase 1 pipeline

    Args:
        job_id: if provided (set by the /pipeline/scrape API endpoint), reuse
                that existing ScrapingJob row instead of creating a new one —
                lets the frontend poll GET /scraping-jobs for this exact id
                to know precisely when the run finishes. CLI usage (no API)
                leaves this None and a fresh job row is created as before.
        profile_id: Industry Profile that decides the search wording and how
                leads are scored. None = the default profile.
    """

    console.rule(f"[bold cyan]Scraping '{niche}' businesses in '{city}'")

    db = SyncSessionLocal()

    industry_key = normalize_industry(niche) or "other"
    if profile_id is None:                      # "plumbers" -> the Plumbing profile, anything unknown -> Other
        profile_id = db.query(IndustryProfile.id).filter(IndustryProfile.slug == industry_key).scalar()
    profile = resolve_profile_by_id(db, profile_id)
    profile_id = profile.id if profile else None
    if profile is not None and profile.slug != industry_key:
        industry_key = profile.slug if profile.slug in {o["key"] for o in industry_options()} else industry_key
    query_template = (
        profile.config.get("search", {}).get("query_template") if profile else None
    ) or DEFAULT_QUERY_TEMPLATE

    # Log scraping job — reuse the row the API already created (if any),
    # otherwise fall back to creating a new one (CLI usage).
    if job_id is not None:
        job = db.query(ScrapingJob).filter(ScrapingJob.id == job_id).first()
    else:
        job = None

    if job is None:
        job = ScrapingJob(
            niche=niche, city=city,
            min_rating=min_rating, min_reviews=min_reviews,
            status="running"
        )
        db.add(job)
        db.commit()

    all_raw_leads = []

    try:
        # ── Step 1: Scrape all sources ─────────────────────────────────────
        console.print("\n[bold]Step 1/4:[/bold] Scraping sources...")

        low_rating_skipped = 0

        with console.status("Scraping Google Places..."):
            google_leads, google_filtered = scrape_google_places(niche, city, min_rating, min_reviews, query_template)
            job.total_google = len(google_leads)
            low_rating_skipped += google_filtered
            all_raw_leads.extend(google_leads)

        with console.status("Scraping SerpAPI..."):
            serp_leads, serp_filtered = scrape_serpapi(niche, city, min_rating, min_reviews, query_template)
            job.total_serpapi = len(serp_leads)
            low_rating_skipped += serp_filtered
            all_raw_leads.extend(serp_leads)

        with console.status("Scraping Yelp..."):
            yelp_leads, yelp_filtered = scrape_yelp(niche, city, min_rating, min_reviews, query_template)
            job.total_yelp = len(yelp_leads)
            low_rating_skipped += yelp_filtered
            all_raw_leads.extend(yelp_leads)

        job.skipped_low_rating = low_rating_skipped
        # Total matches this scrape actually found, before any filtering —
        # what the post-scrape summary calls "mile" (found).
        job.total_raw_matches = len(all_raw_leads) + low_rating_skipped
        db.commit()

        console.print(f"  Raw leads scraped: [bold]{len(all_raw_leads)}[/bold]"
                      f" ({low_rating_skipped} filtered out for low rating/reviews)")

        # ── Step 2: Deduplicate ────────────────────────────────────────────
        console.print("\n[bold]Step 2/4:[/bold] Deduplicating leads...")
        unique_leads, dedup_removed = deduplicate_leads(all_raw_leads)
        job.skipped_duplicate_batch = dedup_removed
        db.commit()
        console.print(f"  Unique leads after dedup: [bold]{len(unique_leads)}[/bold]")

        # Cap at max_leads (Settings → Scraper → "Max Leads Per Scrape")
        if max_leads and len(unique_leads) > max_leads:
            console.print(f"  Capping to max_leads={max_leads} (from Settings)")
            job.skipped_over_cap = len(unique_leads) - max_leads
            db.commit()
            unique_leads = unique_leads[:max_leads]

        # ── Step 3: Save to database ───────────────────────────────────────
        console.print("\n[bold]Step 3/4:[/bold] Saving to database...")
        batch = ImportBatch(name=f"Google Maps: {niche} in {city}", source="google maps", default_industry=industry_key,
                            total_rows=len(unique_leads))
        db.add(batch)
        db.flush()
        saved_leads, db_duplicate_skipped = save_leads_to_db(unique_leads, db, profile_id, batch.id, industry_key)
        batch.imported = len(saved_leads)
        batch.duplicates = db_duplicate_skipped
        job.import_batch_id = batch.id
        job.total_scraped = len(saved_leads)
        job.skipped_duplicate_existing = db_duplicate_skipped
        db.commit()

        # ── Step 4: Crawl websites ───────────────────────────────────────────
        if not skip_crawl:
            console.print(f"\n[bold]Step 4/4:[/bold] Crawling {len(saved_leads)} websites...")
            crawl_lead_websites(saved_leads, db)
        else:
            console.print("\n[yellow]Step 4/4: Website crawling skipped (--skip-crawl flag)[/yellow]")

        # ── Finish ────────────────────────────────────────────────────────
        job.status = "done"
        job.completed_at = datetime.utcnow()
        db.commit()

    except Exception as e:
        # Mark the job as failed instead of leaving it stuck on "running"
        # forever — the frontend polls this status to know when to show the
        # post-scrape "send emails?" dialog, so this must always resolve.
        logger.error(f"Scraper job {job.id} failed: {e}")
        job.status = "failed"
        job.completed_at = datetime.utcnow()
        db.commit()
        db.close()
        return

    # ── Export CSV ────────────────────────────────────────────────────────
    all_leads = db.query(Lead).filter(
        Lead.import_batch_id == batch.id
    ).order_by(Lead.scraped_at.desc()).all()

    csv_path = export_leads_to_csv(all_leads, niche, city)

    # ── Display Results ───────────────────────────────────────────────────
    display_results_table(all_leads)

    console.rule("[bold green]Phase 1 Complete!")
    console.print(f"  Total leads saved: [bold green]{len(all_leads)}[/bold green]")
    console.print(f"  CSV exported to: [bold]{csv_path}[/bold]")
    console.print(f"\n[dim]Next step: python script_03_auditor.py --all[/dim]")

    db.close()


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ecomdropship.ai Lead Scraper")
    parser.add_argument("--niche", type=str, required=True,
                        help="Business niche to search (e.g. 'toys', 'electronics')")
    parser.add_argument("--city", type=str, required=True,
                        help="City to search in (e.g. 'Miami FL', 'New York NY')")
    parser.add_argument("--min_rating", type=float, default=3.5,
                        help="Minimum Google rating (default: 3.5)")
    parser.add_argument("--min_reviews", type=int, default=10,
                        help="Minimum review count (default: 10)")
    parser.add_argument("--skip-crawl", action="store_true",
                        help="Skip website crawling step")

    args = parser.parse_args()

    # Initialize DB if needed
    try:
        init_db()
    except Exception:
        pass

    run_scraper(
        niche=args.niche,
        city=args.city,
        min_rating=args.min_rating,
        min_reviews=args.min_reviews,
        skip_crawl=args.skip_crawl
    )