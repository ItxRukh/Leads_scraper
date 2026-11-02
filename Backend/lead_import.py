"""
lead_import.py — Turn a pasted list or an uploaded file into clean leads
-------------------------------------------------------------------------
  read_table()      CSV / Excel / pasted text  ->  headers + rows
  map_headers()     which column is the email, the website, ...
  clean_rows()      tidy every field, flag problems, find duplicates and Do-Not-Contact matches
  ai_clean()        Groq help for what rules cannot settle (missing industry, "Austin, TX" in one cell)
  save_leads()      write the good rows as Lead records under one ImportBatch

Groq is only a helper and never a source of contact details: it may fill industry, city, state and
a contact name, and anything it returns is checked before use. Emails, phones and websites come
from the file and nowhere else.
"""

import csv
import io
import json
import re
from typing import Iterable, Optional
from urllib.parse import urlparse

from loguru import logger
from sqlalchemy.orm import Session

from database import DNCList, ImportBatch, IndustryProfile, Lead, LeadSource, PipelineStage
from geo import US_STATE_TZ
from industries import industry_label, industry_options, normalize_industry
from phones import phone_key

MAX_ROWS = 5000
MAX_BYTES = 5 * 1024 * 1024

# fields a row can have once cleaned
FIELDS = ["business_name", "contact_name", "contact_title", "email", "phone", "website",
          "industry", "city", "state", "address", "weakness"]

# header text (lower case, letters and digits only, words joined by "_") -> field
_ALIASES: dict[str, list[str]] = {
    "business_name": ["business_name", "business", "company", "company_name", "name", "organization", "organisation",
                      "org", "account", "account_name", "firm", "practice", "practice_name", "clinic", "clinic_name"],
    "contact_name":  ["contact_name", "contact", "contact_person", "owner", "owner_name", "decision_maker",
                      "full_name", "person", "contact_full_name"],
    "contact_title": ["contact_title", "title", "job_title", "position", "role"],
    "email":         ["email", "e_mail", "email_address", "mail", "contact_email", "work_email", "business_email"],
    "phone":         ["phone", "phone_number", "telephone", "tel", "mobile", "cell", "contact_phone", "business_phone", "direct"],
    "website":       ["website", "site", "url", "web", "domain", "web_address", "homepage", "website_url"],
    "industry":      ["industry", "niche", "category", "type", "trade", "vertical", "sector", "business_type", "tag", "industry_tag"],
    "city":          ["city", "town", "location"],
    "state":         ["state", "st", "province", "region", "state_province"],
    "address":       ["address", "street", "street_address"],
    "weakness":      ["weakness", "weaknesses", "weak_points", "issues", "issue", "problem", "problems", "gaps",
                      "pain_points", "pain_point", "whats_missing", "missing", "opportunity", "opportunities",
                      "website_issues", "site_issues", "findings"],
}
_FIRST = {"first_name", "firstname", "first", "given_name"}
_LAST = {"last_name", "lastname", "last", "surname", "family_name"}
_NOTES = {"notes", "note", "comments", "comment"}
_ALIAS_TO_FIELD = {alias: field for field, aliases in _ALIASES.items() for alias in aliases}

_STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

_EMAIL_RE = re.compile(r"^[a-z0-9][a-z0-9._%+'\-]*@[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$")
_ROLE_LOCALS = {"info", "contact", "sales", "support", "admin", "office", "hello", "service", "services", "enquiries",
                "inquiries", "mail", "team", "help", "billing", "accounts", "reception", "appointments", "booking",
                "bookings", "customerservice", "frontdesk", "scheduling"}
_JUNK_LOCALS = {"test", "example", "email", "name", "yourname", "your", "user", "noreply", "no-reply", "donotreply", "none", "na"}
_SOCIAL_HOSTS = ("facebook.com", "instagram.com", "yelp.com", "linkedin.com", "twitter.com", "x.com", "tiktok.com",
                 "youtube.com", "google.com", "goo.gl", "maps.app.goo.gl", "yellowpages.com", "bbb.org", "nextdoor.com")
