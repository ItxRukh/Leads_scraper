"""
scheduler.py — The dispatcher: background work that keeps the outreach moving
------------------------------------------------------------------------------
Runs inside the API process (APScheduler), nothing separate to start.

Every DISPATCHER_INTERVAL_SECONDS (default 60):
  * stop the sequence of leads that replied or opted out
  * put emails that were picked up but never finished back in the approved pile
  * read new replies (what does the lead want?) and write answer drafts
  * contact "not now" leads again when their revisit date has come
  * write messages for steps that are due (channels in Assisted or Auto mode)
  * send approved emails that are due

Every INBOX_POLL_MINUTES (default 3): read replies and bounces from the inbox.

Nothing is sent without approval unless the channel is set to Auto in the
Outreach queue's sending rules. Email is handed to Instantly (or sent over SMTP). SMS and calls
are not made here: they happen in GoHighLevel once a lead has replied.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

import email_inbox
import message_service
import reply_ai
import scheduling
from config import settings
from database import SyncSessionLocal

_scheduler: BackgroundScheduler | None = None


def _run_step(name: str, fn) -> None:
    """One failing step must not stop the others."""
    try:
        fn()
    except Exception as e:
        logger.error(f"[dispatcher] {name} failed: {e}")


def run_tick() -> None:
    with SyncSessionLocal() as db:
        _run_step("stop replied leads", lambda: scheduling.refresh_stops(db))
        _run_step("release stale claims", lambda: message_service.release_stale_claims(db))
        _run_step("understand replies", lambda: reply_ai.process_new_replies(db))
        _run_step("reopen not-now leads", lambda: scheduling.reopen_snoozed(db))
        _run_step("prepare messages", lambda: message_service.ensure_drafts(db))
        _run_step("send emails", lambda: message_service.send_due_emails(db))


def run_inbox_poll() -> None:
    with SyncSessionLocal() as db:
        _run_step("read inbox", lambda: email_inbox.poll_inbox(db))


def start_scheduler() -> None:
    """Called once from app.py's startup event. Does nothing if already running or disabled."""
    global _scheduler
    if _scheduler is not None:
        return
    if not settings.DISPATCHER_ENABLED:
        logger.info("[dispatcher] DISPATCHER_ENABLED=False — not started")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(run_tick, IntervalTrigger(seconds=settings.DISPATCHER_INTERVAL_SECONDS),
                       id="dispatcher_tick", max_instances=1, coalesce=True, replace_existing=True)
    _scheduler.add_job(run_inbox_poll, IntervalTrigger(minutes=settings.INBOX_POLL_MINUTES),
                       id="inbox_poll", max_instances=1, coalesce=True, replace_existing=True)
    _scheduler.start()
    logger.info(f"[dispatcher] started: every {settings.DISPATCHER_INTERVAL_SECONDS}s, "
                f"inbox every {settings.INBOX_POLL_MINUTES} min")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[dispatcher] stopped")
