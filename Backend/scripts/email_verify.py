"""
script_04_email_verify.py — Phase 4: Email Verification
Verifies all lead emails before sending. Prevents spam/bans.
Free local check (syntax, disposable domains, MX lookup) by default;
ZeroBounce is used only if ZEROBOUNCE_API_KEY is set.
Usage:
    python script_04_email_verify.py --all
    python script_04_email_verify.py --lead_id 42
"""

import argparse
import re
import socket
import time
from typing import Optional

import requests

try:
    import dns.exception
    import dns.resolver
    _HAS_DNS = True
except ImportError:  # dnspython missing: fall back to a plain hostname lookup
    _HAS_DNS = False
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn

from utils.console import make_console
from config import settings
from database import Lead, EmailStatus, SyncSessionLocal, LeadCRUD, init_db
from queue_service import enqueue_if_eligible
from scoring_engine import apply_result, score_lead

console = make_console()


_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")

_DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "yopmail.com", "trashmail.com", "sharklasers.com", "getnada.com",
    "throwawaymail.com", "maildrop.cc",
}

# domain -> True/False, so a batch of leads on the same domain costs one lookup
_domain_cache: dict[str, bool] = {}


def _domain_accepts_mail(domain: str) -> Optional[bool]:
    """
    True if the domain has MX (or, per RFC, an A record), False if it does not
    exist or cannot receive mail, None if DNS could not answer right now.
    """
    if domain in _domain_cache:
        return _domain_cache[domain]

    ok: Optional[bool]
    if _HAS_DNS:
        try:
            ok = len(dns.resolver.resolve(domain, "MX", lifetime=5)) > 0
        except dns.resolver.NoAnswer:
            try:
                dns.resolver.resolve(domain, "A", lifetime=5)
                ok = True
            except dns.exception.DNSException:
                ok = False
        except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            ok = False
        except dns.exception.DNSException:
            return None  # timeout / temporary failure — don't cache, don't condemn
    else:
        try:
            socket.gethostbyname(domain)
            ok = True
        except socket.gaierror:
            ok = False

    _domain_cache[domain] = ok
    return ok


def verify_email_local(email: str) -> dict:
    """
    Free verification, no API key: syntax, disposable domains, and whether the
    domain can receive mail (MX lookup). It cannot tell whether the individual
    mailbox exists, so some bounces are still possible — keep daily send limits
    low and treat bounced addresses as invalid.
    Returns the same shape as verify_email_zerobounce().
    """
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return {"status": "invalid", "sub_status": "bad_syntax"}

    domain = email.rsplit("@", 1)[1]
    if domain in _DISPOSABLE_DOMAINS:
        return {"status": "invalid", "sub_status": "disposable"}

    accepts = _domain_accepts_mail(domain)
    if accepts is None:
        return {"status": "unknown", "sub_status": "dns_unavailable"}
    if not accepts:
        return {"status": "invalid", "sub_status": "no_mail_server"}
    return {"status": "valid", "sub_status": "mx_found"}


def verify_email(email: str) -> dict:
    """Free check first, then the paid verifier (MillionVerifier or ZeroBounce) if a key is set. See email_verification.py."""
    from email_verification import verify_address
    result = verify_address(email)
    return {"status": result["status"], "sub_status": result.get("detail", "")}


