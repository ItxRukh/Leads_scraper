"""
profile_schema.py — Structure and validation of an Industry Profile
--------------------------------------------------------------------
An Industry Profile is stored as data (IndustryProfile.config, JSON). This
file defines what that JSON must look like, so a profile edited from the
admin panel can never break scoring or scraping.

Config layout:

    {
      "search":  {"query_template": "{niche} {city}", "default_niche": "..."},
      "offer":   {"summary": "...", "tone": "...", "language": "en"},
      "scoring": {"hot_threshold": null, "warm_threshold": null, "rules": [...]},
      "sequence": {"steps": [...]}
    }

A scoring rule says "if THIS is true for a lead, add N points":

    kind      what it checks                       params
    --------  -----------------------------------  ------------------------------
    field     a stored lead/attribute value        field, op, value
    keyword   words on the lead's website text     source, any (list), none (list)
    regex     a pattern in the website text        source, pattern
    ai        a yes/no question answered by AI     question
    builtin   a named detector already in the code name
    intent    something the lead did after outreach event

`group` is "base" (counts toward the 100-point base score) or "bonus".
`requires` lists data the rule needs ("website", "email"…); when that data is
missing the rule is reported as "not checked" instead of "failed".
"""

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Detectors implemented in utils/crawler.py (kept so the original dropship
# profile scores exactly as before). New profiles should prefer keyword/ai.
BUILTIN_DETECTORS = {
    "dropship_mentioned",
    "processing_fast",
    "delivery_fast",
    "sells_on_marketplace",
}

FIELD_OPS = {">=", "<=", ">", "<", "==", "!=", "exists", "in", "contains"}
KEYWORD_SOURCES = {"website_text"}
INTENT_EVENTS = {"email_opened", "email_clicked", "replied", "call_connected", "meeting_booked"}
REQUIREMENTS = {"website", "email", "phone"}


class ScoringRule(BaseModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=200)
    kind: Literal["field", "keyword", "regex", "ai", "builtin", "intent"]
    points: int = Field(ge=-100, le=100)          # negative = penalty
    group: Literal["base", "bonus"] = "base"
    params: dict[str, Any] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)

    @field_validator("requires")
    @classmethod
    def _known_requirements(cls, v):
        unknown = set(v) - REQUIREMENTS
        if unknown:
            raise ValueError(f"unknown requirement(s): {sorted(unknown)}")
        return v

    @model_validator(mode="after")
    def _check_params(self):
        p, kind = self.params, self.kind

        if kind == "field":
            if not p.get("field"):
                raise ValueError("field rule needs params.field")
            if p.get("op") not in FIELD_OPS:
                raise ValueError(f"field rule op must be one of {sorted(FIELD_OPS)}")
            if p["op"] != "exists" and "value" not in p:
                raise ValueError("field rule needs params.value")

        elif kind == "keyword":
            if p.get("source", "website_text") not in KEYWORD_SOURCES:
                raise ValueError(f"keyword source must be one of {sorted(KEYWORD_SOURCES)}")
            words = p.get("any")
            if not isinstance(words, list) or not words or not all(isinstance(w, str) and w.strip() for w in words):
                raise ValueError("keyword rule needs a non-empty params.any list of words")
            if "none" in p and not isinstance(p["none"], list):
                raise ValueError("params.none must be a list")

        elif kind == "regex":
            pattern = p.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError("regex rule needs params.pattern")
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid regex: {e}")

        elif kind == "ai":
            if not isinstance(p.get("question"), str) or not p["question"].strip():
                raise ValueError("ai rule needs params.question")

        elif kind == "builtin":
            if p.get("name") not in BUILTIN_DETECTORS:
                raise ValueError(f"builtin rule name must be one of {sorted(BUILTIN_DETECTORS)}")

        elif kind == "intent":
            if p.get("event") not in INTENT_EVENTS:
                raise ValueError(f"intent rule event must be one of {sorted(INTENT_EVENTS)}")

        return self


class ScoringConfig(BaseModel):
    # None = fall back to the global thresholds on the Settings page
    hot_threshold: Optional[int] = Field(default=None, ge=0, le=1000)
    warm_threshold: Optional[int] = Field(default=None, ge=0, le=1000)
    rules: list[ScoringRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self):
        keys = [r.key for r in self.rules]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate rule key(s): {sorted(dupes)}")
        if (
            self.hot_threshold is not None
            and self.warm_threshold is not None
            and self.warm_threshold > self.hot_threshold
        ):
            raise ValueError("warm_threshold cannot be higher than hot_threshold")
        return self


class SearchConfig(BaseModel):
    # Placeholders: {niche} and {city}
    query_template: str = Field(default="{niche} {city}", min_length=1, max_length=300)
    default_niche: Optional[str] = None

    @field_validator("query_template")
    @classmethod
    def _has_placeholders(cls, v):
        if "{niche}" not in v and "{city}" not in v:
            raise ValueError("query_template must contain {niche} and/or {city}")
        return v


class OfferConfig(BaseModel):
    summary: str = ""        # what we offer, in a few sentences
    tone: str = "friendly, professional"
    language: str = "en"
    booking_link: str = Field(default="", max_length=500)   # where an interested lead can book a call
    faq: str = Field(default="", max_length=6000)           # questions and answers the AI may use in replies



# ─── Follow-up sequence ───────────────────────────────────────────────────────

CHANNELS = ("email", "whatsapp", "call")


class SequenceStep(BaseModel):
    """
    One contact attempt. `delay_days` counts from the moment the previous step
    was actually done (not from when it was planned). A step is skipped for a
    lead that is below `min_tier` or lacks the contact it needs (email for an
    email step, phone for WhatsApp and calls).
    """
    key: str = Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(default="", max_length=80)
    channel: Literal["email", "whatsapp", "call"]
    delay_days: float = Field(default=0, ge=0, le=90)
    min_tier: Literal["hot", "warm", "cold"] = "warm"   # lowest tier that gets this step


def _default_steps() -> list[SequenceStep]:
    return [
        SequenceStep(key="email_1",    label="First email",        channel="email",    delay_days=0, min_tier="cold"),
        SequenceStep(key="email_2",    label="Follow-up email",    channel="email",    delay_days=3, min_tier="cold"),
        SequenceStep(key="whatsapp_1", label="WhatsApp message",   channel="whatsapp", delay_days=1, min_tier="warm"),
        SequenceStep(key="email_3",    label="New-angle email",    channel="email",    delay_days=3, min_tier="cold"),
        SequenceStep(key="call_1",     label="Call",               channel="call",     delay_days=2, min_tier="warm"),
        SequenceStep(key="email_4",    label="Closing note",       channel="email",    delay_days=5, min_tier="cold"),
    ]


class SequenceConfig(BaseModel):
    steps: list[SequenceStep] = Field(default_factory=_default_steps, max_length=12)

    @model_validator(mode="after")
    def _unique_keys(self):
        keys = [st.key for st in self.steps]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate step key(s): {sorted(dupes)}")
        return self


class ProfileConfig(BaseModel):
    search: SearchConfig = Field(default_factory=SearchConfig)
    offer: OfferConfig = Field(default_factory=OfferConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    sequence: SequenceConfig = Field(default_factory=SequenceConfig)


class ProfileIn(BaseModel):
    """Body for creating or updating a profile."""
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = None
    target_type: Literal["business", "person"] = "business"
    config: ProfileConfig = Field(default_factory=ProfileConfig)


class ProfileOut(BaseModel):
    id: int
    name: str
    slug: str
    description: Optional[str]
    target_type: str
    is_active: bool
    version: int
    lead_count: int = 0
    config: ProfileConfig
