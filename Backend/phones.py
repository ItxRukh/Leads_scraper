"""
phones.py — Phone numbers for WhatsApp and for matching replies to leads
------------------------------------------------------------------------
WhatsApp needs the full international number (country code first, digits only).
Leads often store numbers the way a website printed them, so this module
turns them into that form, and finds the lead behind a number that wrote to us.
"""

import re
from typing import Optional

from sqlalchemy.orm import Session

from database import Lead

# Country code used when a number has no "+" and the lead's country is known
COUNTRY_CODES = {
    "united states": "1", "canada": "1",
    "united kingdom": "44", "uk": "44",
    "pakistan": "92", "india": "91",
    "united arab emirates": "971", "uae": "971",
}


def whatsapp_number(raw: Optional[str], country: Optional[str] = None) -> Optional[str]:
    """
    Digits only, with country code, e.g. "(305) 555-0100" -> "13055550100".
    Returns None when the country cannot be told (a number such as "0300 1234567"
    could belong to any country) or the result is not a plausible number.
    """
    if not raw:
        return None
    text = raw.strip()
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None

    if text.startswith("+"):
        pass                                    # already international
    elif digits.startswith("00"):
        digits = digits[2:]                     # 00 = international call prefix
    else:
        code = COUNTRY_CODES.get((country or "").lower())
        if code is None:                        # no country known: only a US-style number is certain
            if len(digits) == 10 or (len(digits) == 11 and digits.startswith("1")):
                code = "1"
            else:
                return None
        if digits.startswith("0"):
            return None                         # a trunk prefix: the country cannot be guessed
        if not (digits.startswith(code) and len(digits) >= len(code) + 9):
            digits = code + digits              # a national number without its country code

    return digits if 8 <= len(digits) <= 15 else None


def phone_key(raw: Optional[str]) -> str:
    """The last 10 digits: enough to match the same number written in different ways."""
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:]


def find_lead_by_phone(db: Session, raw: Optional[str]) -> Optional[Lead]:
    key = phone_key(raw)
    if len(key) < 7:
        return None
    last4 = key[-4:]
    candidates = db.query(Lead).filter(Lead.phone.isnot(None), Lead.phone.like(f"%{last4}%")).all()
    for lead in candidates:
        if phone_key(lead.phone) == key:
            return lead
    return None
