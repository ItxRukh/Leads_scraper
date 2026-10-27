"""
utils/crawler.py — Website Crawler
------------------------------------
Crawls a business website and extracts:
  - All visible text (for Groq AI analysis)
  - Email addresses (via regex)
  - Dropshipping keywords
  - Processing & delivery time mentions
  - Amazon / Etsy / Shopify links

Pages crawled per site:
  1. Homepage (/)
  2. About page (/about or /about-us)
  3. Shipping page (/shipping or /shipping-policy)

Libraries used:
  - requests      → Makes HTTP GET requests to fetch page HTML
  - BeautifulSoup → Parses the HTML and lets us extract text/links cleanly
  - re            → Regular expressions for finding emails and time patterns

Usage:
    from utils.crawler import crawl_website
    result = crawl_website("https://example.com")
"""

import re
import json
import time
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from loguru import logger

from config import settings


# ─── Keywords to Search For ───────────────────────────────────────────────────

# If any of these words appear on the website → dropship_mentioned = True (+50 pts)
DROPSHIP_KEYWORDS = [
    "dropship", "drop ship", "dropshipping", "drop shipping",
    "wholesale dropship", "willing to dropship", "dropship program",
    "dropship partner", "dropship ready",
]

# Regex pattern to find email addresses in raw text/HTML
# Explanation: word chars + optional dots/dashes + @ + domain + .tld
EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE
)

# Pattern to find processing time mentions like "1-2 business days", "24 hours"
PROCESSING_REGEX = re.compile(
    r"(process(?:ing)?|ship(?:ment)?|dispatch|handle|fulfil(?:l)?)\w*"
    r"[\w\s,]*?(\d+[\-–]\d+|\d+)\s*(business\s+)?days?|hours?",
    re.IGNORECASE
)

# Pattern to find delivery time mentions
DELIVERY_REGEX = re.compile(
    r"(deliver(?:y)?|arrival|transit|shipping time|estimated\s+delivery)"
    r"[\w\s,]*?(\d+[\-–]\d+|\d+)\s*(business\s+)?days?",
    re.IGNORECASE
)

# Marketplace detection: check if website links to or mentions these platforms
MARKETPLACE_PATTERNS = {
    "amazon": [
        re.compile(r"amazon\.com/(?:shops?|dp|gp/product)", re.IGNORECASE),
        re.compile(r"(?:shop|find|buy)\s+(?:us\s+)?on\s+amazon", re.IGNORECASE),
    ],
    "etsy": [
        re.compile(r"etsy\.com/shop/", re.IGNORECASE),
        re.compile(r"(?:shop|find|buy)\s+(?:us\s+)?on\s+etsy", re.IGNORECASE),
    ],
    "shopify": [
        re.compile(r"myshopify\.com", re.IGNORECASE),
        re.compile(r"powered\s+by\s+shopify", re.IGNORECASE),
        re.compile(r"cdn\.shopify\.com", re.IGNORECASE),
    ],
}

# Common paths to try for shipping and about pages
SHIPPING_PATHS = ["/shipping", "/shipping-policy", "/shipping-info", "/delivery"]
ABOUT_PATHS    = ["/about", "/about-us", "/our-story", "/company"]


# ─── HTTP Session ─────────────────────────────────────────────────────────────

