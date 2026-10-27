"""baseline: every table as of the first PostgreSQL (Supabase) setup

Revision ID: 0001
Revises: 
Create Date: 2026-10-08 01:37:39.437237
"""
from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Created with `alembic revision --autogenerate`; later changes get their own revision.
    op.create_table('connection_status',
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=True),
    sa.Column('detail', sa.JSON(), nullable=True),
    sa.Column('last_seen', sa.DateTime(), nullable=True),
    sa.Column('last_ok', sa.DateTime(), nullable=True),
    sa.Column('last_error', sa.String(length=300), nullable=True),
    sa.PrimaryKeyConstraint('channel')
    )
    op.create_table('dnc_list',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('domain', sa.String(length=255), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('added_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_dnc_list_domain'), 'dnc_list', ['domain'], unique=False)
    op.create_index(op.f('ix_dnc_list_email'), 'dnc_list', ['email'], unique=False)
    op.create_index(op.f('ix_dnc_list_phone'), 'dnc_list', ['phone'], unique=False)
    op.create_table('industry_profiles',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('slug', sa.String(length=140), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('target_type', sa.String(length=20), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=True),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name'),
    sa.UniqueConstraint('slug')
    )
    op.create_table('scraping_jobs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('niche', sa.String(length=100), nullable=False),
    sa.Column('city', sa.String(length=100), nullable=False),
    sa.Column('min_rating', sa.Float(), nullable=True),
    sa.Column('min_reviews', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=True),
    sa.Column('total_google', sa.Integer(), nullable=True),
    sa.Column('total_serpapi', sa.Integer(), nullable=True),
    sa.Column('total_yelp', sa.Integer(), nullable=True),
    sa.Column('total_scraped', sa.Integer(), nullable=True),
    sa.Column('total_raw_matches', sa.Integer(), nullable=True),
    sa.Column('skipped_low_rating', sa.Integer(), nullable=True),
    sa.Column('skipped_duplicate_batch', sa.Integer(), nullable=True),
    sa.Column('skipped_duplicate_existing', sa.Integer(), nullable=True),
    sa.Column('skipped_over_cap', sa.Integer(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('leads',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('address', sa.String(length=500), nullable=True),
    sa.Column('city', sa.String(length=100), nullable=True),
    sa.Column('website', sa.String(length=500), nullable=True),
    sa.Column('niche', sa.String(length=100), nullable=True),
    sa.Column('source', sa.Enum('GOOGLE_PLACES', 'SERPAPI', 'YELP', 'MANUAL', name='leadsource'), nullable=True),
    sa.Column('google_place_id', sa.String(length=200), nullable=True),
    sa.Column('yelp_business_id', sa.String(length=200), nullable=True),
    sa.Column('profile_id', sa.Integer(), nullable=True),
    sa.Column('country', sa.String(length=100), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=True),
    sa.Column('attributes', sa.JSON(), nullable=True),
    sa.Column('signals', sa.JSON(), nullable=True),
    sa.Column('rating', sa.Float(), nullable=True),
    sa.Column('review_count', sa.Integer(), nullable=True),
    sa.Column('website_crawled', sa.Boolean(), nullable=True),
    sa.Column('crawled_text', sa.Text(), nullable=True),
    sa.Column('dropship_mentioned', sa.Boolean(), nullable=True),
    sa.Column('processing_days_mentioned', sa.String(length=100), nullable=True),
    sa.Column('delivery_days_mentioned', sa.String(length=100), nullable=True),
    sa.Column('processing_fast', sa.Boolean(), nullable=True),
    sa.Column('delivery_fast', sa.Boolean(), nullable=True),
    sa.Column('sells_on_amazon', sa.Boolean(), nullable=True),
    sa.Column('sells_on_etsy', sa.Boolean(), nullable=True),
    sa.Column('sells_on_shopify', sa.Boolean(), nullable=True),
    sa.Column('sells_on_marketplace', sa.Boolean(), nullable=True),
    sa.Column('marketplace_urls', sa.Text(), nullable=True),
    sa.Column('linkedin_found', sa.Boolean(), nullable=True),
    sa.Column('linkedin_name', sa.String(length=255), nullable=True),
    sa.Column('linkedin_title', sa.String(length=255), nullable=True),
    sa.Column('linkedin_url', sa.String(length=500), nullable=True),
    sa.Column('email_verified', sa.Boolean(), nullable=True),
    sa.Column('email_status', sa.Enum('VALID', 'INVALID', 'CATCH_ALL', 'UNKNOWN', name='emailstatus'), nullable=True),
    sa.Column('base_score', sa.Integer(), nullable=True),
    sa.Column('bonus_score', sa.Integer(), nullable=True),
    sa.Column('final_score', sa.Integer(), nullable=True),
    sa.Column('tier', sa.Enum('HOT', 'WARM', 'COLD', 'UNSCORED', name='leadtier'), nullable=True),
    sa.Column('audit_summary', sa.Text(), nullable=True),
    sa.Column('score_breakdown', sa.JSON(), nullable=True),
    sa.Column('score_incomplete', sa.Boolean(), nullable=True),
    sa.Column('score_incomplete_reason', sa.Text(), nullable=True),
    sa.Column('do_not_contact', sa.Boolean(), nullable=True),
    sa.Column('pipeline_stage', sa.Enum('SCRAPED', 'LINKEDIN_ENRICHED', 'AUDITED', 'EMAIL_VERIFIED', 'EMAIL_SENT', 'EMAIL_OPENED', 'REPLIED', 'CALL_PLACED', 'WHATSAPP_SENT', 'DEMO_BOOKED', 'ONBOARDED', 'REJECTED', 'DNC', name='pipelinestage'), nullable=True),
    sa.Column('scraped_at', sa.DateTime(), nullable=True),
    sa.Column('audited_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['profile_id'], ['industry_profiles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('google_place_id')
    )
    op.create_index('ix_leads_do_not_contact', 'leads', ['do_not_contact'], unique=False)
    op.create_index('ix_leads_email', 'leads', ['email'], unique=False)
    op.create_index('ix_leads_niche_city', 'leads', ['niche', 'city'], unique=False)
    op.create_index('ix_leads_pipeline', 'leads', ['pipeline_stage'], unique=False)
    op.create_index('ix_leads_profile', 'leads', ['profile_id'], unique=False)
    op.create_index('ix_leads_score_tier', 'leads', ['final_score', 'tier'], unique=False)
    op.create_table('call_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('vapi_call_id', sa.String(length=200), nullable=True),
    sa.Column('called_at', sa.DateTime(), nullable=True),
    sa.Column('duration_sec', sa.Integer(), nullable=True),
    sa.Column('outcome', sa.Enum('INTERESTED', 'NOT_INTERESTED', 'CALLBACK', 'NO_ANSWER', 'WRONG_NUMBER', 'VOICEMAIL', name='calloutcome'), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('recording_url', sa.String(length=500), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('email_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.String(length=200), nullable=True),
    sa.Column('instantly_lead_id', sa.String(length=200), nullable=True),
    sa.Column('subject', sa.String(length=500), nullable=True),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('personalized_for', sa.String(length=255), nullable=True),
    sa.Column('email_opened', sa.Boolean(), nullable=True),
    sa.Column('opened_at', sa.DateTime(), nullable=True),
    sa.Column('replied', sa.Boolean(), nullable=True),
    sa.Column('replied_at', sa.DateTime(), nullable=True),
    sa.Column('reply_content', sa.Text(), nullable=True),
    sa.Column('unsubscribed', sa.Boolean(), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('inbound_messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('message_key', sa.String(length=300), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=True),
    sa.Column('lead_id', sa.Integer(), nullable=True),
    sa.Column('sender', sa.String(length=255), nullable=True),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('received_at', sa.DateTime(), nullable=True),
    sa.Column('label', sa.String(length=12), nullable=True),
    sa.Column('summary', sa.String(length=300), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('message_key')
    )
    op.create_table('lead_contacts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('value', sa.String(length=500), nullable=False),
    sa.Column('label', sa.String(length=100), nullable=True),
    sa.Column('is_primary', sa.Boolean(), nullable=True),
    sa.Column('verified', sa.Boolean(), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=True),
    sa.Column('source', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_lead_contacts_lead_kind', 'lead_contacts', ['lead_id', 'kind'], unique=False)
    op.create_index('ix_lead_contacts_value', 'lead_contacts', ['value'], unique=False)
    op.create_table('outreach_queue',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=True),
    sa.Column('tier', sa.String(length=20), nullable=True),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('queued_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.Column('sequence_status', sa.String(length=20), nullable=True),
    sa.Column('stop_reason', sa.String(length=120), nullable=True),
    sa.Column('step_index', sa.Integer(), nullable=True),
    sa.Column('next_step_key', sa.String(length=40), nullable=True),
    sa.Column('next_channel', sa.String(length=20), nullable=True),
    sa.Column('next_due_at', sa.DateTime(), nullable=True),
    sa.Column('last_done_at', sa.DateTime(), nullable=True),
    sa.Column('revisit_at', sa.DateTime(), nullable=True),
    sa.Column('reopened_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['profile_id'], ['industry_profiles.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('lead_id')
    )
    op.create_index('ix_queue_due', 'outreach_queue', ['sequence_status', 'next_due_at'], unique=False)
    op.create_index('ix_queue_status', 'outreach_queue', ['status'], unique=False)
    op.create_table('outreach_step_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('step_key', sa.String(length=40), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_step_log_channel_at', 'outreach_step_log', ['channel', 'at'], unique=False)
    op.create_table('whatsapp_logs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('twilio_message_id', sa.String(length=200), nullable=True),
    sa.Column('message_content', sa.Text(), nullable=True),
    sa.Column('delivery_status', sa.String(length=50), nullable=True),
    sa.Column('replied', sa.Boolean(), nullable=True),
    sa.Column('reply_content', sa.Text(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('replied_at', sa.DateTime(), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('outreach_messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('lead_id', sa.Integer(), nullable=False),
    sa.Column('step_key', sa.String(length=40), nullable=False),
    sa.Column('channel', sa.String(length=20), nullable=False),
    sa.Column('subject', sa.String(length=500), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=True),
    sa.Column('mode', sa.String(length=10), nullable=True),
    sa.Column('attempts', sa.Integer(), nullable=True),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.Column('external_id', sa.String(length=300), nullable=True),
    sa.Column('release_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('claimed_at', sa.DateTime(), nullable=True),
    sa.Column('sent_at', sa.DateTime(), nullable=True),
    sa.Column('kind', sa.String(length=10), nullable=True),
    sa.Column('in_reply_to_id', sa.Integer(), nullable=True),
    sa.Column('tracking_token', sa.String(length=40), nullable=True),
    sa.Column('opened_at', sa.DateTime(), nullable=True),
    sa.Column('open_count', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['in_reply_to_id'], ['inbound_messages.id'], ),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_messages_lead_step', 'outreach_messages', ['lead_id', 'step_key'], unique=False)
    op.create_index('ix_messages_status', 'outreach_messages', ['channel', 'status', 'release_at'], unique=False)
    op.create_index(op.f('ix_outreach_messages_tracking_token'), 'outreach_messages', ['tracking_token'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_outreach_messages_tracking_token'), table_name='outreach_messages')
    op.drop_index('ix_messages_status', table_name='outreach_messages')
    op.drop_index('ix_messages_lead_step', table_name='outreach_messages')
    op.drop_table('outreach_messages')
    op.drop_table('whatsapp_logs')
    op.drop_index('ix_step_log_channel_at', table_name='outreach_step_log')
    op.drop_table('outreach_step_log')
    op.drop_index('ix_queue_status', table_name='outreach_queue')
    op.drop_index('ix_queue_due', table_name='outreach_queue')
    op.drop_table('outreach_queue')
    op.drop_index('ix_lead_contacts_value', table_name='lead_contacts')
    op.drop_index('ix_lead_contacts_lead_kind', table_name='lead_contacts')
    op.drop_table('lead_contacts')
    op.drop_table('inbound_messages')
    op.drop_table('email_logs')
    op.drop_table('call_logs')
    op.drop_index('ix_leads_score_tier', table_name='leads')
    op.drop_index('ix_leads_profile', table_name='leads')
    op.drop_index('ix_leads_pipeline', table_name='leads')
    op.drop_index('ix_leads_niche_city', table_name='leads')
    op.drop_index('ix_leads_email', table_name='leads')
    op.drop_index('ix_leads_do_not_contact', table_name='leads')
    op.drop_table('leads')
    op.drop_table('scraping_jobs')
    op.drop_table('industry_profiles')
    op.drop_index(op.f('ix_dnc_list_phone'), table_name='dnc_list')
    op.drop_index(op.f('ix_dnc_list_email'), table_name='dnc_list')
    op.drop_index(op.f('ix_dnc_list_domain'), table_name='dnc_list')
    op.drop_table('dnc_list')
    op.drop_table('connection_status')
    # PostgreSQL keeps enum types after their tables are gone
    for name in ("leadsource", "emailstatus", "leadtier", "pipelinestage", "calloutcome"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
