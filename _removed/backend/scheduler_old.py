"""
scheduler.py — Auto Follow-Up Scheduler (Email → SMS → Call funnel)
=====================================================================
Runs entirely in the background, inside the same FastAPI process.
No separate worker, no cron, no extra infra — just APScheduler.

What it does, every FOLLOWUP_SCHEDULER_INTERVAL_HOURS hours:
  1. Finds leads whose email was sent 3+ days ago with no reply
     → sends them an automatic SMS follow-up.
  2. Finds leads whose SMS follow-up was sent 2+ days ago with no reply
     (and are warm/hot) → places an automatic voice call follow-up.
  3. Smart First-Touch Routing: leads with NO email but a phone number
     would otherwise sit forever waiting on step 1 (which needs an email
     to have been sent first). This step finds them directly and reaches
     out immediately — SMS for warm+ leads, a call for hot leads — instead
     of leaving them stuck with no outreach at all.

Both steps reuse the exact same functions the manual dashboard buttons
call (run_sms_followup, run_voice_agent) — this file just decides WHEN
to call them instead of a human clicking a button.

Wired into app.py's startup event — nothing to run separately.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from config import settings
from scripts.sms_outreach import run_sms_followup
from scripts.voice_agent import run_voice_agent

_scheduler: BackgroundScheduler | None = None


def run_followup_cycle() -> None:
    """
    One full pass of the auto follow-up funnel.
    Safe to call as often as you like — every eligibility query already
    excludes leads that were already contacted at that step, so nothing
    gets double-sent.
    """
    if not settings.AUTO_FOLLOWUP_ENABLED:
        logger.info("[scheduler] Auto follow-up is disabled in settings — skipping cycle")
        return

    logger.info("[scheduler] Running auto follow-up cycle...")

    try:
        # Step 2: Email → SMS (leads with no reply, EMAIL_FOLLOWUP_WAIT_DAYS+ old)
        run_sms_followup(trigger="auto", dry_run=False)
    except Exception as e:
        logger.error(f"[scheduler] SMS follow-up step failed: {e}")

    try:
        # Step 3: SMS → Call (leads with no reply, SMS_FOLLOWUP_WAIT_DAYS+ old)
        run_voice_agent(trigger="followup", dry_run=False)
    except Exception as e:
        logger.error(f"[scheduler] Call follow-up step failed: {e}")

    try:
        # Smart First-Touch Routing (call side): hot leads with no email and
        # zero prior contact — don't wait for a funnel that can't start.
        run_voice_agent(trigger="no_email_first_touch", dry_run=False)
    except Exception as e:
        logger.error(f"[scheduler] First-touch call routing failed: {e}")

    logger.info("[scheduler] Auto follow-up cycle complete.")


def start_scheduler() -> None:
    """
    Called once from app.py's @app.on_event("startup").
    Starts the background job — does nothing if already running.
    """
    global _scheduler

    if _scheduler is not None:
        logger.warning("[scheduler] Scheduler already running — skipping re-init")
        return

    if not settings.AUTO_FOLLOWUP_ENABLED:
        logger.info("[scheduler] AUTO_FOLLOWUP_ENABLED=False — scheduler not started")
        return

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        run_followup_cycle,
        trigger=IntervalTrigger(hours=settings.FOLLOWUP_SCHEDULER_INTERVAL_HOURS),
        id="auto_followup_cycle",
        # Run once shortly after startup too, so you don't wait a full
        # interval to see it work the first time.
        next_run_time=None,
        replace_existing=True,
        max_instances=1,  # never let two cycles overlap
    )
    _scheduler.start()
    logger.info(
        f"[scheduler] Auto follow-up scheduler started — "
        f"checking every {settings.FOLLOWUP_SCHEDULER_INTERVAL_HOURS}h "
        f"(email→sms after {settings.EMAIL_FOLLOWUP_WAIT_DAYS}d, "
        f"sms→call after {settings.SMS_FOLLOWUP_WAIT_DAYS}d)"
    )


def stop_scheduler() -> None:
    """Called from app.py's shutdown event — clean exit, no orphaned threads."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[scheduler] Auto follow-up scheduler stopped")