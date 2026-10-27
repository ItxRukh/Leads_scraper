"""
scoring_engine.py — Scores a lead by running its Industry Profile's rules
-------------------------------------------------------------------------
    result = score_lead(db, lead)        # pure: reads the lead, changes nothing
    apply_result(lead, result)           # writes score, tier, signals, breakdown

A rule that cannot be checked (for example the website could not be crawled)
is reported as "not_checked" and earns 0, so the score is flagged incomplete
instead of looking like the business failed the check.

Rule definitions and their validation live in profile_schema.py.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy.orm import Session

from app_settings import get_score_thresholds
from database import DNCCrud, IndustryProfile, Lead, LeadTier, PipelineStage, CallOutcome
from profile_schema import ProfileConfig, ScoringRule
from utils.domain import extract_domain

MATCHED, NOT_MATCHED, NOT_CHECKED = "matched", "not_matched", "not_checked"

# Lead columns that the crawler / AI step fills in, one per builtin detector
_BUILTIN_COLUMNS = {
    "dropship_mentioned": "dropship_mentioned",
    "processing_fast": "processing_fast",
    "delivery_fast": "delivery_fast",
    "sells_on_marketplace": "sells_on_marketplace",
}


@dataclass
class RuleResult:
    key: str
    label: str
    kind: str
    group: str
    points: int           # points the rule is worth
    status: str           # matched | not_matched | not_checked
    earned: int = 0
    note: str = ""        # why it could not be checked
    fix: str = ""         # the workflow of ours that addresses it (rule.params["fix"])


@dataclass
class ScoreResult:
    profile_id: Optional[int]
    profile_name: str
    profile_version: int
    base: int = 0
    bonus: int = 0
    max_base: int = 0
    tier: LeadTier = LeadTier.COLD
    results: list[RuleResult] = field(default_factory=list)
    incomplete: bool = False
    incomplete_reason: Optional[str] = None
    on_dnc: bool = False
    summary: str = ""
    signals: dict[str, Optional[bool]] = field(default_factory=dict)

    @property
    def final(self) -> int:
        return self.base + self.bonus

    def top_gaps(self, limit: int = 3) -> list[dict]:
        """The base rules that matched, biggest first: what a message to this lead can point at."""
        matched = [r for r in self.results if r.group == "base" and r.status == MATCHED and r.points > 0]
        matched.sort(key=lambda r: -r.points)
        return [{"key": r.key, "label": r.label, "points": r.points, "fix": r.fix} for r in matched[:limit]]

    def breakdown(self) -> dict:
        return {
            "profile": {"id": self.profile_id, "name": self.profile_name, "version": self.profile_version},
            "base": self.base, "bonus": self.bonus, "final": self.final,
            "max_base": self.max_base, "tier": self.tier.value,
            "incomplete": self.incomplete, "on_dnc": self.on_dnc,
            "rules": [
                {"key": r.key, "label": r.label, "kind": r.kind, "group": r.group,
                 "points": r.points, "earned": r.earned, "status": r.status, "note": r.note, "fix": r.fix}
                for r in self.results
            ],
        }


# ─── Profile lookup ───────────────────────────────────────────────────────────

def resolve_profile(db: Session, lead: Lead) -> Optional[IndustryProfile]:
    """The lead's own profile, else the default one, else None."""
    if lead.profile_id:
        profile = db.get(IndustryProfile, lead.profile_id)
        if profile:
            return profile
    from profile_service import DEFAULT_PROFILE_SLUG
    return db.query(IndustryProfile).filter(IndustryProfile.slug == DEFAULT_PROFILE_SLUG).first()


# ─── Rule evaluation ──────────────────────────────────────────────────────────

