"""
tracking_api.py — Email open tracking
-------------------------------------
Every email carries a tiny invisible image (only when EMAIL_OPEN_TRACKING is on and
PUBLIC_BASE_URL can be reached from outside). When the lead's mail app loads it, this
route records the open.

An "open" is a hint, not proof: some mail apps load images in advance, and some never do.
Loads within seconds of sending are ignored, since those are almost always scanners.
"""

import base64
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from database import EmailLog, OutreachMessage, PipelineStage, get_db

router = APIRouter(tags=["Tracking"])

MIN_SECONDS_AFTER_SEND = 20
_PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")   # 1x1 transparent GIF
_SENT_STAGES = (PipelineStage.SCRAPED, PipelineStage.LINKEDIN_ENRICHED, PipelineStage.AUDITED,
                PipelineStage.EMAIL_VERIFIED, PipelineStage.EMAIL_SENT)


def record_open(db: Session, token: str, now: Optional[datetime] = None) -> bool:
    """Returns True if this load counted as an open."""
    now = now or datetime.utcnow()
    msg = db.query(OutreachMessage).filter(OutreachMessage.tracking_token == token).first()
    if msg is None or msg.sent_at is None or now - msg.sent_at < timedelta(seconds=MIN_SECONDS_AFTER_SEND):
        return False

    msg.open_count = (msg.open_count or 0) + 1
    msg.opened_at = msg.opened_at or now

    log = db.query(EmailLog).filter(EmailLog.lead_id == msg.lead_id).order_by(EmailLog.sent_at.desc()).first()
    if log is not None and not log.email_opened:
        log.email_opened, log.opened_at = True, now
    if msg.lead.pipeline_stage in _SENT_STAGES:
        msg.lead.pipeline_stage = PipelineStage.EMAIL_OPENED
    db.commit()
    return True


@router.get("/track/open/{token}.gif")
def open_pixel(token: str, db: Session = Depends(get_db)):
    record_open(db, token)
    return Response(content=_PIXEL, media_type="image/gif",
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})