def verify_email_zerobounce(email: str) -> dict:
    """
    Call ZeroBounce API to verify a single email.
    Returns: {"status": "valid"|"invalid"|"catch-all"|"unknown", "sub_status": ..., "free_email": bool}
    """
    if not settings.ZEROBOUNCE_API_KEY:
        logger.warning("ZEROBOUNCE_API_KEY not set — skipping verification")
        return {"status": "unknown"}

    url = f"{settings.ZEROBOUNCE_BASE_URL}/validate"
    params = {
        "api_key": settings.ZEROBOUNCE_API_KEY,
        "email": email,
        "ip_address": "",  # Optional: pass user IP for geo check
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        return {
            "status": data.get("status", "unknown").lower(),
            "sub_status": data.get("sub_status", ""),
            "free_email": data.get("free_email", False),
            "did_you_mean": data.get("did_you_mean", ""),
        }
    except Exception as e:
        logger.error(f"ZeroBounce error for {email}: {e}")
        return {"status": "unknown"}


def map_zerobounce_status(zb_status: str) -> EmailStatus:
    """Map ZeroBounce status string to our EmailStatus enum"""
    mapping = {
        "valid": EmailStatus.VALID,
        "invalid": EmailStatus.INVALID,
        "catch-all": EmailStatus.CATCH_ALL,
        "unknown": EmailStatus.UNKNOWN,
        "spamtrap": EmailStatus.INVALID,
        "abuse": EmailStatus.INVALID,
        "do_not_mail": EmailStatus.INVALID,
    }
    return mapping.get(zb_status.lower(), EmailStatus.UNKNOWN)


def rescore_after_verification(db, lead: Lead) -> None:
    """
    A verified email can earn points (depends on the lead's profile), so score
    the lead again and queue it if it now qualifies. A lead that was never
    audited keeps its flag only; its first audit will count it.
    """
    if lead.audited_at is None:
        db.commit()
        return
    apply_result(lead, score_lead(db, lead))
    db.commit()
    enqueue_if_eligible(db, lead)


def run_email_verifier(lead_id: Optional[int] = None, niche: Optional[str] = None,
                        city: Optional[str] = None) -> None:
    """
    Verify emails for all leads or a single lead.

    niche, city: if given, only verifies unverified leads matching this
                 niche/city (used by post_scrape_pipeline.py to scope the
                 chained run to just the batch that was scraped). Leave
                 both None for the old global behaviour (standalone
                 "Verify Emails" button / /pipeline/verify-emails endpoint).
    """

    console.rule("[bold cyan]Phase 4: Email Verification")

    db = SyncSessionLocal()

    if lead_id:
        leads = [db.query(Lead).filter(Lead.id == lead_id).first()]
    else:
        # Only verify leads with emails that haven't been verified yet
        query = db.query(Lead).filter(
            Lead.email != None,
            Lead.email != "",
            Lead.email_verified == False,
            Lead.do_not_contact == False,
        )
        if niche:
            query = query.filter(Lead.niche == niche)
        if city:
            query = query.filter(Lead.city == city)
        leads = query.all()

    if not leads:
        console.print("[yellow]No leads with unverified emails found.[/yellow]")
        return

    console.print(f"Verifying [bold]{len(leads)}[/bold] email addresses...")

    valid_count = invalid_count = catchall_count = unknown_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Verifying emails...", total=len(leads))

        for lead in leads:
            progress.update(task, description=f"Verifying: {lead.email[:40] if lead.email else 'N/A'}")

            if not lead.email:
                progress.advance(task)
                continue

            result = verify_email(lead.email)
            status_str = result.get("status", "unknown")
            email_status = map_zerobounce_status(status_str)

            lead.email_status = email_status

            if email_status == EmailStatus.VALID:
                lead.email_verified = True
                rescore_after_verification(db, lead)
                valid_count += 1

            elif email_status == EmailStatus.CATCH_ALL:
                lead.email_verified = False  # Proceed with caution
                catchall_count += 1

            elif email_status == EmailStatus.INVALID:
                lead.email_verified = False
                invalid_count += 1
                logger.debug(f"Invalid email for {lead.name}: {lead.email}")

            else:
                unknown_count += 1

            # Check for suggested correction
            if result.get("did_you_mean"):
                logger.info(f"ZeroBounce suggestion for {lead.email}: did you mean {result['did_you_mean']}?")

            db.commit()
            progress.advance(task)
            if settings.ZEROBOUNCE_API_KEY:
                time.sleep(0.2)  # Rate limit: 5 req/sec (ZeroBounce only)

    console.rule("[bold green]Phase 4 Complete!")
    console.print(f"  ✅ Valid emails:    [bold green]{valid_count}[/bold green]")
    console.print(f"  ⚠️  Catch-all:      [bold yellow]{catchall_count}[/bold yellow]")
    console.print(f"  ❌ Invalid emails:  [bold red]{invalid_count}[/bold red]")
    console.print(f"  ❓ Unknown:         [bold]{unknown_count}[/bold]")
    console.print(f"\n[dim]Next step: python script_05_email_outreach.py --min_score 40[/dim]")

    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email Verifier")
    parser.add_argument("--all", action="store_true", help="Verify all unverified emails")
    parser.add_argument("--lead_id", type=int, help="Verify specific lead by ID")
    args = parser.parse_args()

    try:
        init_db()
    except Exception:
        pass

    run_email_verifier(lead_id=args.lead_id)