def _lead_field(lead: Lead, name: str) -> Any:
    if hasattr(lead, name) and name not in ("attributes", "signals"):
        return getattr(lead, name)
    return (lead.attributes or {}).get(name)


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "exists":
        return actual not in (None, "", [], {}, False)
    if actual is None:
        return False
    try:
        if op == ">=": return actual >= expected
        if op == "<=": return actual <= expected
        if op == ">":  return actual > expected
        if op == "<":  return actual < expected
        if op == "==": return actual == expected
        if op == "!=": return actual != expected
        if op == "in": return actual in expected
        if op == "contains": return str(expected).lower() in str(actual).lower()
    except TypeError:
        return False
    return False


def _intent(lead: Lead, event: str) -> bool:
    if event == "email_opened":
        return any(l.email_opened for l in lead.email_logs)
    if event == "replied":
        return any(l.replied for l in lead.email_logs) or any(l.replied for l in lead.whatsapp_logs)
    if event == "call_connected":
        connected = {CallOutcome.INTERESTED, CallOutcome.NOT_INTERESTED, CallOutcome.CALLBACK}
        return any(c.outcome in connected for c in lead.call_logs)
    if event == "meeting_booked":
        return lead.pipeline_stage in (PipelineStage.DEMO_BOOKED, PipelineStage.ONBOARDED)
    return False  # e.g. email_clicked: not tracked yet, so never earned


def _evaluate(rule: ScoringRule, lead: Lead) -> Optional[bool]:
    """True / False, or None when the rule cannot be answered from stored data."""
    p, kind = rule.params, rule.kind

    if kind == "field":
        return _compare(_lead_field(lead, p["field"]), p["op"], p.get("value"))

    if kind == "builtin":
        return bool(getattr(lead, _BUILTIN_COLUMNS[p["name"]], False))

    if kind == "intent":
        return _intent(lead, p["event"])

    if kind == "ai":
        # Answered by the AI step and kept in lead.signals; unanswered = unknown
        return (lead.signals or {}).get(rule.key)

    text = (lead.crawled_text or "").lower()
    if not text:
        return None

    if kind == "keyword":
        if any(w.lower() in text for w in p.get("none", [])):
            return False
        return any(w.lower() in text for w in p["any"])

    if kind == "regex":
        return re.search(p["pattern"], text, re.IGNORECASE) is not None

    return None


def _unmet_requirement(lead: Lead, requires: list[str]) -> Optional[str]:
    for need in requires:
        if need == "website":
            if not lead.website:
                return "no website found"
            if not lead.website_crawled:
                return "website could not be crawled"
        elif need == "email" and not lead.email:
            return "no email address"
        elif need == "phone" and not lead.phone:
            return "no phone number"
        elif need == "site_data" and not (lead.attributes or {}).get("site_checked"):
            return "no website found" if not lead.website else "the website could not be checked"
    return None


# ─── Scoring ──────────────────────────────────────────────────────────────────

