"""
delete_leads.py — Quick utility to clear out test/bad leads
----------------------------------------------------------------
Usage:
    python delete_leads.py --all              # deletes ALL leads
    python delete_leads.py --lead_id 1         # deletes one specific lead
"""

import argparse
from database import Lead, SyncSessionLocal, init_db


def delete_all_leads():
    db = SyncSessionLocal()
    count = db.query(Lead).count()
    db.query(Lead).delete()
    db.commit()
    db.close()
    print(f"\n✅ Deleted {count} lead(s). Database is now empty.")


def delete_one_lead(lead_id: int):
    db = SyncSessionLocal()
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        print(f"\n❌ No lead found with ID {lead_id}")
        db.close()
        return
    name = lead.name
    db.delete(lead)
    db.commit()
    db.close()
    print(f"\n✅ Deleted lead #{lead_id} ({name})")


if __name__ == "__main__":
    init_db()
    parser = argparse.ArgumentParser(description="Delete leads from the database")
    parser.add_argument("--all", action="store_true", help="Delete ALL leads")
    parser.add_argument("--lead_id", type=int, help="Delete a specific lead by ID")
    args = parser.parse_args()

    if args.all:
        delete_all_leads()
    elif args.lead_id:
        delete_one_lead(args.lead_id)
    else:
        print("Specify --all to delete everything, or --lead_id <id> for one lead.")