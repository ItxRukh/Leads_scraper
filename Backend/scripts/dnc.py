"""
script_09_dnc.py — Phase 9: DNC / Blacklist Manager
------------------------------------------------------
Manages the Do-Not-Contact list.

Rules:
  - Before ANY outreach (email/call/WhatsApp), always check DNC first
  - Auto-add leads who reply with opt-out keywords
  - Manual add/remove via CLI
  - When a lead is on DNC: score = 0, all outreach blocked

Usage:
    python script_09_dnc.py --check --email test@example.com
    python script_09_dnc.py --add --email test@example.com --reason "Unsubscribed"
    python script_09_dnc.py --add --phone "+13051234567" --reason "Requested removal"
    python script_09_dnc.py --remove --email test@example.com
    python script_09_dnc.py --list
    python script_09_dnc.py --export
    python script_09_dnc.py --scan-replies   # Auto-scan email/WhatsApp replies for opt-outs
"""

import argparse
from datetime import datetime
from typing import Optional

from loguru import logger
from rich.console import Console
from rich.table import Table

from utils.console import make_console
from database import (
    DNCList, DNCCrud, Lead, EmailLog, WhatsAppLog,
    SyncSessionLocal, LeadCRUD, init_db
)
from utils.exporters import export_dnc_to_csv
from utils.domain import extract_domain

console = make_console()

# ─── Opt-Out Keywords ─────────────────────────────────────────────────────────
# If a reply contains any of these words/phrases, auto-add to DNC
OPT_OUT_KEYWORDS = [
    "unsubscribe", "remove me", "remove my email", "stop emailing",
    "not interested", "do not contact", "don't contact", "take me off",
    "please remove", "opt out", "opt-out", "no thanks", "no thank you",
    "leave me alone", "stop calling", "never contact",
]


def check_opt_out_keywords(text: str) -> bool:
    """Returns True if the text contains any opt-out keyword."""
    if not text:
        return False
    text_lower = text.lower()
    return any(keyword in text_lower for keyword in OPT_OUT_KEYWORDS)


# ─── Auto-Scan Replies for Opt-Outs ──────────────────────────────────────────

def scan_replies_for_opt_outs(db) -> int:
    """
    Scans all email and WhatsApp reply content for opt-out keywords.
    Automatically adds matches to DNC and marks the lead as do_not_contact.
    
    Returns:
        Number of leads added to DNC
    """
    added_count = 0

    # ── Scan email replies ──────────────────────────────────────────────────
    email_replies = db.query(EmailLog).filter(
        EmailLog.replied == True,
        EmailLog.reply_content != None,
    ).all()

    for log in email_replies:
        if check_opt_out_keywords(log.reply_content):
            lead = db.query(Lead).filter(Lead.id == log.lead_id).first()
            if lead and not lead.do_not_contact:
                # Add to DNC list
                domain = extract_domain(lead.website)
                if not DNCCrud.is_on_dnc(db, email=lead.email, phone=lead.phone, domain=domain):
                    DNCCrud.add_to_dnc(
                        db,
                        email=lead.email,
                        phone=lead.phone,
                        domain=domain,
                        reason=f"opt_out_keyword: Auto-detected opt-out in email reply: '{log.reply_content[:100]}'",
                    )
                    LeadCRUD.mark_do_not_contact(db, lead.id)
                    added_count += 1
                    logger.info(f"Auto-DNC from email reply: {lead.name} ({lead.email})")

    # ── Scan WhatsApp replies ───────────────────────────────────────────────
    wa_replies = db.query(WhatsAppLog).filter(
        WhatsAppLog.replied == True,
        WhatsAppLog.reply_content != None,
    ).all()

    for log in wa_replies:
        if check_opt_out_keywords(log.reply_content):
            lead = db.query(Lead).filter(Lead.id == log.lead_id).first()
            if lead and not lead.do_not_contact:
                domain = extract_domain(lead.website)
                if not DNCCrud.is_on_dnc(db, email=lead.email, phone=lead.phone, domain=domain):
                    DNCCrud.add_to_dnc(
                        db,
                        email=lead.email,
                        phone=lead.phone,
                        domain=domain,
                        reason=f"opt_out_keyword: Auto-detected opt-out in WhatsApp reply: '{log.reply_content[:100]}'",
                    )
                    LeadCRUD.mark_do_not_contact(db, lead.id)
                    added_count += 1
                    logger.info(f"Auto-DNC from WhatsApp reply: {lead.name}")

    return added_count


# ─── CLI Actions ──────────────────────────────────────────────────────────────

def action_check(db, email: str = None, phone: str = None, domain: str = None) -> None:
    """Check if an email/phone/domain is on the DNC list."""
    is_dnc = DNCCrud.is_on_dnc(db, email=email, phone=phone, domain=domain)
    if is_dnc:
        console.print(f"[bold red]⛔ ON DNC LIST[/bold red] — email={email} phone={phone} domain={domain}")
    else:
        console.print(f"[bold green]✅ NOT on DNC list[/bold green] — safe to contact")


