"""
config.py — Centralized Configuration
--------------------------------------
All API keys and app settings are loaded from the .env file.

Why pydantic-settings?
  - Automatically reads from .env file
  - Type-checks every value (e.g. int, float, str)
  - Raises clear errors if a required key is missing
  - One import gives you all settings anywhere in the project

Usage:
    from config import settings
    print(settings.GROQ_API_KEY)
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All settings are loaded from environment variables or the .env file.
    Field names must exactly match the keys in .env.
    """

    # ── Pydantic config: tells it to read from .env file ───────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,        # GROQ_API_KEY != groq_api_key
        extra="ignore",             # Ignore any extra keys in .env
    )

    # ── Scraping APIs ───────────────────────────────────────────────────────
    GOOGLE_PLACES_API_KEY: str = ""
    GOOGLE_PLACES_BASE_URL: str = "https://maps.googleapis.com/maps/api/place"

    SERPAPI_KEY: str = ""
    SERPAPI_BASE_URL: str = "https://serpapi.com/search"

    YELP_API_KEY: str = ""
    YELP_BASE_URL: str = "https://api.yelp.com/v3"

    # ── LinkedIn Enrichment ─────────────────────────────────────────────────
    PHANTOMBUSTER_API_KEY: str = ""
    PHANTOMBUSTER_BASE_URL: str = "https://api.phantombuster.com/api/v2"

    # ── Email Verification ──────────────────────────────────────────────────
    ZEROBOUNCE_API_KEY: str = ""
    ZEROBOUNCE_BASE_URL: str = "https://api.zerobounce.net/v2"

    # ── Email Outreach (Instantly) ──────────────────────────────────────────
    INSTANTLY_API_KEY: str = ""
    INSTANTLY_BASE_URL: str = "https://api.instantly.ai/api/v1"
    EMAILS_PER_DAY: int = 50

    # ── AI Provider (Groq) ──────────────────────────────────────────────────
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_MODEL: str = "llama-3.3-70b-versatile"

    # ── Voice Calls (Twilio custom agent) ──────────────────────────────────
    VAPI_API_KEY: str = ""
    VAPI_BASE_URL: str = "https://api.vapi.ai"

    # ── Text-to-Speech (ElevenLabs) — used by the custom voice agent ───────
    ELEVENLABS_API_KEY: str = ""

    # ── Groq model just for the voice agent (faster = less call latency) ───
    GROQ_VOICE_MODEL: str = "llama-3.1-8b-instant"

    # ── Public base URL for this backend (ngrok URL in dev, real domain in prod)
    # Used so Twilio knows where to send webhooks / connect WebSocket streams.
    PUBLIC_BASE_URL: str = ""

    # ── Public-facing landing page (the supplier-facing site with the
    # revenue calculator). This is DIFFERENT from PUBLIC_BASE_URL above —
    # that one is the backend API's ngrok URL, this is the actual website
    # link we put in emails/SMS for leads to visit.
    PUBLIC_SITE_URL: str = "https://ecomdropship.ai"

    # ── SMS + Calls (Twilio) ────────────────────────────────────────────────
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""
    TWILIO_WHATSAPP_NUMBER: str = "whatsapp:+14155238886"

    # ── Claude (Anthropic) — for email writing ──────────────────────────────
    ANTHROPIC_API_KEY: str = ""

    # ── Database ────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./data/leads.db"

    # ── App ─────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"

    # ── Error Monitoring (optional) ─────────────────────────────────────────
    # Leave blank to skip Sentry entirely — rotating file logs (logs/app.log)
    # always run regardless, so you're never fully blind even without this.
    # Get a free DSN at https://sentry.io if you want crash alerts.
    SENTRY_DSN: str = ""

    # ── Lead Scoring Thresholds ─────────────────────────────────────────────
    HOT_LEAD_MIN_SCORE: int = 85
    WARM_LEAD_MIN_SCORE: int = 40

    # ── Old Email → SMS → Call funnel (used only by the standalone scripts) ──
    EMAIL_FOLLOWUP_WAIT_DAYS: int = 3
    SMS_FOLLOWUP_WAIT_DAYS: int = 2

    # ── Dispatcher: prepares messages, sends due emails, reads replies ──────
    # The Outreach Queue decides who is contacted and when. This worker does
    # the preparing and (for channels set to Auto) the sending.
    DISPATCHER_ENABLED: bool = True
    DISPATCHER_INTERVAL_SECONDS: int = 60
    INBOX_POLL_MINUTES: int = 3

    # ── Email sending and reading (Gmail with an App Password) ──────────────
    # Google account -> Security -> 2-Step Verification -> App passwords.
    EMAIL_ADDRESS: str = ""
    EMAIL_APP_PASSWORD: str = ""
    EMAIL_FROM_NAME: str = ""
    EMAIL_SMTP_HOST: str = "smtp.gmail.com"
    EMAIL_SMTP_PORT: int = 587
    EMAIL_IMAP_HOST: str = "imap.gmail.com"

    # Open tracking adds a tiny invisible image to each email; it needs PUBLIC_BASE_URL to be an
    # address the lead's mail app can reach (not localhost). Off by default.
    EMAIL_OPEN_TRACKING: bool = False

    # ── Scraping Defaults ───────────────────────────────────────────────────
    DEFAULT_MIN_RATING: float = 3.5
    DEFAULT_MIN_REVIEWS: int = 10
    MAX_PAGES_PER_SOURCE: int = 3

    # ── Crawler Settings ────────────────────────────────────────────────────
    CRAWLER_TIMEOUT: int = 10
    CRAWLER_MAX_PAGES: int = 3
    CRAWLER_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()