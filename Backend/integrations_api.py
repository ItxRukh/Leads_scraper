"""
integrations_api.py — The Integrations page: Instantly, email verification, GoHighLevel
----------------------------------------------------------------------------------------
Keys stay in Backend/.env and are never sent to the browser; this page only shows whether each one is set.

    GET  /integrations/status                  what is connected, and what still blocks sending
    GET  /integrations/instantly/campaigns     the campaigns in the Instantly account
    POST /integrations/instantly/campaign      choose the campaign leads are added to
    GET  /integrations/instantly/template      the steps the campaign must contain (copy them into Instantly)
    POST /integrations/instantly/webhooks      register the event webhooks in Instantly
    POST /integrations/provider                "instantly" or "smtp"
    POST /integrations/company                 postal address and verification choices
    POST /integrations/ghl/test                send a marked test contact to GoHighLevel
    POST /integrations/ghl/send/{lead_id}      send one lead to GoHighLevel now
    POST /integrations/send-now                hand over approved emails now instead of waiting for the next minute
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import email_verification
import ghl_sync
import instantly_client
import instantly_handoff
import message_service
from app_settings import get_app_settings, save_app_settings
from config import settings
from database import Lead, get_db
from industry_profiles import _SEQUENCE

router = APIRouter(prefix="/integrations", tags=["Integrations"])


def _blockers(cfg: dict) -> list[str]:
    if cfg.get("sendProvider") != "instantly":
        return []
    problems = []
    if not instantly_client.configured():
        problems.append("Add INSTANTLY_API_KEY to Backend/.env")
    if not instantly_handoff.campaign_id():
        problems.append("Choose the Instantly campaign")
    if not instantly_handoff.company()[1]:
        problems.append("Add your company's postal address (required in every commercial email)")
    if not settings.DISPATCHER_ENABLED:
        problems.append("DISPATCHER_ENABLED is off in Backend/.env, so nothing is written or handed over automatically")
    return problems


@router.get("/status")
def status():
    cfg = get_app_settings()
    name, address = instantly_handoff.company()
    return {
        "provider": "instantly" if cfg.get("sendProvider") == "instantly" else "smtp",
        "instantly": {"configured": instantly_client.configured(), "campaign_id": instantly_handoff.campaign_id(),
                      "webhook_secret_set": bool(settings.INSTANTLY_WEBHOOK_SECRET)},
        "verification": {"paid_provider": email_verification.paid_provider(),
                         "verify_before_send": bool(cfg.get("verifyBeforeSend", True)),
                         "send_to_catch_all": bool(cfg.get("sendToCatchAll", False))},
        "ghl": {"configured": ghl_sync.configured(), "push_labels": cfg.get("ghlPushLabels", [])},
        "company": {"name": name, "address": address},
        "public_base_url": settings.PUBLIC_BASE_URL,
        "dispatcher_enabled": bool(settings.DISPATCHER_ENABLED),
        "blockers": _blockers(cfg),
    }


def _instantly(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except instantly_client.InstantlyError as e:
        raise HTTPException(502, str(e))


@router.get("/instantly/campaigns")
def campaigns():
    return _instantly(instantly_client.list_campaigns)


class CampaignBody(BaseModel):
    campaign_id: str = Field(min_length=8, max_length=64)


@router.post("/instantly/campaign")
def choose_campaign(body: CampaignBody):
    save_app_settings({"instantlyCampaignId": body.campaign_id.strip()})
    return {"campaign_id": body.campaign_id.strip()}


@router.get("/instantly/template")
def template():
    delays = [step["delay_days"] for step in _SEQUENCE[1:]]
    return {"steps": instantly_handoff.campaign_template(delays, len(_SEQUENCE)),
            "variables": ["lead_id", "industry", "audit_score", "top_gap_1", "top_gap_2", "top_gap_3", "email_steps"]}


class WebhookBody(BaseModel):
    public_url: Optional[str] = None                 # defaults to PUBLIC_BASE_URL


@router.post("/instantly/webhooks")
def register_webhooks(body: WebhookBody):
    base = (body.public_url or settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not base.startswith("https://") or "localhost" in base or "127.0.0.1" in base:
        raise HTTPException(422, "Instantly can only reach a public https address. Set PUBLIC_BASE_URL in Backend/.env "
                                 "(your domain, or a tunnel such as ngrok while testing).")
    if not settings.INSTANTLY_WEBHOOK_SECRET:
        raise HTTPException(422, "Set INSTANTLY_WEBHOOK_SECRET in Backend/.env first (any long random text), "
                                 "so only Instantly can send events.")
    target = f"{base}/webhooks/instantly"
    created, failed = [], []
    for event in instantly_client.WEBHOOK_EVENTS:
        try:
            instantly_client.create_webhook(target, event, settings.INSTANTLY_WEBHOOK_SECRET)
            created.append(event)
        except instantly_client.InstantlyError as e:
            failed.append({"event": event, "error": str(e)})
    return {"target": target, "created": created, "failed": failed}


class ProviderBody(BaseModel):
    provider: str = Field(pattern="^(instantly|smtp)$")


@router.post("/provider")
def set_provider(body: ProviderBody):
    save_app_settings({"sendProvider": body.provider})
    return {"provider": body.provider}


class CompanyBody(BaseModel):
    company_name: Optional[str] = Field(default=None, max_length=120)
    address: Optional[str] = Field(default=None, max_length=300)
    verify_before_send: Optional[bool] = None
    send_to_catch_all: Optional[bool] = None


@router.post("/company")
def set_company(body: CompanyBody):
    update = {}
    if body.company_name is not None:
        update["companyName"] = body.company_name.strip()
    if body.address is not None:
        update["companyAddress"] = " ".join(body.address.split())
    if body.verify_before_send is not None:
        update["verifyBeforeSend"] = body.verify_before_send
    if body.send_to_catch_all is not None:
        update["sendToCatchAll"] = body.send_to_catch_all
    save_app_settings(update)
    return status()


@router.post("/ghl/test")
def ghl_test():
    return ghl_sync.push_test()


@router.post("/ghl/send/{lead_id}")
def ghl_send(lead_id: int, db: Session = Depends(get_db)):
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(404, "Lead not found")
    if lead.do_not_contact:
        raise HTTPException(409, "This lead is on the do-not-contact list")
    return ghl_sync.push(db, lead, "manual", force=True)


@router.post("/send-now")
def send_now(db: Session = Depends(get_db)):
    return message_service.send_due_emails(db)
