"""
utils/exporters.py — CSV Export Helper
-----------------------------------------
Exports lead data from the database to CSV files.

Why CSV?
  - Universal format — opens in Excel, Google Sheets, any tool
  - Easy to share with non-technical team members
  - Can be re-imported into other CRMs

Usage:
    from utils.exporters import export_leads_to_csv
    path = export_leads_to_csv(leads, niche="toys", city="Miami FL")
"""

import csv
import os
from datetime import datetime
from typing import List

from loguru import logger


# ─── Column Definitions ───────────────────────────────────────────────────────

# These are the columns that will appear in the exported CSV
# Order matters — this is the column order left-to-right in the file
LEAD_EXPORT_COLUMNS = [
    "id",
    "name",
    "phone",
    "email",
    "address",
    "city",
    "niche",
    "website",
    "rating",
    "review_count",
    "source",

    # Scoring
    "base_score",
    "bonus_score",
    "final_score",
    "tier",

    # Website analysis
    "dropship_mentioned",
    "processing_fast",
    "processing_days_mentioned",
    "delivery_fast",
    "delivery_days_mentioned",

    # Marketplace
    "sells_on_marketplace",
    "sells_on_amazon",
    "sells_on_etsy",
    "sells_on_shopify",

    # LinkedIn
    "linkedin_found",
    "linkedin_name",
    "linkedin_title",
    "linkedin_url",

    # Email verification
    "email_verified",
    "email_status",

    # Status
    "do_not_contact",
    "pipeline_stage",

    # Timestamps
    "scraped_at",
    "audited_at",
]


# ─── Main Export Function ─────────────────────────────────────────────────────

def export_leads_to_csv(leads: list, niche: str = "all", city: str = "all") -> str:
    """
    Exports a list of Lead objects to a CSV file in the /exports directory.
    
    Args:
        leads: List of Lead SQLAlchemy model instances
        niche: Used in the filename (e.g. "toys")
        city: Used in the filename (e.g. "Miami_FL")
    
    Returns:
        Path to the created CSV file (e.g. "exports/leads_toys_Miami_FL_20250101_120000.csv")
    """
    # Ensure exports directory exists
    os.makedirs("exports", exist_ok=True)

    # Build filename with timestamp so files don't overwrite each other
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    city_clean = city.replace(" ", "_").replace(",", "")
    filename = f"exports/leads_{niche}_{city_clean}_{timestamp}.csv"

    if not leads:
        logger.warning("No leads to export — CSV not created")
        return ""

    try:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            # DictWriter makes it easy to write objects as rows
            # extrasaction="ignore" means extra fields on the object are silently skipped
            writer = csv.DictWriter(f, fieldnames=LEAD_EXPORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()

            for lead in leads:
                # Convert SQLAlchemy model to dict
                # getattr(lead, col, "") safely returns "" if a field doesn't exist
                row = {}
                for col in LEAD_EXPORT_COLUMNS:
                    value = getattr(lead, col, "")

                    # Convert enum values to their string representation
                    if hasattr(value, "value"):
                        value = value.value

                    # Format datetime objects as readable strings
                    if isinstance(value, datetime):
                        value = value.strftime("%Y-%m-%d %H:%M:%S")

                    # Convert booleans to Yes/No for readability in Excel
                    if isinstance(value, bool):
                        value = "Yes" if value else "No"

                    row[col] = value if value is not None else ""

                writer.writerow(row)

        logger.info(f"Exported {len(leads)} leads to {filename}")
        return filename

    except Exception as e:
        logger.error(f"CSV export failed: {e}")
        return ""


def export_logs_to_csv(rows: List[dict], columns: List[str], filename_prefix: str) -> str:
    """
    Generic exporter for log-style data that's already been serialized to
    plain dicts (e.g. email/call/WhatsApp logs from the API layer).

    Args:
        rows: list of plain dicts (already JSON-serializable — no SQLAlchemy
              objects or enums here, unlike export_leads_to_csv)
        columns: column order for the CSV
        filename_prefix: e.g. "email_logs", "call_logs", "whatsapp_logs"

    Returns:
        Path to the created CSV file, or "" if there was nothing to export.
    """
    os.makedirs("exports", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"exports/{filename_prefix}_{timestamp}.csv"

    if not rows:
        logger.warning(f"No {filename_prefix} rows to export — CSV not created")
        return ""

    try:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                clean = {}
                for col in columns:
                    value = row.get(col, "")
                    if isinstance(value, bool):
                        value = "Yes" if value else "No"
                    clean[col] = value if value is not None else ""
                writer.writerow(clean)

        logger.info(f"Exported {len(rows)} rows to {filename}")
        return filename

    except Exception as e:
        logger.error(f"{filename_prefix} CSV export failed: {e}")
        return ""


def export_dnc_to_csv(dnc_entries: list) -> str:
    """
    Exports the DNC (Do-Not-Contact) list to CSV.
    
    Args:
        dnc_entries: List of DNCList model instances
    
    Returns:
        Path to created CSV file
    """
    os.makedirs("exports", exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"exports/dnc_list_{timestamp}.csv"

    columns = ["id", "email", "phone", "domain", "reason", "added_at"]

    try:
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()

            for entry in dnc_entries:
                row = {col: getattr(entry, col, "") for col in columns}
                if isinstance(row.get("added_at"), datetime):
                    row["added_at"] = row["added_at"].strftime("%Y-%m-%d %H:%M:%S")
                writer.writerow(row)

        logger.info(f"Exported {len(dnc_entries)} DNC entries to {filename}")
        return filename

    except Exception as e:
        logger.error(f"DNC CSV export failed: {e}")
        return ""