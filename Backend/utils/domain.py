# utils/domain.py — safe domain extraction from a lead's website URL
# --------------------------------------------------------------------
# Replaces the old `website.split("/")[2]` pattern used in 5 places
# (scripts/auditor.py, scripts/dnc.py x2, webhooks/instantly_webhook.py x2).
# That pattern assumed the website always looks like "https://example.com/..."
# and crashes with IndexError on anything else (e.g. "example.com",
# "www.example.com", or a malformed/empty string) — which would silently
# break DNC opt-out handling and audit scoring for that lead.
#
# urlparse() handles all of these safely and returns "" instead of raising
# when there's nothing to parse.

from urllib.parse import urlparse


def extract_domain(website: str | None) -> str | None:
    """
    Safely extracts the domain from a lead's website field.

    Examples:
        "https://www.example.com/path" -> "www.example.com"
        "http://example.com"           -> "example.com"
        "example.com"                  -> "example.com"   (no scheme — old code crashed here)
        "www.example.com"              -> "www.example.com"
        ""  / None                     -> None
    """
    if not website:
        return None

    website = website.strip()
    if not website:
        return None

    # urlparse needs a scheme to correctly split netloc from path;
    # add one if missing so bare domains ("example.com") still work.
    candidate = website if "://" in website else f"//{website}"
    parsed = urlparse(candidate)
    domain = parsed.netloc or parsed.path.split("/")[0]

    return domain or None