def action_add(db, email: str = None, phone: str = None,
               domain: str = None, reason: str = "manual") -> None:
    """Manually add an entry to the DNC list."""
    if not email and not phone and not domain:
        console.print("[red]Provide at least one of: --email, --phone, --domain[/red]")
        return

    # Check if already on DNC
    if DNCCrud.is_on_dnc(db, email=email, phone=phone, domain=domain):
        console.print("[yellow]Already on DNC list — no action taken[/yellow]")
        return

    entry = DNCCrud.add_to_dnc(db, email=email, phone=phone, domain=domain, reason=reason)
    console.print(f"[bold green]✅ Added to DNC:[/bold green] email={email} phone={phone} reason={reason}")

    # Also mark the lead as DNC if we can find them by email
    if email:
        lead = db.query(Lead).filter(Lead.email == email).first()
        if lead:
            LeadCRUD.mark_do_not_contact(db, lead.id)
            console.print(f"  → Lead '{lead.name}' also marked as do_not_contact in leads table")


def action_remove(db, email: str = None, phone: str = None, domain: str = None) -> None:
    """Remove an entry from the DNC list."""
    from sqlalchemy import or_
    query = db.query(DNCList)
    conditions = []
    if email:   conditions.append(DNCList.email == email)
    if phone:   conditions.append(DNCList.phone == phone)
    if domain:  conditions.append(DNCList.domain == domain)

    if not conditions:
        console.print("[red]Provide at least one of: --email, --phone, --domain[/red]")
        return

    entry = query.filter(or_(*conditions)).first()
    if not entry:
        console.print("[yellow]Entry not found on DNC list[/yellow]")
        return

    db.delete(entry)
    db.commit()
    console.print(f"[bold green]✅ Removed from DNC list[/bold green]")

    # Also un-mark any lead(s) that were flagged do_not_contact for this
    # same email/phone — otherwise the lead stays stuck showing "Do not
    # contact" everywhere (badge, Contact Now, outreach status) even though
    # the DNC list entry that caused it is gone. Only clear the flag if the
    # lead isn't ALSO matched by some other remaining DNC entry (e.g. it
    # matched on phone but there's a separate entry for its email too).
    lead_conditions = []
    if email: lead_conditions.append(Lead.email == email)
    if phone: lead_conditions.append(Lead.phone == phone)
    if lead_conditions:
        for lead in db.query(Lead).filter(or_(*lead_conditions), Lead.do_not_contact == True).all():
            still_on_dnc = DNCCrud.is_on_dnc(db, email=lead.email, phone=lead.phone, domain=extract_domain(lead.website))
            if not still_on_dnc:
                lead.do_not_contact = False
                console.print(f"  → Lead '{lead.name}' un-marked as do_not_contact")
        db.commit()


def action_list(db) -> None:
    """Display all DNC entries in a formatted table."""
    entries = DNCCrud.get_all(db)

    if not entries:
        console.print("[yellow]DNC list is empty[/yellow]")
        return

    table = Table(title=f"DNC List ({len(entries)} entries)", show_lines=True)
    table.add_column("ID",     style="dim",    justify="center", width=6)
    table.add_column("Email",  style="yellow", max_width=35)
    table.add_column("Phone",  style="cyan",   max_width=20)
    table.add_column("Domain", style="blue",   max_width=25)
    table.add_column("Reason", style="white",  max_width=40)
    table.add_column("Added",  style="dim",    max_width=20)

    for e in entries:
        table.add_row(
            str(e.id),
            e.email  or "—",
            e.phone  or "—",
            e.domain or "—",
            (e.reason or "—")[:40],
            e.added_at.strftime("%Y-%m-%d %H:%M") if e.added_at else "—",
        )

    console.print(table)


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ecomdropship.ai DNC Manager")

    parser.add_argument("--check",       action="store_true", help="Check if email/phone is on DNC")
    parser.add_argument("--add",         action="store_true", help="Add entry to DNC list")
    parser.add_argument("--remove",      action="store_true", help="Remove entry from DNC list")
    parser.add_argument("--list",        action="store_true", help="Show all DNC entries")
    parser.add_argument("--export",      action="store_true", help="Export DNC list to CSV")
    parser.add_argument("--scan-replies",action="store_true", help="Auto-scan replies for opt-out keywords")

    parser.add_argument("--email",  type=str, help="Email address")
    parser.add_argument("--phone",  type=str, help="Phone number")
    parser.add_argument("--domain", type=str, help="Domain (e.g. example.com)")
    parser.add_argument("--reason", type=str, default="manual", help="Reason for DNC")

    args = parser.parse_args()

    try:
        init_db()
    except Exception:
        pass

    db = SyncSessionLocal()

    try:
        console.rule("[bold cyan]Phase 9: DNC Manager")

        if args.check:
            action_check(db, email=args.email, phone=args.phone, domain=args.domain)

        elif args.add:
            action_add(db, email=args.email, phone=args.phone,
                       domain=args.domain, reason=args.reason)

        elif args.remove:
            action_remove(db, email=args.email, phone=args.phone, domain=args.domain)

        elif args.list:
            action_list(db)

        elif args.export:
            entries = DNCCrud.get_all(db)
            path = export_dnc_to_csv(entries)
            if path:
                console.print(f"[bold green]✅ Exported to: {path}[/bold green]")

        elif args.scan_replies:
            console.print("Scanning all replies for opt-out keywords...")
            count = scan_replies_for_opt_outs(db)
            console.print(f"[bold green]✅ Auto-added {count} leads to DNC from reply scan[/bold green]")

        else:
            parser.print_help()

    finally:
        db.close()