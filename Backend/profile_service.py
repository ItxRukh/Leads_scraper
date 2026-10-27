"""
profile_service.py — Industry Profile logic (create, edit, copy, import/export, seed)
-------------------------------------------------------------------------------------
The admin panel and the pipeline talk to profiles only through this module.
It also seeds the original dropshipping setup as the first profile, so the
system behaves exactly as before until another profile is chosen.
"""

import re
from typing import Optional

from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import IndustryProfile, Lead
from profile_schema import ProfileConfig, ProfileIn, ProfileOut

EXPORT_FORMAT = "leads-scraper-profile"
EXPORT_VERSION = 1

DEFAULT_PROFILE_SLUG = "dropship-suppliers"
DEFAULT_QUERY_TEMPLATE = "{niche} wholesale supplier {city}"


class ProfileNotFound(Exception):
    pass


class ProfileConflict(Exception):
    """Name already used by another profile."""


# ─── The original dropshipping setup, expressed as a profile ──────────────────

def _default_profile_config() -> ProfileConfig:
    """Same rules and points that scripts/auditor.py applied before profiles existed."""
    rules = [
        {"key": "dropship_mentioned", "label": "Dropshipping mentioned on website",
         "kind": "builtin", "points": 50, "requires": ["website"],
         "params": {"name": "dropship_mentioned"}},
        {"key": "processing_fast", "label": "Processing time of 1-2 days",
         "kind": "builtin", "points": 10, "requires": ["website"],
         "params": {"name": "processing_fast"}},
        {"key": "delivery_fast", "label": "Delivery time of 4-5 days",
         "kind": "builtin", "points": 10, "requires": ["website"],
         "params": {"name": "delivery_fast"}},
        {"key": "rating_4_plus", "label": "Google rating of 4.0 or more",
         "kind": "field", "points": 10,
         "params": {"field": "rating", "op": ">=", "value": 4.0}},
        {"key": "reviews_20_plus", "label": "20 or more reviews",
         "kind": "field", "points": 10,
         "params": {"field": "review_count", "op": ">=", "value": 20}},
        {"key": "has_website", "label": "Has a website",
         "kind": "field", "points": 5,
         "params": {"field": "website", "op": "exists"}},
        {"key": "has_email", "label": "Has an email address",
         "kind": "field", "points": 5,
         "params": {"field": "email", "op": "exists"}},
        {"key": "linkedin_found", "label": "LinkedIn decision maker found",
         "kind": "field", "points": 15, "group": "bonus",
         "params": {"field": "linkedin_found", "op": "==", "value": True}},
        {"key": "email_verified", "label": "Email verified as deliverable",
         "kind": "field", "points": 10, "group": "bonus",
         "params": {"field": "email_verified", "op": "==", "value": True}},
        {"key": "sells_on_marketplace", "label": "Sells on Amazon, Etsy or Shopify",
         "kind": "builtin", "points": 20, "group": "bonus", "requires": ["website"],
         "params": {"name": "sells_on_marketplace"}},
    ]
    return ProfileConfig.model_validate({
        "search": {"query_template": DEFAULT_QUERY_TEMPLATE},
        "offer": {
            "summary": ("ecomdropship.ai helps wholesale suppliers sell through a "
                        "dropship model: we run the storefront and marketing, the "
                        "supplier only ships orders."),
            "tone": "conversational, professional",
            "language": "en",
        },
        "scoring": {"rules": rules},
    })