def score_lead(db: Session, lead: Lead, profile: Optional[IndustryProfile] = None) -> ScoreResult:
    profile = profile or resolve_profile(db, lead)
    if profile is None:
        raise RuntimeError("No Industry Profile exists — start the app once so the default is created")

    config = ProfileConfig.model_validate(profile.config)
    res = ScoreResult(profile_id=profile.id, profile_name=profile.name, profile_version=profile.version or 1)

    hot, warm = get_score_thresholds()
    if config.scoring.hot_threshold is not None:
        hot = config.scoring.hot_threshold
    if config.scoring.warm_threshold is not None:
        warm = config.scoring.warm_threshold

    # A lead on the DNC list scores zero and is never contacted
    domain = extract_domain(lead.website)
    if DNCCrud.is_on_dnc(db, email=lead.email, phone=lead.phone, domain=domain):
        logger.info(f"Lead {lead.name} is on DNC list — score = 0")
        res.on_dnc = True
        res.summary = "ON DNC LIST — all outreach blocked"
        return res

    unchecked_notes: list[str] = []
    data_gaps: set[str] = set()       # e.g. "no website found", whether or not a rule needed it
    signals = dict(lead.signals or {})

    for rule in config.scoring.rules:
        value = _evaluate(rule, lead)
        gap = _unmet_requirement(lead, rule.requires)
        if gap:
            data_gaps.add(gap)
        rr = RuleResult(rule.key, rule.label, rule.kind, rule.group, rule.points, NOT_MATCHED,
                        fix=str(rule.params.get("fix", "")))

        if value:
            rr.status, rr.earned = MATCHED, rule.points
        elif value is None or gap:
            note = gap or "not enough data to check"
            rr.status, rr.note = NOT_CHECKED, note
            unchecked_notes.append(f"{rule.label} ({note})")

        if rule.kind != "field":
            signals[rule.key] = None if rr.status == NOT_CHECKED else (rr.status == MATCHED)

        if rule.group == "base":
            res.base += rr.earned
            res.max_base += max(rule.points, 0)
        else:
            res.bonus += rr.earned
        res.results.append(rr)

    # Incomplete whenever required data was missing, even if the rule happened
    # to be answered some other way (same as the original auditor).
    if unchecked_notes or data_gaps:
        res.incomplete = True
        detail = "; ".join(unchecked_notes) if unchecked_notes else "; ".join(sorted(data_gaps))
        res.incomplete_reason = (
            "Some checks could not be made, so the score may be lower than the "
            f"business deserves: {detail}. Re-run the audit once the data is available."
        )

    if res.final >= hot:
        res.tier = LeadTier.HOT
    elif res.final >= warm:
        res.tier = LeadTier.WARM
    else:
        res.tier = LeadTier.COLD

    res.signals = signals
    res.summary = _render_summary(res)
    return res


def _render_summary(res: ScoreResult) -> str:
    lines = []
    gaps = res.top_gaps()
    if gaps:
        lines.append("BIGGEST OPPORTUNITIES (from what the website shows or lacks):")
        lines += [f"  • {g['label']} (+{g['points']})" + (f" → we offer: {g['fix']}" if g.get("fix") else "") for g in gaps]
        lines.append("")
    for r in res.results:
        tag = "" if r.group == "base" else " BONUS"
        if r.status == MATCHED:
            lines.append(f"✅ +{r.points}{tag}: {r.label}")
        elif r.status == NOT_CHECKED:
            lines.append(f"⚠️ +0{tag}: Not checked — {r.label} ({r.note})")
        else:
            lines.append(f"❌ +0{tag}: {r.label}")
    tier_label = {LeadTier.HOT: "🔴 HOT LEAD", LeadTier.WARM: "🟡 WARM LEAD"}.get(res.tier, "🔵 COLD LEAD")
    lines += [
        f"\n{'─' * 40}",
        f"Profile:     {res.profile_name} (v{res.profile_version})",
        f"Base Score:  {res.base}/{res.max_base}",
        f"Bonus Score: +{res.bonus}",
        f"FINAL SCORE: {res.final} → {tier_label}",
    ]
    if res.incomplete:
        lines.append(f"\n⚠️ SCORE INCOMPLETE: {res.incomplete_reason}")
    return "\n".join(lines)


def apply_result(lead: Lead, res: ScoreResult) -> None:
    """Write a score onto the lead. The caller commits."""
    lead.profile_id = lead.profile_id or res.profile_id
    lead.base_score, lead.bonus_score, lead.final_score = res.base, res.bonus, res.final
    lead.tier = res.tier
    lead.audit_summary = res.summary
    lead.score_breakdown = res.breakdown()
    lead.score_incomplete = res.incomplete
    lead.score_incomplete_reason = res.incomplete_reason
    lead.audited_at = datetime.utcnow()
    lead.attributes = {**(lead.attributes or {}), "top_gaps": res.top_gaps()}    # a new dict, so the JSON column is saved
    if not res.on_dnc:
        lead.signals = res.signals