_GENERIC_NAMES = {"owner", "manager", "info", "n/a", "na", "none", "admin", "office", "team", "unknown", "-"}


# ─── Reading the input ────────────────────────────────────────────────────────

class ImportProblem(ValueError):
    """The input cannot be read; the message is meant for the person using the page."""


def _header_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)                      # Excel stores 5125550142 as 5125550142.0
    return str(value).strip()


def _read_xlsx(content: bytes) -> list[list[str]]:
    try:
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:
        raise ImportProblem("That Excel file could not be opened. Save it as .xlsx or .csv and try again.") from e
    for sheet in wb.worksheets:
        rows = []
        for raw in sheet.iter_rows(values_only=True):
            row = [_cell(v) for v in raw]
            if any(row):
                rows.append(row)
            if len(rows) > MAX_ROWS + 1:
                break
        if rows:
            return rows
    return []


def _read_text(text: str) -> list[list[str]]:
    sample = text[:5000]
    counts = {d: sample.count(d) for d in (",", "\t", ";", "|")}
    delimiter = max(counts, key=counts.get) if any(counts.values()) else ","
    rows = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        cells = [c.strip() for c in row]
        if any(cells):
            rows.append(cells)
        if len(rows) > MAX_ROWS + 1:
            break
    return rows


def read_table(filename: Optional[str], content: Optional[bytes], text: Optional[str]) -> list[list[str]]:
    """Every non-empty row of the file or text, as lists of strings. The first row is usually the headings."""
    if content is not None:
        if len(content) > MAX_BYTES:
            raise ImportProblem("That file is larger than 5 MB. Split it into smaller files.")
        if (filename or "").lower().endswith((".xlsx", ".xlsm")):
            return _read_xlsx(content)
        if (filename or "").lower().endswith(".xls"):
            raise ImportProblem("Old .xls files are not supported. Save it as .xlsx or .csv and try again.")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = content.decode("latin-1")
    if not (text or "").strip():
        raise ImportProblem("There is nothing to import: the file or text is empty.")
    return _read_text(text)


# ─── Column mapping ───────────────────────────────────────────────────────────

def map_headers(headers: list[str]) -> dict[int, str]:
    """
    column index -> field name ("email", "website", ...). Columns that mean nothing to us are left out.
    First/last name columns are returned as "first_name" / "last_name" and joined later.
    """
    mapping: dict[int, str] = {}
    taken: set[str] = set()
    for i, header in enumerate(headers):
        key = _header_key(header)
        field = "first_name" if key in _FIRST else "last_name" if key in _LAST else \
                "notes" if key in _NOTES else _ALIAS_TO_FIELD.get(key)
        if field and (field not in taken or field in ("notes",)):
            mapping[i] = field
            taken.add(field)
    return mapping


def looks_like_headers(headers: list[str]) -> bool:
    return len(set(map_headers(headers).values()) & {"business_name", "email", "website", "phone"}) >= 1 \
        and not any("@" in h for h in headers)


# ─── Cleaning one field at a time ─────────────────────────────────────────────

def _text(value: str, limit: int = 255) -> str:
    value = re.sub(r"\s+", " ", (value or "")).strip().lstrip("=@+-").strip()    # a leading =,+,-,@ would run as a formula in Excel
    return value[:limit]


def clean_email(raw: str) -> tuple[Optional[str], Optional[str]]:
    """(email, problem). Problem is a short sentence or None."""
    value = (raw or "").strip().lower().replace("mailto:", "")
    value = re.split(r"[\s,;]", value)[0] if value else ""          # "a@x.com, b@x.com" -> first one
    if not value:
        return None, None
    if not _EMAIL_RE.match(value) or ".." in value:
        return None, f"Email '{raw.strip()[:40]}' is not valid"
    if value.split("@")[0] in _JUNK_LOCALS:
        return None, "Email looks like a placeholder"
    return value, None