def _legacy_signals(lead: Lead) -> dict:
    """The dropship columns on an existing lead, as profile signals."""
    return {
        "dropship_mentioned": bool(lead.dropship_mentioned),
        "processing_fast": bool(lead.processing_fast),
        "delivery_fast": bool(lead.delivery_fast),
        "sells_on_marketplace": bool(lead.sells_on_marketplace),
        "sells_on_amazon": bool(lead.sells_on_amazon),
        "sells_on_etsy": bool(lead.sells_on_etsy),
        "sells_on_shopify": bool(lead.sells_on_shopify),
        "processing_days_mentioned": lead.processing_days_mentioned,
        "delivery_days_mentioned": lead.delivery_days_mentioned,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "profile"


def _unique_slug(db: Session, name: str) -> str:
    base = _slugify(name)
    slug, n = base, 2
    while db.query(IndustryProfile.id).filter(IndustryProfile.slug == slug).first():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _unique_name(db: Session, name: str) -> str:
    candidate, n = name, 2
    while db.query(IndustryProfile.id).filter(IndustryProfile.name == candidate).first():
        candidate = f"{name} ({n})"
        n += 1
    return candidate


def _name_taken(db: Session, name: str, exclude_id: Optional[int] = None) -> bool:
    q = db.query(IndustryProfile.id).filter(func.lower(IndustryProfile.name) == name.lower())
    if exclude_id is not None:
        q = q.filter(IndustryProfile.id != exclude_id)
    return q.first() is not None


def to_out(db: Session, profile: IndustryProfile) -> ProfileOut:
    lead_count = db.query(func.count(Lead.id)).filter(Lead.profile_id == profile.id).scalar() or 0
    return ProfileOut(
        id=profile.id, name=profile.name, slug=profile.slug,
        description=profile.description, target_type=profile.target_type,
        is_active=profile.is_active, version=profile.version,
        lead_count=lead_count, config=ProfileConfig.model_validate(profile.config),
    )


def build_query(template: str, niche: str, city: str) -> str:
    """Fill a profile's search template, e.g. "{niche} contractor {city}"."""
    return " ".join(template.format(niche=niche, city=city).split())


def resolve_profile_by_id(db: Session, profile_id: Optional[int]) -> Optional[IndustryProfile]:
    """The requested active profile, or the default one when none is given."""
    if profile_id:
        profile = db.get(IndustryProfile, profile_id)
        if profile is None:
            raise ProfileNotFound(f"Profile {profile_id} not found")
        return profile
    return db.query(IndustryProfile).filter(IndustryProfile.slug == DEFAULT_PROFILE_SLUG).first()


# ─── CRUD ─────────────────────────────────────────────────────────────────────

def list_profiles(db: Session, include_archived: bool = False) -> list[IndustryProfile]:
    q = db.query(IndustryProfile)
    if not include_archived:
        q = q.filter(IndustryProfile.is_active == True)  # noqa: E712
    return q.order_by(IndustryProfile.name).all()


def get_profile(db: Session, profile_id: int) -> IndustryProfile:
    profile = db.get(IndustryProfile, profile_id)
    if profile is None:
        raise ProfileNotFound(f"Profile {profile_id} not found")
    return profile


def create_profile(db: Session, data: ProfileIn) -> IndustryProfile:
    if _name_taken(db, data.name):
        raise ProfileConflict(f"A profile named '{data.name}' already exists")
    profile = IndustryProfile(
        name=data.name, slug=_unique_slug(db, data.name),
        description=data.description, target_type=data.target_type,
        config=data.config.model_dump(),
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def update_profile(db: Session, profile_id: int, data: ProfileIn) -> IndustryProfile:
    profile = get_profile(db, profile_id)
    if _name_taken(db, data.name, exclude_id=profile_id):
        raise ProfileConflict(f"A profile named '{data.name}' already exists")
    profile.name = data.name
    profile.description = data.description
    profile.target_type = data.target_type
    profile.config = data.config.model_dump()
    profile.version = (profile.version or 1) + 1
    db.commit()
    db.refresh(profile)
    return profile


def duplicate_profile(db: Session, profile_id: int, name: Optional[str] = None) -> IndustryProfile:
    source = get_profile(db, profile_id)
    new_name = _unique_name(db, name or f"{source.name} (copy)")
    return create_profile(db, ProfileIn(
        name=new_name, description=source.description,
        target_type=source.target_type,
        config=ProfileConfig.model_validate(source.config),
    ))


def set_archived(db: Session, profile_id: int, archived: bool) -> IndustryProfile:
    """Profiles are archived, never deleted — leads keep pointing at them."""
    profile = get_profile(db, profile_id)
    profile.is_active = not archived
    db.commit()
    db.refresh(profile)
    return profile


# ─── Import / export ──────────────────────────────────────────────────────────

def export_profile(db: Session, profile_id: int) -> dict:
    p = get_profile(db, profile_id)
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "name": p.name,
        "description": p.description,
        "target_type": p.target_type,
        "config": p.config,
    }


def import_profile(db: Session, payload: dict) -> IndustryProfile:
    if payload.get("format") != EXPORT_FORMAT:
        raise ValueError("Not a Leads Scraper profile file")
    if payload.get("version") != EXPORT_VERSION:
        raise ValueError(f"Unsupported profile file version: {payload.get('version')}")
    data = ProfileIn.model_validate({
        "name": payload.get("name"),
        "description": payload.get("description"),
        "target_type": payload.get("target_type", "business"),
        "config": payload.get("config", {}),
    })
    data.name = _unique_name(db, data.name)  # never overwrite an existing profile
    return create_profile(db, data)


# ─── Startup seed ─────────────────────────────────────────────────────────────

def bootstrap_profiles(db: Session) -> None:
    """
    Safe to run on every start:
      1. If there are no profiles at all, create the original dropship profile.
      2. Attach leads that have no profile to it and copy their dropship
         columns into `signals` (the old columns are kept for now).
    """
    if db.query(func.count(IndustryProfile.id)).scalar() == 0:
        db.add(IndustryProfile(
            name="Dropship Suppliers", slug=DEFAULT_PROFILE_SLUG,
            description="Original setup: wholesale suppliers who could join ecomdropship.ai.",
            target_type="business", config=_default_profile_config().model_dump(),
        ))
        db.commit()
        logger.info("Created default Industry Profile 'Dropship Suppliers'")

    default = db.query(IndustryProfile).filter(IndustryProfile.slug == DEFAULT_PROFILE_SLUG).first()
    if default is None:
        return  # the default was renamed away or removed on purpose

    orphans = db.query(Lead).filter(Lead.profile_id.is_(None)).all()
    if not orphans:
        return
    for lead in orphans:
        lead.profile_id = default.id
        if not lead.signals:
            lead.signals = _legacy_signals(lead)
    db.commit()
    logger.info(f"Attached {len(orphans)} existing lead(s) to profile '{default.name}'")
