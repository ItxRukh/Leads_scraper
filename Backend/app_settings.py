# ─── app_settings.py ─────────────────────────────────────────────────────────
# Tiny persisted key/value store for settings configured on the frontend
# Settings page (Lead Scoring thresholds, scraper defaults, etc).
#
# Kept deliberately simple (a JSON file) instead of a DB table since these
# are a handful of admin-level config values, not per-record data.
# ─────────────────────────────────────────────────────────────────────────────

import json
import os
from threading import Lock

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "data", "app_settings.json")

# Mirrors Frontend/src/pages/Settings.jsx DEFAULTS for the fields the
# backend actually needs to act on.
DEFAULTS = {
    "companyName":   "EcomDrop.ai",
    "adminName":     "",
    "maxLeads":      50,
    "autoAudit":     True,
    "hotThreshold":  70,
    "warmThreshold": 40,
    "exportFormat":  "csv",
    # Lowest tier that enters the Outreach Queue: "warm" = Hot + Warm, "hot" = Hot only
    "queueMinTier":  "warm",
    "autoExport":    False,
    # Feature 7 — Daily Digest Notification: last time the admin acknowledged
    # the weekly "no email found" digest in the bell dropdown. Compared
    # against the digest's rolling 7-day window to decide read/unread.
    "digestReadAt":  None,
}

_lock = Lock()


def get_app_settings() -> dict:
    """Read merged settings (defaults + saved overrides)."""
    with _lock:
        if not os.path.exists(SETTINGS_FILE):
            return dict(DEFAULTS)
        try:
            with open(SETTINGS_FILE, "r") as f:
                saved = json.load(f)
            return {**DEFAULTS, **saved}
        except Exception:
            return dict(DEFAULTS)


def save_app_settings(data: dict) -> dict:
    """Merge + persist new settings, returns the full merged settings dict."""
    with _lock:
        current = dict(DEFAULTS)
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, "r") as f:
                    current = {**DEFAULTS, **json.load(f)}
            except Exception:
                pass
        merged = {**current, **data}
        os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(merged, f, indent=2)
        return merged


def get_score_thresholds() -> tuple[int, int]:
    """
    Returns (hot_threshold, warm_threshold) from the live Settings page
    values (app_settings.json), falling back to DEFAULTS if unset.

    Use this instead of config.settings.HOT_LEAD_MIN_SCORE /
    WARM_LEAD_MIN_SCORE anywhere a lead's tier is calculated, so the
    Settings -> Lead Scoring sliders actually take effect.
    """
    cfg = get_app_settings()
    hot = int(cfg.get("hotThreshold", DEFAULTS["hotThreshold"]))
    warm = int(cfg.get("warmThreshold", DEFAULTS["warmThreshold"]))
    return hot, warm