def _get_session() -> requests.Session:
    """
    Creates a requests Session with headers that mimic a real browser.
    Some websites block requests without a User-Agent header.
    Using a Session is better than requests.get() because it reuses
    the TCP connection (faster for multiple pages on same domain).
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": settings.CRAWLER_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    })
    return session


# ─── Single Page Fetcher ──────────────────────────────────────────────────────

def _fetch_page(session: requests.Session, url: str) -> Optional[str]:
    """
    Fetches a single URL and returns the HTML as a string.
    Returns None if the page doesn't exist or times out.
    
    Args:
        session: Shared requests.Session (for connection reuse)
        url: Full URL to fetch (e.g. "https://example.com/about")
    
    Returns:
        Raw HTML string or None on failure
    """
    try:
        response = session.get(
            url,
            timeout=settings.CRAWLER_TIMEOUT,
            allow_redirects=True,
        )
        # Only process successful responses (status 200-299)
        if response.status_code == 200:
            return response.text
        logger.debug(f"Page returned {response.status_code}: {url}")
        return None
    except requests.exceptions.Timeout:
        logger.debug(f"Timeout fetching: {url}")
        return None
    except requests.exceptions.ConnectionError:
        logger.debug(f"Connection error: {url}")
        return None
    except Exception as e:
        logger.debug(f"Fetch error for {url}: {e}")
        return None


# ─── HTML → Clean Text ────────────────────────────────────────────────────────

def _extract_text(html: str) -> str:
    """
    Converts raw HTML into clean readable text.
    
    BeautifulSoup parses the HTML tree, then we:
      1. Remove <script> and <style> tags (not useful text)
      2. Extract all remaining text
      3. Clean up extra whitespace
    
    Args:
        html: Raw HTML string
    
    Returns:
        Clean plain text
    """
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style tags — their content is code, not readable text
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()  # decompose() removes the tag and its content from the tree

    text = soup.get_text(separator=" ")  # Join all text nodes with a space

    # Collapse multiple whitespace/newlines into single spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ─── Email Extractor ──────────────────────────────────────────────────────────

def _extract_emails(text: str, html: str) -> list[str]:
    """
    Finds all email addresses in the page text and HTML.
    Searches both because some emails are in href="mailto:..." links
    which might not appear in extracted text.
    
    Filters out common false positives like image filenames.
    """
    found = set()

    # Search in visible text
    found.update(EMAIL_REGEX.findall(text))

    # Also search raw HTML (catches mailto: links)
    found.update(EMAIL_REGEX.findall(html))

    # Filter out common false positives
    filtered = []
    skip_domains = {"example.com", "domain.com", "email.com", "youremail.com",
                    "sentry.io", "wixpress.com", "squarespace.com"}
    for email in found:
        domain = email.split("@")[-1].lower()
        if domain not in skip_domains and not email.endswith(".png") and not email.endswith(".jpg"):
            filtered.append(email.lower())

    return list(set(filtered))  # Remove duplicates


# ─── Dropship Keyword Detector ────────────────────────────────────────────────

def _detect_dropship(text: str) -> bool:
    """
    Returns True if any dropshipping keyword is found in the page text.
    Case-insensitive.
    """
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in DROPSHIP_KEYWORDS)


# ─── Processing & Delivery Time Detector ─────────────────────────────────────

def _extract_number_from_match(match_str: str) -> Optional[int]:
    """
    Extracts the largest number from a string like '1-2' or '3'.
    We take the max to be conservative (e.g. '1-2 days' → max is 2).
    """
    numbers = re.findall(r"\d+", match_str)
    if numbers:
        return max(int(n) for n in numbers)
    return None


def _detect_processing_time(text: str) -> tuple[bool, Optional[str]]:
    """
    Returns (processing_fast, raw_mention) where:
      processing_fast = True if processing time ≤ 2 days
      raw_mention = the actual text found, e.g. "1-2 business days"
    """
    match = PROCESSING_REGEX.search(text)
    if not match:
        return False, None

    raw = match.group(0).strip()
    max_days = _extract_number_from_match(raw)

    if max_days and max_days <= 2:
        return True, raw
    return False, raw


def _detect_delivery_time(text: str) -> tuple[bool, Optional[str]]:
    """
    Returns (delivery_fast, raw_mention) where:
      delivery_fast = True if delivery time ≤ 5 days
      raw_mention = the actual text found, e.g. "3-5 business days"
    """
    match = DELIVERY_REGEX.search(text)
    if not match:
        return False, None

    raw = match.group(0).strip()
    max_days = _extract_number_from_match(raw)

    if max_days and max_days <= 5:
        return True, raw
    return False, raw


# ─── Marketplace Detector ─────────────────────────────────────────────────────

def _detect_marketplaces(text: str, html: str) -> dict:
    """
    Detects if the business sells on Amazon, Etsy, or Shopify.
    Checks both page text and raw HTML (for embedded links/badges).
    
    Returns:
        {
            "sells_on_amazon": bool,
            "sells_on_etsy": bool,
            "sells_on_shopify": bool,
            "marketplace_urls": list[str]
        }
    """
    result = {"sells_on_amazon": False, "sells_on_etsy": False,
              "sells_on_shopify": False, "marketplace_urls": []}
    combined = text + " " + html  # Search both

    for platform, patterns in MARKETPLACE_PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(combined)
            if match:
                result[f"sells_on_{platform}"] = True
                # Try to extract the actual URL if it's a link
                url_match = re.search(
                    rf"https?://(?:www\.)?{platform}\.com/[^\s\"'<>]+",
                    combined, re.IGNORECASE
                )
                if url_match:
                    result["marketplace_urls"].append(url_match.group(0))
                break  # Found for this platform, no need to check more patterns

    return result


# ─── Main Crawler Function ────────────────────────────────────────────────────

def crawl_website(base_url: str) -> Optional[dict]:
    """
    Main entry point. Crawls a business website and returns extracted data.
    
    Steps:
      1. Fetch homepage
      2. Try to fetch /about and /shipping pages
      3. Combine all text from all pages
      4. Run all detectors on combined text
    
    Args:
        base_url: Business website URL (e.g. "https://exampletoys.com")
    
    Returns:
        dict with all extracted fields, or None if site is unreachable
    """
    if not base_url:
        return None

    # Normalize URL — make sure it has https://
    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    # Extract domain for building sub-page URLs
    parsed = urlparse(base_url)
    domain_root = f"{parsed.scheme}://{parsed.netloc}"

    session = _get_session()
    all_text = ""
    all_html = ""

    logger.debug(f"Crawling: {base_url}")

    # ── Step 1: Fetch Homepage ─────────────────────────────────────────────
    homepage_html = _fetch_page(session, base_url)
    if not homepage_html:
        logger.debug(f"Homepage unreachable: {base_url}")
        return None

    all_html += homepage_html
    all_text += _extract_text(homepage_html) + " "

    # ── Step 2: Fetch About Page ───────────────────────────────────────────
    for path in ABOUT_PATHS:
        about_html = _fetch_page(session, domain_root + path)
        if about_html:
            all_html += about_html
            all_text += _extract_text(about_html) + " "
            break  # Found one, stop trying other paths
        time.sleep(0.3)  # Small delay to be polite to servers

    # ── Step 3: Fetch Shipping Page ────────────────────────────────────────
    for path in SHIPPING_PATHS:
        shipping_html = _fetch_page(session, domain_root + path)
        if shipping_html:
            all_html += shipping_html
            all_text += _extract_text(shipping_html) + " "
            break
        time.sleep(0.3)

    # ── Step 4: Run All Detectors ──────────────────────────────────────────
    emails = _extract_emails(all_text, all_html)
    dropship = _detect_dropship(all_text)
    processing_fast, processing_mention = _detect_processing_time(all_text)
    delivery_fast, delivery_mention = _detect_delivery_time(all_text)
    marketplace_data = _detect_marketplaces(all_text, all_html)

    sells_on_any = any([
        marketplace_data["sells_on_amazon"],
        marketplace_data["sells_on_etsy"],
        marketplace_data["sells_on_shopify"],
    ])

    result = {
        "website_crawled": True,
        "crawled_text": all_text[:10000],   # Store first 10k chars (enough for Groq analysis)
        "emails_found": emails,
        "dropship_mentioned": dropship,
        "processing_fast": processing_fast,
        "processing_days_mentioned": processing_mention,
        "delivery_fast": delivery_fast,
        "delivery_days_mentioned": delivery_mention,
        "sells_on_amazon": marketplace_data["sells_on_amazon"],
        "sells_on_etsy": marketplace_data["sells_on_etsy"],
        "sells_on_shopify": marketplace_data["sells_on_shopify"],
        "sells_on_marketplace": sells_on_any,
        "marketplace_urls": json.dumps(marketplace_data["marketplace_urls"]),
    }

    logger.debug(
        f"Crawled {base_url} | "
        f"Emails: {len(emails)} | "
        f"Dropship: {dropship} | "
        f"Marketplace: {sells_on_any}"
    )

    return result
