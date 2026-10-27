"""
industries.py — The industries a lead can be tagged with
----------------------------------------------------------
One list for the whole app: the Import page's industry picker, the keyword matching that tags
leads whose file has no industry column, and the label stored on Lead.niche. Each key is also the
slug an Industry Profile can use, so a lead is scored with its own industry's rules once that
profile exists (see profile_service).

Add an industry by adding one entry here.
"""

import re
from typing import Optional

# key (slug) -> (label, regex that spots the industry in free text such as a category or a business name)
_INDUSTRIES: dict[str, tuple[str, str]] = {
    "hvac":             ("HVAC",                r"hvac|heating|cooling|air ?conditioning|\ba/?c\b|furnace|heat pump"),
    "plumbing":         ("Plumbing",            r"plumb|drain|sewer|water heater|rooter"),
    "electrical":       ("Electrical",          r"electric(?!al engineering)|electrician"),
    "roofing":          ("Roofing",             r"\broof"),
    "solar":            ("Solar",               r"solar"),
    "garage-door":      ("Garage Doors",        r"garage ?door|overhead door"),
    "landscaping":      ("Landscaping & Lawn Care", r"landscap|lawn|tree service|irrigation|turf"),
    "pest-control":     ("Pest Control",        r"pest|exterminat|termite|rodent"),
    "construction":     ("Construction",        r"construction|general contractor|builders?\b|remodel"),
    "home-services":    ("Home Services",       r"home services?|handyman|home repair|restoration|cleaning|painting"),
    "dental":           ("Dental",              r"dental|dentist|orthodont|endodont|periodont|oral surg"),
    "med-spa":          ("Med Spa",             r"med ?spa|medical spa|aesthetic|botox|laser"),
    "chiropractic":     ("Chiropractic",        r"chiropract"),
    "physical-therapy": ("Physical Therapy",    r"physical therap|physiotherap|\bpt clinic"),
    "dermatology":      ("Dermatology",         r"dermatolog|skin (care|clinic|center)"),
    "orthopedic":       ("Orthopedic",          r"orthop"),
    "veterinary":       ("Veterinary",          r"veterin|vet clinic|animal (hospital|clinic)|pet hospital|\bvet\b"),
    "radiology":        ("Radiology",           r"radiolog"),
    "imaging":          ("Imaging Centers",     r"imaging|\bmri\b|x-?ray|ultrasound"),
    "law-firms":        ("Law Firms",           r"\b(law|attorneys?|lawyers?|legal|esq)\b"),
    "other":            ("Other",               r"$^"),     # never matched by text; chosen by hand
}

_COMPILED = [(key, re.compile(pattern, re.I)) for key, (_, pattern) in _INDUSTRIES.items()]
_BY_LABEL = {label.lower(): key for key, (label, _) in _INDUSTRIES.items()}


def industry_options() -> list[dict]:
    return [{"key": key, "label": label} for key, (label, _) in _INDUSTRIES.items()]


def industry_label(key: Optional[str]) -> Optional[str]:
    return _INDUSTRIES[key][0] if key in _INDUSTRIES else None


def normalize_industry(text: Optional[str]) -> Optional[str]:
    """
    Turn whatever a file says ("Heating & Air", "dentist", "hvac-companies") into one of our keys.
    Returns None when nothing matches; the caller decides what to do then.
    """
    value = (text or "").strip().lower()
    if not value:
        return None
    if value in _INDUSTRIES:
        return value
    if value in _BY_LABEL:
        return _BY_LABEL[value]
    slug = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    for key in _INDUSTRIES:
        if slug == key or slug.startswith(key + "-"):          # "hvac-companies" -> hvac
            return key
    for key, pattern in _COMPILED:
        if pattern.search(value):
            return key
    return None
