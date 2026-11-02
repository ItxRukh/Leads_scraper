"""
pipeline_run.py — Everything that happens to a batch of new leads before a person has to look at it
---------------------------------------------------------------------------------------------------
    run_batch_pipeline(batch_id)   1. check every website, 2. correct the weakness, 3. check the email addresses,
                                   4. write the first email of every lead that is ready
    pipeline_status(db, batch_id)  where that is, for the progress panel

Used by the Import page. Nothing is sent: the emails are drafts that wait on the Approve emails page.
"""

from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

import email_verification
import first_email
import weakness_check
from database import Lead, SyncSessionLocal

RUNNING: set[int] = set()          # batches being processed in this server right now


def mark_running(batch_id: int) -> None:
    RUNNING.add(batch_id)


def run_batch_pipeline(batch_id: int, job_id: Optional[int] = None, db: Optional[Session] = None) -> bool:
    """The four steps, one after another. Returns True when all finished. One failing lead never stops the others."""
    from import_api import audit_batch

    own_session = db is None
    db = db or SyncSessionLocal()
    RUNNING.add(batch_id)
    try:
        audit_batch(batch_id, db=db)
        weakness_check.check_batch(batch_id, db)
        email_verification.verify_batch(batch_id, db=db)
        first_email.write_batch(db, batch_id)
        return True
    except Exception as e:
        logger.exception(f"[pipeline] batch {batch_id} failed: {e}")
        db.rollback()
        return False
    finally:
        RUNNING.discard(batch_id)
        if own_session:
            db.close()


def pipeline_status(db: Session, batch_id: int) -> dict:
    """stage: audit | verify | write while it runs, 'done' when everything is finished, 'stopped' if it ended early."""
    from import_api import audit_counts

    audit = audit_counts(db, batch_id)
    verify = email_verification.batch_status(db, batch_id)
    emails = first_email.counts(db, batch_id)
    leads = db.query(Lead).filter(Lead.import_batch_id == batch_id).all()
    weakness = {"with_weakness": sum(1 for lead in leads if (lead.attributes or {}).get("weakness")),
                "edited": sum(1 for lead in leads if (lead.attributes or {}).get("weakness_edited"))}

    if audit["audited"] < audit["total"]:
        pending = "audit"
    elif verify["checked"] < verify["to_check"]:
        pending = "verify"
    elif emails["written"] < emails["ready"]:
        pending = "write"
    else:
        pending = None

    if pending is None:
        stage = "done"
    elif batch_id in RUNNING:
        stage = pending
    else:
        stage = "stopped"
    return {"stage": stage, "pending": pending, "audit": audit, "verify": verify, "emails": emails, "weakness": weakness}