def clean_phone(raw: str) -> tuple[Optional[str], Optional[str]]:
    """US numbers become +1XXXXXXXXXX. Other international numbers are kept as +digits."""
    text = (raw or "").strip()
    if not text:
        return None, None
    text = re.split(r"\s*(?:ext\.?|x|#)\s*\d+\s*$", text, flags=re.I)[0]
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") and not digits.startswith("1"):
        return (f"+{digits}", None) if 8 <= len(digits) <= 15 else (None, "Phone is not valid")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "23456789" and digits[3] in "23456789":
        return f"+1{digits}", None
    return None, f"Phone '{raw.strip()[:25]}' is not a valid US number"


def website_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    host = urlparse(url if "//" in url else f"https://{url}").netloc.lower().split("@")[-1].split(":")[0]
    return host[4:] if host.startswith("www.") else host or None


def clean_website(raw: str) -> tuple[Optional[str], Optional[str]]:
    text = (raw or "").strip()
    if not text or text.lower() in ("n/a", "na", "none", "-"):
        return None, None
    if " " in text:
        return None, f"Website '{text[:40]}' is not valid"
    parsed = urlparse(text if "//" in text else f"https://{text}")
    host = parsed.netloc.lower().split("@")[-1].split(":")[0]
    if "." not in host or not re.match(r"^[a-z0-9.\-]+$", host):
        return None, f"Website '{text[:40]}' is not valid"
    bare = host[4:] if host.startswith("www.") else host
    if any(bare == h or bare.endswith("." + h) for h in _SOCIAL_HOSTS):
        return None, "Website is a social or directory page, not the company's own site"
    path = parsed.path.rstrip("/")
    return f"https://{host}{path}", None


def clean_state(raw: str) -> Optional[str]:
    value = (raw or "").strip().rstrip(".")
    if len(value) == 2 and value.upper() in US_STATE_TZ:
        return value.upper()
    return _STATE_NAMES.get(value.lower())


_CITY_STATE_RE = re.compile(r"^(?P<city>.+?)[,\s]+(?P<state>[A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?$")


def split_city_state(city: str) -> tuple[str, Optional[str]]:
    """'Austin, TX 78701' -> ('Austin', 'TX'). Anything else is returned unchanged."""
    m = _CITY_STATE_RE.match((city or "").strip())
    if m and m.group("state").upper() in US_STATE_TZ:
        return m.group("city").strip(" ,"), m.group("state").upper()
    return (city or "").strip(), None


def clean_person(raw: str) -> Optional[str]:
    value = _text(raw, 80)
    if not value or value.lower() in _GENERIC_NAMES or re.search(r"[@\d]", value):
        return None
    return value


# ─── Cleaning rows ────────────────────────────────────────────────────────────

def rows_to_dicts(table: list[list[str]], mapping: dict[int, str]) -> list[dict]:
    """Rows below the headings as {field: value}; first/last names are joined, unmapped columns kept under 'extra'."""
    headers = table[0]
    out = []
    for n, cells in enumerate(table[1:], start=2):                 # n = the row number a person sees in Excel
        row: dict = {"_row": n, "extra": {}}
        for i, value in enumerate(cells):
            field = mapping.get(i)
            if field is not None:
                row[field] = value
            elif value and i < len(headers) and headers[i]:
                row["extra"][headers[i]] = value
        if row.get("first_name") or row.get("last_name"):
            row.setdefault("contact_name", f"{row.get('first_name', '')} {row.get('last_name', '')}".strip())
        out.append(row)
    return out


