"""
geo.py — Work out a lead's country and time zone
------------------------------------------------
Used so outreach can follow the lead's own working hours. The time zone is
an IANA name such as "America/New_York". US states that span two zones get
the zone that covers most of the state, so this is a sensible default, not
an exact answer; a value already stored on the lead is never overwritten.
"""

import re
from typing import Optional

_ET, _CT, _MT, _PT = "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles"

US_STATE_TZ = {
    "AL": _CT, "AK": "America/Anchorage", "AZ": "America/Phoenix", "AR": _CT, "CA": _PT,
    "CO": _MT, "CT": _ET, "DE": _ET, "DC": _ET, "FL": _ET, "GA": _ET, "HI": "Pacific/Honolulu",
    "ID": _MT, "IL": _CT, "IN": _ET, "IA": _CT, "KS": _CT, "KY": _ET, "LA": _CT, "ME": _ET,
    "MD": _ET, "MA": _ET, "MI": _ET, "MN": _CT, "MS": _CT, "MO": _CT, "MT": _MT, "NE": _CT,
    "NV": _PT, "NH": _ET, "NJ": _ET, "NM": _MT, "NY": _ET, "NC": _ET, "ND": _CT, "OH": _ET,
    "OK": _CT, "OR": _PT, "PA": _ET, "RI": _ET, "SC": _ET, "SD": _CT, "TN": _CT, "TX": _CT,
    "UT": _MT, "VT": _ET, "VA": _ET, "WA": _PT, "WV": _ET, "WI": _CT, "WY": _MT,
}

# Countries with a single main time zone
COUNTRY_TZ = {
    "united kingdom": "Europe/London", "uk": "Europe/London",
    "pakistan": "Asia/Karachi", "india": "Asia/Kolkata",
    "united arab emirates": "Asia/Dubai", "uae": "Asia/Dubai",
}

_US_NAMES = ("usa", "united states", "u.s.a", "u.s.")

# "..., Miami, FL 33101, USA"  or  "Miami FL"  or  "Miami, FL"
_STATE_RE = re.compile(r"(?:,|\s)\s*([A-Z]{2})(?=\s+\d{5}|\s*,|\s*$)")


def _us_state(*texts: Optional[str]) -> Optional[str]:
    for text in texts:
        for m in _STATE_RE.finditer(text or ""):
            if m.group(1) in US_STATE_TZ:
                return m.group(1)
    return None


def us_state(city: Optional[str], address: Optional[str]) -> Optional[str]:
    """The two-letter US state found in an address or city text, or None."""
    return _us_state(address, city)


def infer_geo(city: Optional[str], address: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Returns (country, timezone); either may be None when it cannot be told."""
    haystack = f"{address or ''} {city or ''}".lower()

    for name, tz in COUNTRY_TZ.items():
        if re.search(rf"\b{re.escape(name)}\b", haystack):
            return name.title() if len(name) > 3 else name.upper(), tz

    state = _us_state(address, city)
    if state or any(n in haystack for n in _US_NAMES):
        return "United States", US_STATE_TZ.get(state) if state else None

    return None, None


def ensure_geo(lead) -> bool:
    """Fill lead.country / lead.timezone when empty. Returns True if anything changed."""
    if lead.country and lead.timezone:
        return False
    country, tz = infer_geo(lead.city, lead.address)
    changed = False
    if not lead.country and country:
        lead.country, changed = country, True
    if not lead.timezone and tz:
        lead.timezone, changed = tz, True
    return changed
