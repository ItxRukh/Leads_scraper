"""
scripts/post_scrape_pipeline.py — Chained Auto-Outreach After Scraping
=========================================================================
Runs right after a scrape finishes (triggered from the post-scrape dialog
in the frontend). Chains three existing phases back-to-back so a single
click takes leads all the way from "just scraped" to "email sent":

    Audit (score leads) → Verify emails → Send emails

Each step updates the SAME ScrapingJob row's `status` field so the
frontend can poll GET /scraping-jobs and show live progress
("Scoring leads..." → "Verifying emails..." → "Sending emails...").

After this, the leads are picked up automatically by scheduler.py's
Email → SMS → Call follow-up funnel — no further manual steps needed.
"""

from datetime import datetime

from loguru import logger

from database import ScrapingJob, SyncSessionLocal
from scripts.auditor import run_auditor
from scripts.email_verify import run_email_verifier
from scripts.email_outreach import run_email_outreach


def run_post_scrape_pipeline(job_id: int, niche: str, city: str,
                              min_score: int = 0, dry_run: bool = False) -> None:
    """
    Args:
        job_id:    the ScrapingJob row to report progress on (reused from
                   the scrape step so the frontend can keep polling the
                   same id it already has)
        niche/city: scopes the final "send emails" step to this batch
        min_score: minimum score filter for who gets emailed
                   (0 = everyone in this batch with a verified email,
                    85 = hot leads only, or any custom number)
        dry_run:   if True, generates emails but doesn't actually send —
                   useful for testing the whole chain safely first
    """
    db = SyncSessionLocal()
    job = db.query(ScrapingJob).filter(ScrapingJob.id == job_id).first()
    if not job:
        logger.error(f"post_scrape_pipeline: job {job_id} not found — aborting")
        db.close()
        return

    try:
        # ── Step 1: Audit / score unaudited leads from THIS batch ────────
        # Scoped to niche+city so this run doesn't also sweep up unrelated
        # unaudited leads sitting in the DB from other scrapes.
        job.status = "auditing"
        db.commit()
        logger.info(f"[post-scrape] Job {job_id}: auditing (niche={niche}, city={city})...")
        run_auditor(lead_id=None, niche=niche, city=city)

        # ── Step 2: Verify emails for unverified leads from THIS batch ───
        job.status = "verifying_emails"
        db.commit()
        logger.info(f"[post-scrape] Job {job_id}: verifying emails (niche={niche}, city={city})...")
        run_email_verifier(lead_id=None, niche=niche, city=city)

        # ── Step 3: Send emails to eligible leads in this batch ──────────
        job.status = "sending_emails"
        db.commit()
        logger.info(f"[post-scrape] Job {job_id}: sending emails "
                    f"(niche={niche}, city={city}, min_score={min_score})...")
        run_email_outreach(min_score=min_score, dry_run=dry_run, niche=niche, city=city)

        # ── Done ──────────────────────────────────────────────────────────
        job.status = "outreach_done"
        job.completed_at = datetime.utcnow()
        db.commit()
        logger.info(f"[post-scrape] Job {job_id}: pipeline complete.")

    except Exception as e:
        logger.error(f"[post-scrape] Job {job_id} failed: {e}")
        job.status = "outreach_failed"
        db.commit()

    finally:
        db.close()