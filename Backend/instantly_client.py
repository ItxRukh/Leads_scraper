"""
instantly_client.py — The few Instantly API v2 calls we need
-------------------------------------------------------------
Base https://api.instantly.ai/api/v2, header `Authorization: Bearer <key>`.

    list_campaigns()                     GET  /campaigns
    create_lead(...)                     POST /leads          (adds a lead, with variables, to a campaign)
    delete_lead(id)                      DELETE /leads/{id}   (a blocked lead leaves the campaign)
    send_reply(...)                      POST /emails/reply   (answer a lead's reply in its thread)
    create_webhook(...)                  POST /webhooks       (events come back to /webhooks/instantly)

Every call raises InstantlyError with a short, readable message, so the admin page can show it as it is.
"""

from typing import Optional

import requests
from loguru import logger

from config import settings

V2_BASE = "https://api.instantly.ai/api/v2"
TIMEOUT_SECONDS = 20

CAMPAIGN_STATUS = {0: "Draft", 1: "Active", 2: "Paused", 3: "Completed", 4: "Running subsequences",
                   -99: "Account suspended", -1: "Accounts unhealthy", -2: "Bounce protect"}

# the events we act on (see webhooks/instantly_webhook.py)
WEBHOOK_EVENTS = ["reply_received", "email_bounced", "lead_unsubscribed", "email_sent", "lead_interested",
                  "lead_not_interested", "lead_meeting_booked", "email_opened"]


class InstantlyError(Exception):
    pass


def configured() -> bool:
    return bool(settings.INSTANTLY_API_KEY)


def base_url() -> str:
    """The v2 address. An old v1 address left in .env is ignored: v1 is a different API."""
    url = (settings.INSTANTLY_BASE_URL or V2_BASE).rstrip("/")
    return V2_BASE if "/v1" in url else url


def _request(method: str, path: str, *, params: Optional[dict] = None, json: Optional[dict] = None) -> dict:
    if not configured():
        raise InstantlyError("INSTANTLY_API_KEY is not set in Backend/.env")
    try:
        response = requests.request(method, f"{base_url()}{path}", params=params, json=json, timeout=TIMEOUT_SECONDS,
                                    headers={"Authorization": f"Bearer {settings.INSTANTLY_API_KEY}"})
    except requests.RequestException as e:
        raise InstantlyError(f"Could not reach Instantly: {e}") from e

    if response.status_code >= 400:
        try:
            body = response.json()
            detail = body.get("message") or body.get("error") or str(body)
        except ValueError:
            detail = response.text
        meaning = {401: "the API key was not accepted", 402: "the Instantly plan does not include API access",
                   429: "Instantly is rate limiting: try again in a minute"}.get(response.status_code, "")
        raise InstantlyError(f"Instantly said {response.status_code}{' (' + meaning + ')' if meaning else ''}: {str(detail)[:200]}")
    try:
        return response.json()
    except ValueError as e:
        raise InstantlyError("Instantly sent a reply that is not JSON") from e


def list_campaigns(limit: int = 100) -> list[dict]:
    data = _request("GET", "/campaigns", params={"limit": min(limit, 100)})
    return [{"id": c["id"], "name": c.get("name", ""), "status": c.get("status"),
             "status_label": CAMPAIGN_STATUS.get(c.get("status"), "Unknown")} for c in data.get("items", [])]


def create_lead(campaign_id: str, email: str, *, first_name: Optional[str] = None, last_name: Optional[str] = None,
                company_name: Optional[str] = None, website: Optional[str] = None, phone: Optional[str] = None,
                personalization: Optional[str] = None, custom_variables: Optional[dict] = None) -> str:
    """Adds the lead to the campaign and returns Instantly's id for it. A lead already in the workspace is not added twice."""
    body = {"campaign": campaign_id, "email": email, "skip_if_in_workspace": True,
            "custom_variables": custom_variables or {}}
    for key, value in (("first_name", first_name), ("last_name", last_name), ("company_name", company_name),
                       ("website", website), ("phone", phone), ("personalization", personalization)):
        if value:
            body[key] = value
    lead = _request("POST", "/leads", json=body)
    if not lead.get("id"):
        raise InstantlyError("Instantly did not return a lead id")
    return lead["id"]


def delete_lead(lead_id: str) -> None:
    """Removes a lead from Instantly, which ends its sequence."""
    _request("DELETE", f"/leads/{lead_id}")


def send_reply(reply_to_uuid: str, eaccount: str, subject: str, text: str, html_body: str) -> dict:
    """Answers an email in its own thread, from the mailbox that received it."""
    return _request("POST", "/emails/reply", json={"reply_to_uuid": reply_to_uuid, "eaccount": eaccount,
                                                   "subject": subject, "body": {"text": text, "html": html_body}})


def create_webhook(target_url: str, event_type: str, secret: str = "", name: Optional[str] = None) -> dict:
    body = {"target_hook_url": target_url, "event_type": event_type, "name": name or f"Leadwren: {event_type}"}
    if secret:
        body["headers"] = {"X-Leadwren-Secret": secret}
    return _request("POST", "/webhooks", json=body)


def list_webhooks() -> list[dict]:
    data = _request("GET", "/webhooks", params={"limit": 100})
    return data.get("items", []) if isinstance(data, dict) else []


def log_missing_setup() -> None:
    if not configured():
        logger.warning("[instantly] INSTANTLY_API_KEY is not set: sending through Instantly is off")