def clean_row(row: dict, default_industry: Optional[str] = None) -> dict:
    """One row -> {row, data, warnings, errors, ...}. `errors` make the row unusable; `warnings` do not."""
    warnings: list[str] = []
    errors: list[str] = []

    name = _text(row.get("business_name", ""))
    city = _text(row.get("city", ""), 100)
    state = clean_state(row.get("state", ""))
    if city:
        city, state_from_city = split_city_state(city)
        state = state or state_from_city

    email, problem = clean_email(row.get("email", ""))
    if problem:
        warnings.append(problem)
    phone, problem = clean_phone(row.get("phone", ""))
    if problem:
        warnings.append(problem)
    website, problem = clean_website(row.get("website", ""))
    if problem:
        warnings.append(problem)

    industry = normalize_industry(row.get("industry", "")) or normalize_industry(name)
    if not industry and default_industry:
        industry = normalize_industry(default_industry)
    if not industry:
        warnings.append("No industry: choose a default industry or add an industry column")

    if not name:
        errors.append("Missing business name")
    if not (email or website or phone):
        errors.append("No email, website or phone to reach them")
    elif not email:
        warnings.append("No email: it will be looked for on the website" if website else "No email")

    local = email.split("@")[0] if email else ""
    data = {
        "business_name": name,
        "contact_name": clean_person(row.get("contact_name", "")),
        "contact_title": _text(row.get("contact_title", ""), 100) or None,
        "email": email,
        "email_is_role": bool(local) and local in _ROLE_LOCALS,
        "phone": phone,
        "website": website,
        "industry": industry,
        "city": city or None,
        "state": state,
        "address": _text(row.get("address", ""), 300) or None,
        "weakness": _text(row.get("weakness", ""), 500) or None,
    }
    return {"row": row.get("_row"), "data": data, "warnings": warnings, "errors": errors,
            "extra": row.get("extra") or {}, "notes": _text(row.get("notes", ""), 500) or None, "ai_fixed": []}


# ─── Duplicates and Do-Not-Contact ────────────────────────────────────────────

class _Known:
    """What the database already holds, loaded once per import."""

    def __init__(self, db: Session):
        self.emails: set[str] = set()
        self.domains: set[str] = set()
        self.phones: set[str] = set()
        self.names: set[tuple[str, str]] = set()
        for email, website, phone, name, city in db.query(Lead.email, Lead.website, Lead.phone, Lead.name, Lead.city):
            if email:
                self.emails.add(email.strip().lower())
            if (d := website_domain(website)):
                self.domains.add(d)
            if len(k := phone_key(phone)) >= 10:
                self.phones.add(k)
            self.names.add(((name or "").strip().lower(), (city or "").strip().lower()))

        self.dnc_emails: set[str] = set()
        self.dnc_domains: set[str] = set()
        self.dnc_phones: set[str] = set()
        for email, domain, phone in db.query(DNCList.email, DNCList.domain, DNCList.phone):
            if email:
                self.dnc_emails.add(email.strip().lower())
            if domain:
                self.dnc_domains.add(domain.strip().lower().removeprefix("www."))
            if len(k := phone_key(phone)) >= 10:
                self.dnc_phones.add(k)


def _keys(data: dict) -> dict:
    return {
        "email": data["email"],
        "domain": website_domain(data["website"]),
        "phone": phone_key(data["phone"]) if data["phone"] else None,
        "name": (data["business_name"].lower(), (data["city"] or "").lower()),
    }


