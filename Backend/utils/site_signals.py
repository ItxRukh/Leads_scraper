"""
site_signals.py — What a local service business's website is missing
---------------------------------------------------------------------
Looks at the homepage and up to two more pages (contact / booking / services) and answers the
questions a sales call would start with: can a visitor tap to call, book online, chat, send a
message; does the site mention after-hours or emergency help; does it show reviews; is it built for
phones and kept up to date.

    facts = collect_site_signals("example.com")   # None when the site cannot be reached

The `site_*` keys in the result are stored on the lead (Lead.attributes) and read by the scoring rules
of the Industry Profiles. They are facts about what was *found*; a page that hides its booking button
behind a script can look like "no booking" here, so messages must say "I couldn't find", never "you don't have".

Pages are fetched with a guard against internal addresses (a lead's website comes from an uploaded file,
so it must not be able to point this server at localhost or a private network).
"""

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config import settings
from utils.crawler import _extract_emails, _extract_text

MAX_BYTES = 1_500_000
MAX_REDIRECTS = 4
EXTRA_PAGES = 2
_REDIRECT_CODES = {301, 302, 303, 307, 308}


@dataclass
class Page:
    url: str
    html: str


# ─── Fetching ─────────────────────────────────────────────────────────────────

def _is_public_host(host: str) -> bool:
    """False for localhost, private networks, link-local addresses and anything that does not resolve."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def fetch_page(url: str) -> Optional[Page]:
    """One page as HTML, or None. Follows up to four redirects, re-checking every address."""
    headers = {
        "User-Agent": settings.CRAWLER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "en-US,en;q=0.5",
    }
    for _ in range(MAX_REDIRECTS + 1):
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname or not _is_public_host(parts.hostname):
            logger.debug(f"Skipping non-public or invalid address: {url}")
            return None
        try:
            response = requests.get(url, headers=headers, timeout=settings.CRAWLER_TIMEOUT,
                                    allow_redirects=False, stream=True)
        except requests.RequestException as e:
            logger.debug(f"Fetch failed for {url}: {e}")
            return None
        try:
            if response.status_code in _REDIRECT_CODES and response.headers.get("Location"):
                url = urljoin(url, response.headers["Location"])
                continue
            kind = response.headers.get("Content-Type", "")
            if response.status_code != 200 or (kind and "html" not in kind.lower()):
                return None
            raw = b""
            for chunk in response.iter_content(65536):
                raw += chunk
                if len(raw) > MAX_BYTES:
                    break
            return Page(url, raw[:MAX_BYTES].decode(response.encoding or "utf-8", errors="replace"))
        finally:
            response.close()
    return None


# ─── What to look for ─────────────────────────────────────────────────────────

BOOKING_TOOLS = {
    "Calendly": r"calendly\.com", "Acuity": r"acuityscheduling\.com|//as\.me/", "Square Appointments": r"squareup\.com/appointments|square\.site",
    "Housecall Pro": r"housecallpro\.com", "ServiceTitan": r"servicetitan\.com|st\.chat", "Jobber": r"getjobber\.com|clienthub",
    "Zocdoc": r"zocdoc\.com", "NexHealth": r"nexhealth\.com", "Setmore": r"setmore\.com", "SimplyBook.me": r"simplybook\.",
    "Vagaro": r"vagaro\.com", "Mindbody": r"mindbodyonline\.com", "Jane App": r"janeapp\.com", "Schedulicity": r"schedulicity\.com",
    "Booksy": r"booksy\.com", "LeadConnector calendar": r"leadconnectorhq\.com/widget/(booking|bookings)|gohighlevel\.com/widget/booking",
    "Cal.com": r"cal\.com/", "Patient Pop": r"patientpop\.com", "LocalMed": r"localmed\.com",
}
BOOKING_WORDS = re.compile(r"\b(book|schedule|reserve)\s+(an?\s+)?(online|now|appointment|service|visit|consultation|call|today)\b|"
                           r"\brequest\s+an?\s+appointment\b|\bonline\s+(booking|scheduling)\b", re.I)

CHAT_TOOLS = {
    "Tidio": r"tidio\.co|code\.tidio", "Intercom": r"widget\.intercom\.io|intercomcdn", "Drift": r"js\.driftt\.com|drift\.com",
    "LiveChat": r"livechatinc\.com|cdn\.livechat", "Tawk.to": r"tawk\.to", "Crisp": r"crisp\.chat",
    "HubSpot chat": r"js\.hs-scripts\.com|hs-chat|conversations-embed", "Podium": r"podium\.com|connect\.podium",
    "Birdeye": r"birdeye\.com", "Smith.ai": r"smith\.ai", "LeadConnector chat": r"leadconnectorhq\.com/loader|chat-widget|widgets\.leadconnector",
    "Olark": r"olark\.com", "Zendesk chat": r"zopim|zdassets", "Freshchat": r"freshchat|wchat\.freshchat", "Messenger chat": r"customerchat",
    "Weave": r"getweave\.com", "Emitrr": r"emitrr", "Hatch": r"usehatchapp", "Gorgias": r"gorgias",
}

AFTER_HOURS = re.compile(r"24\s*/\s*7|24[- ]hours?|24[- ]hr|round[- ]the[- ]clock|after[- ]hours|emergency|same[- ]day|"
                         r"\bweekends?\b|evening appointments|open (late|saturday|sunday)", re.I)
REVIEWS = re.compile(r"testimonial|customer reviews|patient reviews|client reviews|what our (customers|clients|patients) say|"
                     r"5[- ]star|★|google reviews|trustpilot|yelp|birdeye|reviews\.io|elfsight|reviews? (and|&) ratings", re.I)
FORM_PROVIDERS = re.compile(r"jotform|typeform|wufoo|formstack|hsforms|hubspot.*form|wpcf7|elementor-form|gform_wrapper|"
                            r"forminator|ninja-forms|wpforms|leadconnectorhq\.com/widget/form|form\.jotform", re.I)
SERVICE_AREA = re.compile(r"service areas?|areas? (we )?served?|areas we serve|(proudly |locally )?serving|we serve|zip codes?|"
                          r"counties|cities we serve", re.I)
RECURRING_PLAN = re.compile(r"maintenance (plan|agreement|membership|program|contract)|(service|care|protection|preventive|annual|recurring|"
                            r"pest|lawn) (plan|membership|agreement|program)|\bmembership\b|\bclub\b|subscription", re.I)
ESTIMATES = re.compile(r"free (estimate|quote|inspection|consultation|evaluation|assessment|proposal)s?|request (an? )?(estimate|quote|bid|proposal)|"
                       r"get (an? )?(free )?(estimate|quote)|schedule (an? )?(estimate|inspection)|\bbid request", re.I)
PATIENT_FORMS = re.compile(r"new (patient|client) forms?|(patient|client|intake) forms?|(patient|client) portal|download (our |the )?forms|"
                           r"online forms|paperwork", re.I)
REFERRALS = re.compile(r"referr(al|ing)s?|refer a (patient|client)|physician referral", re.I)
LOCATIONS = re.compile(r"\b(our )?(locations|offices|clinics|branches)\b|find a (location|office|clinic)|multiple locations|"
                       r"(\d+|two|three|four|five|six|seven|eight|nine|ten) (locations|offices|clinics)", re.I)
CONTACT_LINK = re.compile(r"contact|book|schedule|appointment|request|quote|estimate|service", re.I)

_PLATFORMS = [("WordPress", r"wp-content|wp-includes"), ("Wix", r"wixstatic\.com|wix\.com"), ("Squarespace", r"squarespace"),
              ("Webflow", r"webflow"), ("GoDaddy", r"godaddy|secureserver"), ("Shopify", r"cdn\.shopify\.com"),
              ("Weebly", r"weebly"), ("Duda", r"multiscreensite|dudaone")]
_COPYRIGHT = re.compile(r"(?:©|&copy;|copyright)\s*(?:\(c\)\s*)?((?:19|20)\d{2})(?:\s*[-–—]\s*((?:19|20)\d{2}))?", re.I)


def _first_match(patterns: dict[str, str], haystack: str) -> Optional[str]:
    for name, pattern in patterns.items():
        if re.search(pattern, haystack, re.I):
            return name
    return None


def _has_contact_form(soup: BeautifulSoup, raw_html: str) -> bool:
    for form in soup.find_all("form"):
        marker = " ".join([form.get("role", ""), form.get("id", ""), " ".join(form.get("class", [])), form.get("action", "")]).lower()
        if "search" in marker or form.find("input", {"type": "search"}):
            continue
        if form.find("textarea") or form.find("input", {"type": re.compile("^(email|tel)$", re.I)}) or \
                form.find("input", {"name": re.compile("email|phone|message", re.I)}):
            return True
    return bool(FORM_PROVIDERS.search(raw_html))


def _candidate_links(home: Page) -> list[str]:
    """Same-site links that probably hold a contact form or booking page, best first."""
    base_host = (urlparse(home.url).hostname or "").removeprefix("www.")
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for a in BeautifulSoup(home.html, "html.parser").find_all("a", href=True):
        href = urljoin(home.url, a["href"].strip()).split("#")[0]
        parts = urlparse(href)
        if parts.scheme not in ("http", "https") or (parts.hostname or "").removeprefix("www.") != base_host or href in seen:
            continue
        text = f"{parts.path} {a.get_text(' ', strip=True)}"
        if not CONTACT_LINK.search(text) or href.rstrip("/") == home.url.rstrip("/"):
            continue
        seen.add(href)
        score = 0 if re.search(r"contact", text, re.I) else 1 if re.search(r"book|schedule|appointment", text, re.I) else 2
        ranked.append((score, href))
    return [href for _, href in sorted(ranked)][:EXTRA_PAGES]


# ─── Analysis ─────────────────────────────────────────────────────────────────

def analyze(pages: list[Page]) -> dict:
    """Facts from the fetched pages. `pages[0]` is the homepage."""
    home = pages[0]
    raw = "\n".join(p.html for p in pages)
    soups = [BeautifulSoup(p.html, "html.parser") for p in pages]
    text = " ".join(_extract_text(p.html) for p in pages)            # what is stored as the site's text

    booking_tool = _first_match(BOOKING_TOOLS, raw)
    anchor_text = " ".join(el.get_text(" ", strip=True) for s in soups for el in s.find_all(["a", "button"]))
    chat_tool = _first_match(CHAT_TOOLS, raw)

    # Banners and footers ("24/7 emergency service", "© 2016") are exactly what we look for, and _extract_text
    # drops them as boilerplate, so the checks below read the whole visible page instead.
    for soup in soups:
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
    visible = " ".join(s.get_text(" ", strip=True) for s in soups)

    years = [int(y) for m in _COPYRIGHT.finditer(visible) for y in m.groups() if y]
    newest = max(years) if years else None

    platform = next((name for name, pattern in _PLATFORMS if re.search(pattern, raw, re.I)), None)
    mailto = [a["href"][7:].split("?")[0] for s in soups for a in s.find_all("a", href=re.compile("^mailto:", re.I))]

    facts = {
        "site_checked": True,
        "site_pages_checked": len(pages),
        "site_https": home.url.lower().startswith("https://"),
        "site_has_click_to_call": any(s.find("a", href=re.compile(r"^\s*tel:", re.I)) for s in soups),
        "site_has_contact_form": any(_has_contact_form(s, p.html) for s, p in zip(soups, pages)),
        "site_has_online_booking": bool(booking_tool or BOOKING_WORDS.search(anchor_text)),
        "site_booking_tool": booking_tool,
        "site_has_chat": chat_tool is not None,
        "site_chat_tool": chat_tool,
        "site_mentions_after_hours": bool(AFTER_HOURS.search(visible)),
        "site_has_reviews": bool(REVIEWS.search(visible) or REVIEWS.search(raw)),
        "site_mobile_friendly": bool(soups[0].find("meta", attrs={"name": re.compile("^viewport$", re.I)})),
        "site_copyright_year": newest,
        "site_looks_outdated": newest is not None and newest <= datetime.utcnow().year - 3,
        "site_platform": platform,
        # what the agency's workflows hang on (service area, estimates, recurring plans, intake forms, referrals, branches)
        "site_mentions_service_area": bool(SERVICE_AREA.search(visible)),
        "site_invites_estimates": bool(ESTIMATES.search(visible) or ESTIMATES.search(anchor_text)),
        "site_mentions_recurring_plan": bool(RECURRING_PLAN.search(visible)),
        "site_has_patient_forms": bool(PATIENT_FORMS.search(visible) or PATIENT_FORMS.search(anchor_text)),
        "site_mentions_referrals": bool(REFERRALS.search(visible)),
        "site_multiple_locations": bool(LOCATIONS.search(visible) or LOCATIONS.search(anchor_text)),
    }
    return {"facts": facts, "text": text[:10000], "emails": _extract_emails(" ".join([text, *mailto]), raw)}


def collect_site_signals(url: str, fetch: Callable[[str], Optional[Page]] = fetch_page) -> Optional[dict]:
    """
    {"facts": {...}, "text": "...", "emails": [...]} or None when the homepage cannot be fetched.
    `fetch` can be replaced in tests.
    """
    if not url:
        return None
    start = url if "//" in url else f"https://{url}"
    home = fetch(start)
    if home is None and start.startswith("https://"):
        home = fetch("http://" + start[len("https://"):])         # some small-business sites have no https at all
    if home is None:
        return None

    pages = [home]
    for link in _candidate_links(home):
        page = fetch(link)
        if page:
            pages.append(page)
        time.sleep(0.2)                                          # be polite to small servers
    return analyze(pages)
