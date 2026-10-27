"""
pipeline_run.py — Everything that happens to a batch of new leads before a person has to look at it
---------------------------------------------------------------------------------------------------
    run_batch_pipeline(batch_id)   check every website and score the lead, then check the Hot and Warm leads' emails
    pipeline_status(db, batch_id)  where that is, for the progress panel

Used by the Import page ("Check websites and emails"), and after a Google Maps scrape. Nothing is sent: the next
step (drafts, approval, hand-over to Instantly) starts by itself for Hot and Warm leads and waits for approval.
"""

from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

import email_verification
from database import ScrapingJob, SyncSessionLocal


def _set_job(db: Session, job_id: Optional[int], status: str) -> None:
    """A scrape job's status drives the progress dialog on the Leads page."""
    job = db.get(ScrapingJob, job_id) if job_id else None
    if job is not None:
        job.status = status
        db.commit()


def run_batch_pipeline(batch_id: int, job_id: Optional[int] = None, db: Optional[Session] = None) -> bool:
    """Audit, then verify. Returns True when both finished. One failing lead never stops the others."""
    from import_api import audit_batch

    own_session = db is None
    db = db or SyncSessionLocal()
    try:
        _set_job(db, job_id, "auditing")
        audit_batch(batch_id, db=db)
        _set_job(db, job_id, "verifying_emails")
        email_verification.verify_batch(batch_id, db=db)
        _set_job(db, job_id, "outreach_done")
        return True
    except Exception as e:
        logger.exception(f"[pipeline] batch {batch_id} failed: {e}")
        db.rollback()
        _set_job(db, job_id, "outreach_failed")
        return False
    finally:
        if own_session:
            db.close()


def pipeline_status(db: Session, batch_id: int) -> dict:
    from import_api import audit_counts

    audit = audit_counts(db, batch_id)
    verify = email_verification.batch_status(db, batch_id)
    if audit["audited"] < audit["total"]:
        stage = "audit"
    elif verify["checked"] < verify["to_check"]:
        stage = "verify"
    else:
        stage = "done"
    return {"stage": stage, "audit": audit, "verify": verify}
