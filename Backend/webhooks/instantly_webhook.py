"""
webhooks/instantly_webhook.py — Where Instantly reports what happened to our leads
-----------------------------------------------------------------------------------
Instantly (API v2) POSTs a JSON event here: a reply, a bounce, an unsubscribe, an email that went out...
The Integrations page can register the webhooks for you (POST /integrations/instantly/webhooks). They send the
header `X-Leadwren-Secret` when INSTANTLY_WEBHOOK_SECRET is set; a request without the right secret is refused.

What each event does is in instantly_events.py.
"""

import hmac

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from config import settings
from database import SyncSessionLocal
from instantly_events import handle_event

router = APIRouter()


@router.post("/webhooks/instantly", tags=["Webhooks"])
async def instantly_webhook(request: Request):
    secret = settings.INSTANTLY_WEBHOOK_SECRET
    if secret and not hmac.compare_digest(request.headers.get("x-leadwren-secret", ""), secret):
        raise HTTPException(status_code=401, detail="Wrong or missing webhook secret")

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="The body is not JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="The body must be a JSON object")

    db = SyncSessionLocal()
    try:
        return handle_event(db, payload)
    except Exception as e:                      # Instantly would retry a 500 forever; log it and answer
        logger.exception(f"[instantly] could not handle event: {e}")
        db.rollback()
        return {"status": "error"}
    finally:
        db.close()