def classify(results: list[dict], db: Session) -> list[dict]:
    """Sets result['status'] to ready | duplicate | dnc | invalid and result['reasons']."""
    known = _Known(db)
    seen: dict[str, dict[object, int]] = {"email": {}, "domain": {}, "phone": {}, "name": {}}

    for r in results:
        data, keys = r["data"], _keys(r["data"])
        r["reasons"] = []

        if r["errors"]:
            r["status"], r["reasons"] = "invalid", list(r["errors"])
            continue

        dnc = (keys["email"] in known.dnc_emails) or (keys["domain"] in known.dnc_domains) or \
              (keys["phone"] in known.dnc_phones if keys["phone"] else False)
        if dnc:
            r["status"], r["reasons"] = "dnc", ["On your Do-Not-Contact list"]
            continue

        reason = None
        if keys["email"] and keys["email"] in known.emails:
            reason = "Already in your leads (same email)"
        elif keys["domain"] and keys["domain"] in known.domains:
            reason = "Already in your leads (same website)"
        elif keys["phone"] and keys["phone"] in known.phones:
            reason = "Already in your leads (same phone)"
        elif keys["name"][1] and keys["name"] in known.names:
            reason = "Already in your leads (same name and city)"
        if not reason:
            for kind in ("email", "domain", "phone", "name"):
                value = keys[kind]
                if value and (kind != "name" or value[1]) and value in seen[kind]:
                    reason = f"Same {kind if kind != 'domain' else 'website'} as row {seen[kind][value]}"
                    break
        if reason:
            r["status"], r["reasons"] = "duplicate", [reason]
            continue

        r["status"] = "ready"
        for kind, value in keys.items():
            if value and (kind != "name" or value[1]):
                seen[kind][value] = r["row"]
    return results


def summarize(results: list[dict]) -> dict:
    counts = {"total": len(results), "ready": 0, "duplicate": 0, "dnc": 0, "invalid": 0, "no_email": 0}
    for r in results:
        counts[r["status"]] += 1
        if r["status"] == "ready" and not r["data"]["email"]:
            counts["no_email"] += 1
    return counts


# ─── Groq help ────────────────────────────────────────────────────────────────

_AI_BATCH = 25


def _json_from(text: Optional[str]):
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start = min((i for i in (cleaned.find("["), cleaned.find("{")) if i >= 0), default=-1)
    if start < 0:
        return None
    try:
        return json.loads(cleaned[start:])
    except ValueError:
        return None


def ai_available() -> bool:
    from config import settings
    return bool(settings.GROQ_API_KEY)


def ai_clean(results: list[dict]) -> str:
    """
    Tag the industry of rows that have none, from the business name and website. Only those rows are sent.
    State is never guessed: a wrong guess would put the lead in the wrong time zone. Returns a one-line note.
    """
    from utils.groq_helper import _call_groq

    if not ai_available():
        return "AI cleaning skipped: GROQ_API_KEY is not set"

    allowed = [o["key"] for o in industry_options() if o["key"] != "other"]
    todo = [r for r in results if not r["errors"] and not r["data"]["industry"]]
    if not todo:
        return "AI cleaning: every row already has an industry"

    system = (
        "You tag business leads with an industry. Reply with JSON only: a list with one object per input, "
        'each shaped {"i": <id>, "industry": <one of the allowed keys, or null>}. '
        "Use only what the record shows (business name, website domain). If unsure, use null. Never invent anything."
    )
    fixed = 0
    for start in range(0, len(todo), _AI_BATCH):
        chunk = todo[start:start + _AI_BATCH]
        payload = [{"i": r["row"], "business_name": r["data"]["business_name"],
                    "website_domain": website_domain(r["data"]["website"])} for r in chunk]
        reply = _json_from(_call_groq(system, f"Allowed industries: {allowed}\nRecords: {json.dumps(payload)}",
                                      max_tokens=1500, temperature=0))
        if not isinstance(reply, list):
            continue
        by_row = {r["row"]: r for r in chunk}
        for item in reply:
            r = by_row.get(item.get("i")) if isinstance(item, dict) else None
            if r and item.get("industry") in allowed:
                r["data"]["industry"] = item["industry"]
                r["ai_fixed"].append("industry")
                r["warnings"] = [w for w in r["warnings"] if not w.startswith("No industry")]
                fixed += 1
    return f"AI cleaning: tagged the industry of {fixed} of {len(todo)} rows that had none"


