"""
update_lead_website.py — Quick script to update a lead's website URL
-----------------------------------------------------------------------
Replaces a test lead's placeholder website ("https://example.com") so the audit
has real content to crawl.

Usage:
    python update_lead_website.py
"""

from database import Lead, SyncSessionLocal, init_db

# ── EDIT THIS BEFORE RUNNING ────────────────────────────────────────────────
LEAD_ID     = 1
NEW_WEBSITE = "https://www.spocket.co"   # <-- yahan koi real website daalo
# ─────────────────────────────────────────────────────────────────────────────


def update_website():
    init_db()
    db = SyncSessionLocal()

    lead = db.query(Lead).filter(Lead.id == LEAD_ID).first()

    if not lead:
        print(f"❌ Lead ID {LEAD_ID} not found.")
        return

    old_website = lead.website
    lead.website = NEW_WEBSITE

    # Reset crawl/audit fields so the auditor treats it as fresh
    lead.website_crawled = False
    lead.crawled_text = None
    lead.audited_at = None

    db.commit()
    db.refresh(lead)

    print(f"✅ Lead {LEAD_ID} website updated!")
    print(f"   Old website: {old_website}")
    print(f"   New website: {lead.website}")

    db.close()


if __name__ == "__main__":
    update_website()