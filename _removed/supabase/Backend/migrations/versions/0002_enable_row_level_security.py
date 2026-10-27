"""Turn on row level security for every table (PostgreSQL / Supabase only)

Supabase publishes the tables of the public schema through its own web API. Without row level
security anyone who holds the project's public key could read them. Turning it on with no
policies closes that door. This app connects directly as the table owner, which is not
affected, so nothing changes for it.

Every table added by a later migration must be added here too (a test checks this).

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = [
    "call_logs", "connection_status", "dnc_list", "email_logs", "inbound_messages",
    "industry_profiles", "lead_contacts", "leads", "outreach_messages", "outreach_queue",
    "outreach_step_log", "scraping_jobs", "whatsapp_logs",
]


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _postgres():
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    if _postgres():
        for table in TABLES:
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