def ai_map_headers(headers: list[str], table: list[list[str]], mapping: dict[int, str]) -> tuple[dict[int, str], int]:
    """Ask Groq what the columns we could not recognise are. Returns the extended mapping and how many it added."""
    from utils.groq_helper import _call_groq

    unknown = [i for i, h in enumerate(headers) if i not in mapping and h]
    if not unknown or not ai_available():
        return mapping, 0
    allowed = set(FIELDS) | {"first_name", "last_name", "notes"}
    samples = [{"i": i, "heading": headers[i], "examples": [row[i] for row in table[1:6] if i < len(row) and row[i]][:3]}
               for i in unknown]
    system = (
        "Match spreadsheet columns to lead fields. Reply with JSON only: a list of "
        '{"i": <column id>, "field": <one of the allowed fields, or "ignore">}. Use "ignore" when unsure.'
    )
    reply = _json_from(_call_groq(system, f"Allowed fields: {sorted(allowed)}\nColumns: {json.dumps(samples)}",
                                  max_tokens=800, temperature=0))
    if not isinstance(reply, list):
        return mapping, 0
    taken, added, out = set(mapping.values()), 0, dict(mapping)
    for item in reply:
        if isinstance(item, dict) and item.get("i") in unknown and item.get("field") in allowed                 and item["field"] not in taken:
            out[item["i"]] = item["field"]
            taken.add(item["field"])
            added += 1
    return out, added


def ai_extract_rows(text: str) -> list[dict]:
    """
    For pasted text that has no column headings (names and numbers in free form): ask Groq to pull the leads
    out. Every email, phone and website it returns must literally appear in the pasted text, or it is dropped.
    """
    from utils.groq_helper import _call_groq

    if not ai_available():
        raise ImportProblem("The first row should name the columns (business_name, email, website, ...). "
                           "Without headings the AI reader is needed, but GROQ_API_KEY is not set.")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    system = (
        "Extract business leads from the text. Reply with JSON only: a list of objects with these keys, using null when "
        "the text does not say: business_name, contact_name, email, phone, website, city, state. "
        "Copy emails, phone numbers and websites exactly as written. Never invent or guess any value."
    )
    rows: list[dict] = []
    for start in range(0, len(lines), 40):
        chunk = "\n".join(lines[start:start + 40])
        reply = _json_from(_call_groq(system, chunk, max_tokens=3000, temperature=0))
        if not isinstance(reply, list):
            continue
        flat = re.sub(r"\D", "", chunk)
        lowered = chunk.lower()
        for item in reply:
            if not isinstance(item, dict):
                continue
            row = {k: (str(item.get(k)) if item.get(k) else "") for k in
                   ("business_name", "contact_name", "email", "phone", "website", "city", "state")}
            if row["email"] and row["email"].lower() not in lowered:
                row["email"] = ""
            if row["website"] and row["website"].lower().replace("https://", "").replace("http://", "").rstrip("/") not in lowered:
                row["website"] = ""
            if row["phone"] and re.sub(r"\D", "", row["phone"]) not in flat:
                row["phone"] = ""
            if row["business_name"] and row["business_name"].lower() not in lowered:
                row["business_name"] = ""
            rows.append(row)
    for n, row in enumerate(rows, start=1):
        row["_row"] = n
        row["extra"] = {}
    return rows


# ─── Preview ──────────────────────────────────────────────────────────────────

def build_preview(db: Session, table: list[list[str]], *, text: Optional[str] = None,
                  mapping_override: Optional[dict[int, str]] = None,
                  default_industry: Optional[str] = None, use_ai: bool = True) -> dict:
    """The whole pipeline short of saving. Returns what the Import page shows."""
    notes: list[str] = []
    headers = table[0] if table else []

    if table and looks_like_headers(headers):
        mapping = mapping_override if mapping_override is not None else map_headers(headers)
        if mapping_override is None and use_ai:
            mapping, added = ai_map_headers(headers, table, mapping)
            if added:
                notes.append(f"AI matched {added} column{'s' if added != 1 else ''} we did not recognise: check the column mapping")
        rows = rows_to_dicts(table, mapping)
        mode = "table"
    else:
        if text is None:
            raise ImportProblem("The first row should name the columns, for example: business_name, email, website, industry.")
        rows = ai_extract_rows(text)
        headers, mapping, mode = [], {}, "text"
        notes.append("No column headings found: the AI read the text. Check every row before importing.")

    if not rows:
        raise ImportProblem("No lead rows were found under the column headings.")
    if len(rows) > MAX_ROWS:
        raise ImportProblem(f"That is {len(rows):,} rows; the limit is {MAX_ROWS:,} per import. Split the file.")

    results = [clean_row(r, default_industry) for r in rows]
    if use_ai:
        notes.append(ai_clean(results))
    classify(results, db)

    return {
        "mode": mode,
        "headers": headers,
        "mapping": {str(i): f for i, f in mapping.items()},
        "fields": FIELDS,
        "summary": summarize(results),
        "notes": notes,
        "ai_used": use_ai and ai_available(),
        "rows": results,
    }


# ─── Saving ───────────────────────────────────────────────────────────────────

def save_leads(db: Session, results: Iterable[dict], *, batch_name: str, source: str,
               default_industry: Optional[str], total_rows: int, ai_used: bool) -> ImportBatch:
    """Writes every result whose status is 'ready'. Counts the rest on the batch."""
    results = list(results)
    profiles = {p.slug: p.id for p in db.query(IndustryProfile).filter(IndustryProfile.is_active == True)}  # noqa: E712

    batch = ImportBatch(name=batch_name[:200] or "Import", source=source[:300], default_industry=default_industry,
                        total_rows=total_rows, ai_used=ai_used,
                        duplicates=sum(r["status"] == "duplicate" for r in results),
                        on_dnc=sum(r["status"] == "dnc" for r in results),
                        invalid=sum(r["status"] == "invalid" for r in results))
    db.add(batch)
    db.flush()

    imported = 0
    for r in results:
        if r["status"] != "ready":
            continue
        d = r["data"]
        state = d["state"]
        db.add(Lead(
            name=d["business_name"], email=d["email"], phone=d["phone"], website=d["website"],
            city=d["city"], address=d["address"], niche=industry_label(d["industry"]),
            source=LeadSource.MANUAL, pipeline_stage=PipelineStage.SCRAPED,
            profile_id=profiles.get(d["industry"]),
            country="United States" if state else None, timezone=US_STATE_TZ.get(state) if state else None,
            linkedin_name=d["contact_name"], linkedin_title=d["contact_title"],     # used as the greeting name; linkedin_found stays False
            import_batch_id=batch.id,
            attributes={"state": state, "industry_key": d["industry"], "email_is_role": d["email_is_role"],
                        "weakness": d["weakness"], "weakness_original": d["weakness"],
                        "import_row": r["row"], "ai_fixed": r["ai_fixed"], "notes": r["notes"], "extra": r["extra"]},
        ))
        imported += 1
    batch.imported = imported
    db.commit()
    logger.info(f"[import] batch {batch.id} '{batch.name}': {imported} saved of {total_rows}")
    return batch


def sample_csv() -> str:
    """The template people download: the columns we use, and a few made-up rows to show the format."""
    lines = [
        "business_name,contact_name,contact_title,email,phone,website,industry,city,state,weakness",
        'Sample Comfort Heating & Air,Dana Rivera,Owner,dana@samplecomfort.example,(512) 555-0142,samplecomfort.example,HVAC,Austin,TX,"no online booking, no after-hours info"',
        'Example Family Dental,Dr. Chris Lee,Practice Manager,office@examplefamilydental.example,512-555-0177,www.examplefamilydental.example,Dental,Round Rock,Texas,no patient forms online',
        "Demo Plumbing Co,,,,(303) 555-0101,demoplumbing.example,plumbing,Denver,CO,",
        "Placeholder Law Group,Sam Patel,Partner,sam@placeholderlaw.example,,placeholderlaw.example,Law Firms,Miami,FL,no contact form",
    ]
    return "\n".join(lines) + "\n"
