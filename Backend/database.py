"""
database.py — Database Models, Schema & CRUD Operations
---------------------------------------------------------
This file does three things:
  1. Defines all database TABLES as Python classes (SQLAlchemy ORM models)
  2. Creates the database engine and session
  3. Provides CRUD helper classes (Create, Read, Update, Delete)

Why SQLAlchemy ORM?
  - Write Python objects instead of raw SQL
  - Plain Python classes over a SQLite file
  - Type-safe, easy to maintain, industry standard

Table Overview:
  - leads         → Every scraped business (main table)
  - email_logs    → History of every email sent
  - call_logs     → History of every VAPI voice call
  - whatsapp_logs → History of every WhatsApp message
  - dnc_list      → Do-Not-Contact blacklist
  - scraping_jobs → Log of every scraping run
  - industry_profiles → Per-industry search, scoring rules and offer (data, not code)
  - lead_contacts → Every email/phone/WhatsApp/social contact of a lead
  - outreach_queue → Hot/Warm leads waiting to be contacted
  - outreach_messages → The text of each step (draft, approved, sent…)
  - inbound_messages → Replies and bounces picked up from email and WhatsApp
  - connection_status → Whether the WhatsApp extension and the email account are working

Usage:
    from database import Lead, LeadCRUD, SyncSessionLocal, init_db
"""

import enum
from datetime import datetime
from typing import Optional, Generator

from loguru import logger
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, Boolean,
    DateTime, Text, Enum as SAEnum, ForeignKey, Index, JSON, func
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from config import settings


# ─── SQLAlchemy Engine Setup ──────────────────────────────────────────────────

# check_same_thread=False lets the background dispatcher thread share the SQLite file
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,          # Set True to see raw SQL in terminal (debug mode)
    pool_pre_ping=True,  # Test connection before using it (avoids stale connections)
)

# SessionLocal is a factory — call it to get a new DB session
SyncSessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,   # We manually call db.commit()
    autoflush=False,    # We manually call db.flush() when needed
)


# ─── Base Class ───────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """All models inherit from this. SQLAlchemy uses it to track all tables."""
    pass


# ─── Enums (Fixed Choice Fields) ─────────────────────────────────────────────

class LeadSource(str, enum.Enum):
    """Where was this lead scraped from?"""
    GOOGLE_PLACES = "google_places"
    SERPAPI       = "serpapi"
    YELP          = "yelp"
    MANUAL        = "manual"


class LeadTier(str, enum.Enum):
    """Lead quality tier based on final score"""
    HOT      = "hot"      # Score 85-145
    WARM     = "warm"     # Score 40-84
    COLD     = "cold"     # Score 0-39
    UNSCORED = "unscored" # Not yet audited


class EmailStatus(str, enum.Enum):
    """Result of ZeroBounce email verification"""
    VALID     = "valid"      # Safe to send
    INVALID   = "invalid"    # Do not send — will bounce
    CATCH_ALL = "catch-all"  # Server accepts all emails (risky but possible)
    UNKNOWN   = "unknown"    # Could not determine


class PipelineStage(str, enum.Enum):
    """CRM pipeline — tracks where each lead is in the outreach process"""
    SCRAPED           = "scraped"
    LINKEDIN_ENRICHED = "linkedin_enriched"
    AUDITED           = "audited"
    EMAIL_VERIFIED    = "email_verified"
    EMAIL_SENT        = "email_sent"
    EMAIL_OPENED      = "email_opened"
    REPLIED           = "replied"
    CALL_PLACED       = "call_placed"
    WHATSAPP_SENT     = "whatsapp_sent"
    DEMO_BOOKED       = "demo_booked"
    ONBOARDED         = "onboarded"
    REJECTED          = "rejected"
    DNC               = "dnc"


class CallOutcome(str, enum.Enum):
    """Result of a VAPI voice call"""
    INTERESTED    = "interested"
    NOT_INTERESTED = "not_interested"
    CALLBACK      = "callback"
    NO_ANSWER     = "no_answer"
    WRONG_NUMBER  = "wrong_number"
    VOICEMAIL     = "voicemail"


# ─── Lead Model (Main Table) ──────────────────────────────────────────────────

class Lead(Base):
    """
    Main table — one row per scraped business.
    Every other table references this via lead_id (foreign key).
    """
    __tablename__ = "leads"

    # ── Primary Key ────────────────────────────────────────────────────────
    id = Column(Integer, primary_key=True, autoincrement=True)

    # ── Basic Info (from scraping) ─────────────────────────────────────────
    name         = Column(String(255), nullable=False)
    phone        = Column(String(30),  nullable=True)
    email        = Column(String(255), nullable=True)
    address      = Column(String(500), nullable=True)
    city         = Column(String(100), nullable=True)
    website      = Column(String(500), nullable=True)
    niche        = Column(String(100), nullable=True)

    # ── Source & IDs ───────────────────────────────────────────────────────
    source            = Column(SAEnum(LeadSource), default=LeadSource.GOOGLE_PLACES)
    google_place_id   = Column(String(200), nullable=True, unique=True)
    yelp_business_id  = Column(String(200), nullable=True)

    # ── Generic multi-industry data ────────────────────────────────────────
    # profile_id: which Industry Profile this lead was captured/scored under.
    # attributes: raw fields from the source (rating, category, CSV columns…).
    # signals:    the result of each of the profile's scoring rules.
    profile_id = Column(Integer, ForeignKey("industry_profiles.id"), nullable=True)
    country    = Column(String(100), nullable=True)
    timezone   = Column(String(64),  nullable=True)  # IANA name, e.g. "America/New_York"
    attributes = Column(JSON, nullable=True)
    signals    = Column(JSON, nullable=True)
    import_batch_id = Column(Integer, nullable=True)   # set when the lead came from the Import page (ImportBatch.id)

    # ── Ratings (from Google/Yelp) ─────────────────────────────────────────
    rating       = Column(Float,   default=0.0)
    review_count = Column(Integer, default=0)

    # ── Website Crawl Results ──────────────────────────────────────────────
    website_crawled          = Column(Boolean, default=False)
    crawled_text             = Column(Text,    nullable=True)  # Raw text from website pages
    dropship_mentioned       = Column(Boolean, default=False)  # Found "dropship" on website?
    processing_days_mentioned = Column(String(100), nullable=True)  # e.g. "1-2 business days"
    delivery_days_mentioned   = Column(String(100), nullable=True)  # e.g. "3-5 days"
    processing_fast          = Column(Boolean, default=False)  # Processing ≤ 2 days?
    delivery_fast            = Column(Boolean, default=False)  # Delivery ≤ 5 days?

    # ── Marketplace Detection ──────────────────────────────────────────────
    sells_on_amazon    = Column(Boolean, default=False)
    sells_on_etsy      = Column(Boolean, default=False)
    sells_on_shopify   = Column(Boolean, default=False)
    sells_on_marketplace = Column(Boolean, default=False)  # True if any of above
    marketplace_urls   = Column(Text, nullable=True)       # JSON list of found URLs

    # ── LinkedIn Enrichment ────────────────────────────────────────────────
    linkedin_found  = Column(Boolean, default=False)
    linkedin_name   = Column(String(255), nullable=True)  # Decision maker name
    linkedin_title  = Column(String(255), nullable=True)  # e.g. "Owner", "Operations Manager"
    linkedin_url    = Column(String(500), nullable=True)

    # ── Email Verification ─────────────────────────────────────────────────
    email_verified = Column(Boolean, default=False)
    email_status   = Column(SAEnum(EmailStatus), default=EmailStatus.UNKNOWN)

    # ── Scoring ────────────────────────────────────────────────────────────
    base_score   = Column(Integer, default=0)   # Max 100 (criteria-based)
    bonus_score  = Column(Integer, default=0)   # Can exceed 100 (LinkedIn, email, marketplace)
    final_score  = Column(Integer, default=0)   # base_score + bonus_score
    tier         = Column(SAEnum(LeadTier), default=LeadTier.UNSCORED)
    audit_summary = Column(Text, nullable=True) # Human-readable breakdown of score
    score_breakdown = Column(JSON, nullable=True)  # Same breakdown as data: one entry per scoring rule

    # True when the score could NOT fully check website-content signals
    # (dropship/processing/delivery — up to 70 pts) because there was no
    # website to crawl, or the crawl failed. Lets the UI warn "score may
    # be low because we couldn't check the site" instead of implying the
    # business itself failed those checks.
    score_incomplete        = Column(Boolean, default=False)
    score_incomplete_reason = Column(Text, nullable=True)

    # ── DNC / Blacklist ────────────────────────────────────────────────────
    do_not_contact = Column(Boolean, default=False)

    # ── CRM Pipeline ──────────────────────────────────────────────────────
    pipeline_stage = Column(SAEnum(PipelineStage), default=PipelineStage.SCRAPED)

    # ── Timestamps ────────────────────────────────────────────────────────
    scraped_at   = Column(DateTime, default=datetime.utcnow)
    audited_at   = Column(DateTime, nullable=True)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Relationships (links to other tables) ─────────────────────────────
    # One lead can have many emails, calls, WhatsApp messages
    email_logs     = relationship("EmailLog",     back_populates="lead", cascade="all, delete-orphan")
    call_logs      = relationship("CallLog",      back_populates="lead", cascade="all, delete-orphan")
    whatsapp_logs  = relationship("WhatsAppLog",  back_populates="lead", cascade="all, delete-orphan")
    contacts       = relationship("LeadContact",  back_populates="lead", cascade="all, delete-orphan")
    profile        = relationship("IndustryProfile", back_populates="leads")

    def __repr__(self):
        return f"<Lead id={self.id} name='{self.name}' score={self.final_score} tier={self.tier}>"


# ─── Industry Profile Model ───────────────────────────────────────────────────

class IndustryProfile(Base):
    """
    Everything that differs between industries, kept as data instead of code:
    search query, scoring rules, offer. The structure of `config` is defined
    and validated in profile_schema.py.
    """
    __tablename__ = "industry_profiles"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    name        = Column(String(120), nullable=False, unique=True)
    slug        = Column(String(140), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    target_type = Column(String(20), default="business")  # "business" | "person"
    is_active   = Column(Boolean, default=True)           # False = archived
    version     = Column(Integer, default=1)              # bumped on every edit
    config      = Column(JSON, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    leads = relationship("Lead", back_populates="profile")

    def __repr__(self):
        return f"<IndustryProfile id={self.id} name='{self.name}' v{self.version}>"


# ─── Outreach Queue Model ─────────────────────────────────────────────────────

class OutreachQueueEntry(Base):
    """
    A lead that qualified for outreach (Hot/Warm) and is waiting to be
    contacted. One row per lead; the schedule for its steps is added later.
    """
    __tablename__ = "outreach_queue"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    lead_id     = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, unique=True)
    profile_id  = Column(Integer, ForeignKey("industry_profiles.id"), nullable=True)
    status      = Column(String(20), default="queued")   # queued | paused | removed
    tier        = Column(String(20), nullable=True)      # tier when it entered the queue
    score       = Column(Integer, nullable=True)         # score when it entered the queue
    note        = Column(String(300), nullable=True)
    queued_at   = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ── Follow-up schedule (all times are UTC) ──
    sequence_status = Column(String(20), default="active")  # active | completed | stopped
    stop_reason     = Column(String(120), nullable=True)    # e.g. "replied", "do not contact"
    step_index      = Column(Integer, default=0)            # position in the profile's sequence
    next_step_key   = Column(String(40), nullable=True)
    next_channel    = Column(String(20), nullable=True)     # email | whatsapp | call
    next_due_at     = Column(DateTime, nullable=True)       # already moved inside working hours
    last_done_at    = Column(DateTime, nullable=True)       # when the previous step was done
    revisit_at      = Column(DateTime, nullable=True)       # a "not now" lead is contacted again then
    reopened_at     = Column(DateTime, nullable=True)       # replies before this no longer stop the sequence

    lead = relationship("Lead")

    def __repr__(self):
        return f"<OutreachQueueEntry lead={self.lead_id} status={self.status}>"


class OutreachStepLog(Base):
    """History of sequence steps: what was done (or skipped) for each lead, and when."""
    __tablename__ = "outreach_step_log"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    lead_id   = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    step_key  = Column(String(40), nullable=False)
    channel   = Column(String(20), nullable=False)
    status    = Column(String(10), nullable=False)       # done | skipped
    mode      = Column(String(10), nullable=True)        # manual | assisted | auto
    note      = Column(String(300), nullable=True)
    at        = Column(DateTime, default=datetime.utcnow)


# ─── Outreach Message Model ───────────────────────────────────────────────────

class OutreachMessage(Base):
    """
    The actual text for one step of one lead. It starts as a draft, is approved
    (by a person, or automatically for channels in Auto mode), is released at a
    planned time, and ends up sent or failed.
    """
    __tablename__ = "outreach_messages"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    lead_id     = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    step_key    = Column(String(40), nullable=False)
    channel     = Column(String(20), nullable=False)       # email | whatsapp
    subject     = Column(String(500), nullable=True)       # email only
    body        = Column(Text, nullable=False)
    status      = Column(String(12), default="draft")      # draft | approved | claimed | sent | failed | discarded
    mode        = Column(String(10), nullable=True)        # assisted | auto: how it was approved
    attempts    = Column(Integer, default=0)
    error       = Column(String(300), nullable=True)
    external_id = Column(String(300), nullable=True)       # email Message-ID / WhatsApp message id
    release_at  = Column(DateTime, nullable=True)          # planned send time (UTC)
    created_at  = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    claimed_at  = Column(DateTime, nullable=True)
    sent_at     = Column(DateTime, nullable=True)

    # ── Replies to a lead's message ──
    kind           = Column(String(10), default="step")          # step | reply
    in_reply_to_id = Column(Integer, ForeignKey("inbound_messages.id"), nullable=True)

    # ── Email open tracking ──
    tracking_token = Column(String(40), nullable=True, index=True)
    opened_at      = Column(DateTime, nullable=True)
    open_count     = Column(Integer, default=0)

    lead = relationship("Lead")
    inbound = relationship("InboundMessage", foreign_keys=[in_reply_to_id])

    def __repr__(self):
        return f"<OutreachMessage lead={self.lead_id} {self.channel}/{self.step_key} {self.status}>"


# ─── Inbound Message Model ────────────────────────────────────────────────────

class InboundMessage(Base):
    """Every reply or bounce picked up from the inbox or WhatsApp; `message_key` prevents counting one twice."""
    __tablename__ = "inbound_messages"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    message_key = Column(String(300), nullable=False, unique=True)
    channel     = Column(String(20), nullable=False)       # email | whatsapp
    kind        = Column(String(12), default="reply")      # reply | bounce
    lead_id     = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=True)
    sender      = Column(String(255), nullable=True)
    body        = Column(Text, nullable=True)
    received_at = Column(DateTime, default=datetime.utcnow)
    label       = Column(String(12), nullable=True)        # interested | question | not_now | stop | other
    summary     = Column(String(300), nullable=True)       # one line about what the lead said


# ─── Connection Status Model ──────────────────────────────────────────────────

class ConnectionStatus(Base):
    """Latest known state of an outside connection (the WhatsApp extension, the email account)."""
    __tablename__ = "connection_status"

    channel    = Column(String(20), primary_key=True)      # whatsapp | email
    state      = Column(String(20), nullable=True)         # connected | loading | logged_out | ok | error
    detail     = Column(JSON, nullable=True)
    last_seen  = Column(DateTime, nullable=True)           # last signal received
    last_ok    = Column(DateTime, nullable=True)           # last successful use
    last_error = Column(String(300), nullable=True)


# ─── Lead Contact Model ───────────────────────────────────────────────────────

class LeadContact(Base):
    """
    One row per way of reaching a lead (several emails, phones, a WhatsApp
    number, social links). Lead.email / Lead.phone stay as the primary contact.
    """
    __tablename__ = "lead_contacts"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    lead_id     = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False)
    kind        = Column(String(20), nullable=False)   # email | phone | whatsapp | website | social
    value       = Column(String(500), nullable=False)
    label       = Column(String(100), nullable=True)   # e.g. "owner", "info@"
    is_primary  = Column(Boolean, default=False)
    verified    = Column(Boolean, default=False)
    status      = Column(String(30), nullable=True)    # e.g. valid | invalid | unknown
    source      = Column(String(50), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="contacts")

    def __repr__(self):
        return f"<LeadContact lead={self.lead_id} {self.kind}={self.value!r}>"


# ─── Email Log Model ──────────────────────────────────────────────────────────

class EmailLog(Base):
    """
    Tracks every email sent via Instantly.
    One lead can receive multiple emails (follow-ups).
    """
    __tablename__ = "email_logs"

    id                 = Column(Integer, primary_key=True, autoincrement=True)
    lead_id            = Column(Integer, ForeignKey("leads.id"), nullable=False)

    campaign_id        = Column(String(200), nullable=True)   # Instantly campaign ID
    instantly_lead_id  = Column(String(200), nullable=True)   # Instantly's internal lead ID
    subject            = Column(String(500), nullable=True)
    body               = Column(Text,        nullable=True)
    personalized_for   = Column(String(255), nullable=True)   # Who was it addressed to?

    # ── Tracking (updated via Instantly webhook) ───────────────────────────
    email_opened       = Column(Boolean, default=False)
    opened_at          = Column(DateTime, nullable=True)
    replied            = Column(Boolean, default=False)
    replied_at         = Column(DateTime, nullable=True)
    reply_content      = Column(Text,    nullable=True)
    unsubscribed       = Column(Boolean, default=False)
    is_read            = Column(Boolean, default=True)   # False = new reply, not yet reviewed by admin

    sent_at            = Column(DateTime, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="email_logs")

    def __repr__(self):
        return f"<EmailLog lead_id={self.lead_id} sent_at={self.sent_at}>"


# ─── Call Log Model ───────────────────────────────────────────────────────────

class CallLog(Base):
    """
    Tracks every VAPI voice call made to a lead.
    """
    __tablename__ = "call_logs"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    lead_id      = Column(Integer, ForeignKey("leads.id"), nullable=False)

    vapi_call_id = Column(String(200), nullable=True)   # VAPI's call ID for reference
    called_at    = Column(DateTime, default=datetime.utcnow)
    duration_sec = Column(Integer, nullable=True)        # Call duration in seconds
    outcome      = Column(SAEnum(CallOutcome), nullable=True)
    notes        = Column(Text, nullable=True)           # AI-generated call summary
    recording_url = Column(String(500), nullable=True)  # Link to call recording
    is_read      = Column(Boolean, default=True)   # False = new inbound callback, not yet reviewed

    lead = relationship("Lead", back_populates="call_logs")

    def __repr__(self):
        return f"<CallLog lead_id={self.lead_id} outcome={self.outcome}>"


# ─── WhatsApp Log Model ───────────────────────────────────────────────────────

class WhatsAppLog(Base):
    """
    Tracks every WhatsApp message sent via Twilio.
    """
    __tablename__ = "whatsapp_logs"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    lead_id          = Column(Integer, ForeignKey("leads.id"), nullable=False)

    twilio_message_id = Column(String(200), nullable=True)
    message_content   = Column(Text,        nullable=True)
    delivery_status   = Column(String(50),  default="sent")  # sent/delivered/read/failed
    replied           = Column(Boolean,     default=False)
    reply_content     = Column(Text,        nullable=True)

    sent_at  = Column(DateTime, default=datetime.utcnow)
    replied_at = Column(DateTime, nullable=True)
    is_read  = Column(Boolean, default=True)   # False = new reply, not yet reviewed by admin

    lead = relationship("Lead", back_populates="whatsapp_logs")

    def __repr__(self):
        return f"<WhatsAppLog lead_id={self.lead_id} status={self.delivery_status}>"


# ─── DNC List Model ───────────────────────────────────────────────────────────

class DNCList(Base):
    """
    Do-Not-Contact blacklist.
    Before any outreach, always check this table first.
    Match by phone, email, OR domain.
    """
    __tablename__ = "dnc_list"

    id       = Column(Integer, primary_key=True, autoincrement=True)
    phone    = Column(String(30),  nullable=True, index=True)
    email    = Column(String(255), nullable=True, index=True)
    domain   = Column(String(255), nullable=True, index=True)  # e.g. "example.com"
    reason   = Column(String(500), nullable=True)              # Why was it added?
    added_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<DNCList email={self.email} phone={self.phone}>"


# ─── Scraping Job Log ─────────────────────────────────────────────────────────

class ScrapingJob(Base):
    """
    Logs every time script_01_scraper.py is run.
    Useful for tracking campaign history.
    """
    __tablename__ = "scraping_jobs"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    niche          = Column(String(100), nullable=False)
    city           = Column(String(100), nullable=False)
    min_rating     = Column(Float,   default=3.5)
    min_reviews    = Column(Integer, default=10)
    status         = Column(String(50), default="running")  # running/done/failed
    total_google   = Column(Integer, default=0)
    total_serpapi  = Column(Integer, default=0)
    total_yelp     = Column(Integer, default=0)
    total_scraped  = Column(Integer, default=0)   # After deduplication
    # ── Skip-reason breakdown for the post-scrape summary ──────────────────
    total_raw_matches         = Column(Integer, default=0)  # matched query, before rating/review filter
    skipped_low_rating        = Column(Integer, default=0)  # below min_rating/min_reviews threshold
    skipped_duplicate_batch   = Column(Integer, default=0)  # duplicate within this scrape's own results
    skipped_duplicate_existing = Column(Integer, default=0) # already existed in DB from an earlier scrape
    skipped_over_cap           = Column(Integer, default=0) # cut off by the "Max Leads Per Scrape" setting
    started_at     = Column(DateTime, default=datetime.utcnow)
    completed_at   = Column(DateTime, nullable=True)
    import_batch_id = Column(Integer, nullable=True)      # the ImportBatch that holds the leads this scrape saved

    def __repr__(self):
        return f"<ScrapingJob niche={self.niche} city={self.city} status={self.status}>"


# ─── Import Batch ─────────────────────────────────────────────────────────────

class ImportBatch(Base):
    """
    One use of the Import page (a file or pasted text). Lets a whole import be found again,
    counted, and undone while its leads have not been contacted.
    """
    __tablename__ = "import_batches"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    name             = Column(String(200), nullable=False)
    source           = Column(String(300), nullable=True)      # "file: leads.csv" or "pasted text"
    default_industry = Column(String(60),  nullable=True)
    total_rows       = Column(Integer, default=0)
    imported         = Column(Integer, default=0)
    duplicates       = Column(Integer, default=0)
    on_dnc           = Column(Integer, default=0)
    invalid          = Column(Integer, default=0)
    ai_used          = Column(Boolean, default=False)
    created_at       = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ImportBatch id={self.id} name={self.name!r} imported={self.imported}>"


# ─── Indexes for Performance ──────────────────────────────────────────────────
# Indexes speed up queries on columns we filter/search by frequently

Index("ix_leads_niche_city",    Lead.niche,         Lead.city)
Index("ix_leads_score_tier",    Lead.final_score,   Lead.tier)
Index("ix_leads_pipeline",      Lead.pipeline_stage)
Index("ix_leads_email",         Lead.email)
Index("ix_leads_do_not_contact", Lead.do_not_contact)
Index("ix_leads_profile",       Lead.profile_id)
Index("ix_leads_import_batch",  Lead.import_batch_id)
Index("ix_queue_status",        OutreachQueueEntry.status)
Index("ix_messages_lead_step",  OutreachMessage.lead_id, OutreachMessage.step_key)
Index("ix_messages_status",     OutreachMessage.channel, OutreachMessage.status, OutreachMessage.release_at)
Index("ix_queue_due",           OutreachQueueEntry.sequence_status, OutreachQueueEntry.next_due_at)
Index("ix_step_log_channel_at", OutreachStepLog.channel, OutreachStepLog.at)
Index("ix_lead_contacts_lead_kind", LeadContact.lead_id, LeadContact.kind)
Index("ix_lead_contacts_value",     LeadContact.value)


# ─── Database Initialization ──────────────────────────────────────────────────

# Columns added after a table was first created. (column name, SQL type and default)
_ADDED_COLUMNS = {
    "leads": [
        ("score_incomplete",        "BOOLEAN DEFAULT 0"),
        ("score_incomplete_reason", "TEXT"),
        ("profile_id",              "INTEGER REFERENCES industry_profiles(id)"),
        ("country",                 "VARCHAR(100)"),
        ("timezone",                "VARCHAR(64)"),
        ("attributes",              "JSON"),
        ("signals",                 "JSON"),
        ("score_breakdown",         "JSON"),
        ("import_batch_id",         "INTEGER"),
    ],
    "scraping_jobs": [
        ("import_batch_id",            "INTEGER"),
        ("total_raw_matches",          "INTEGER DEFAULT 0"),
        ("skipped_low_rating",         "INTEGER DEFAULT 0"),
        ("skipped_duplicate_batch",    "INTEGER DEFAULT 0"),
        ("skipped_duplicate_existing", "INTEGER DEFAULT 0"),
        ("skipped_over_cap",           "INTEGER DEFAULT 0"),
    ],
    "outreach_messages": [
        ("kind",           "VARCHAR(10) DEFAULT 'step'"),
        ("in_reply_to_id", "INTEGER REFERENCES inbound_messages(id)"),
        ("tracking_token", "VARCHAR(40)"),
        ("opened_at",      "DATETIME"),
        ("open_count",     "INTEGER DEFAULT 0"),
    ],
    "inbound_messages": [
        ("label",   "VARCHAR(12)"),
        ("summary", "VARCHAR(300)"),
    ],
    "outreach_queue": [
        ("revisit_at",  "DATETIME"),
        ("reopened_at", "DATETIME"),
    ],
}


def _add_missing_columns() -> None:
    """
    `Base.metadata.create_all()` only creates tables that do not exist yet; it never adds
    a new column to a table that is already there. Any column added to a model after the
    database was created is listed in _ADDED_COLUMNS and added here.

    Safe to call on every start: only the columns that are actually missing are added.
    """
    from sqlalchemy import inspect, text

    if not settings.DATABASE_URL.startswith("sqlite"):
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in tables:
                continue                      # created fresh by create_all with every column
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, definition in columns:
                if name not in existing:
                    logger.info(f"Migrating {table}: adding missing column '{name}'")
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def init_db() -> None:
    """
    Creates missing tables and adds missing columns. Safe to call on every start.
    """
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()

    # Seed the default Industry Profile and attach existing leads to it.
    from profile_service import bootstrap_profiles
    with SyncSessionLocal() as session:
        bootstrap_profiles(session)

    # Log the URL without the password.
    logger.info(f"Database initialized: {engine.url.render_as_string(hide_password=True)}")


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency — yields a DB session per request, closes it after.
    
    Usage in FastAPI:
        @app.get("/leads")
        def get_leads(db: Session = Depends(get_db)):
            return db.query(Lead).all()
    """
    db = SyncSessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── CRUD Helper Classes ──────────────────────────────────────────────────────

class LeadCRUD:
    """
    All database operations related to the Lead table.
    Keeps DB logic separate from business logic.
    """

    @staticmethod
    def check_duplicate(db: Session, name: str, phone: Optional[str]) -> Optional[Lead]:
        """
        Check if a lead already exists by name + phone.
        Prevents saving the same business twice from different sources.
        """
        query = db.query(Lead).filter(Lead.name == name)
        if phone:
            query = query.filter(Lead.phone == phone)
        return query.first()

    @staticmethod
    def get_by_id(db: Session, lead_id: int) -> Optional[Lead]:
        """Fetch a single lead by its ID."""
        return db.query(Lead).filter(Lead.id == lead_id).first()

    @staticmethod
    def get_all(db: Session, skip: int = 0, limit: int = 100) -> list[Lead]:
        """Fetch all leads with pagination. skip=offset, limit=page size."""
        return db.query(Lead).offset(skip).limit(limit).all()

    @staticmethod
    def get_by_tier(db: Session, tier: LeadTier) -> list[Lead]:
        """Fetch all leads of a specific tier (hot/warm/cold)."""
        return db.query(Lead).filter(Lead.tier == tier).order_by(Lead.final_score.desc()).all()

    @staticmethod
    def get_eligible_for_email(db: Session, min_score: int = 40) -> list[Lead]:
        """
        Returns leads that are ready for email outreach:
          - Score above threshold
          - Email is verified
          - Not on DNC list
          - Has an email address
        """
        return (
            db.query(Lead)
            .filter(
                Lead.final_score >= min_score,
                Lead.email_verified == True,
                Lead.do_not_contact == False,
                Lead.email != None,
            )
            .order_by(Lead.final_score.desc())
            .all()
        )

    @staticmethod
    def get_hot_leads(db: Session) -> list[Lead]:
        """Returns all Hot leads (score >= HOT threshold, live from Settings page)."""
        from app_settings import get_score_thresholds
        hot_threshold, _ = get_score_thresholds()
        return (
            db.query(Lead)
            .filter(
                Lead.final_score >= hot_threshold,
                Lead.do_not_contact == False,
            )
            .order_by(Lead.final_score.desc())
            .all()
        )

    @staticmethod
    def update_score(db: Session, lead_id: int, base: int, bonus: int) -> Optional[Lead]:
        """
        Update a lead's score and recalculate tier.
        Called by script_03_auditor.py after scoring.
        """
        from app_settings import get_score_thresholds
        hot_threshold, warm_threshold = get_score_thresholds()
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return None

        lead.base_score  = base
        lead.bonus_score = bonus
        lead.final_score = base + bonus

        # Assign tier based on final score (uses live Settings → Lead Scoring thresholds)
        if lead.final_score >= hot_threshold:
            lead.tier = LeadTier.HOT
        elif lead.final_score >= warm_threshold:
            lead.tier = LeadTier.WARM
        else:
            lead.tier = LeadTier.COLD

        db.commit()
        db.refresh(lead)
        return lead

    @staticmethod
    def update_pipeline_stage(db: Session, lead_id: int, stage: PipelineStage) -> None:
        """Move a lead to the next CRM pipeline stage."""
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.pipeline_stage = stage
            lead.updated_at = datetime.utcnow()
            db.commit()

    @staticmethod
    def mark_do_not_contact(db: Session, lead_id: int) -> None:
        """Flag a lead as DNC — blocks all future outreach."""
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if lead:
            lead.do_not_contact = True
            lead.pipeline_stage = PipelineStage.DNC
            lead.final_score    = 0
            lead.tier           = LeadTier.COLD
            db.commit()

    @staticmethod
    def search(db: Session, niche: str = None, city: str = None,
               tier: LeadTier = None, min_score: int = None,
               q: str = None, pipeline_stage: PipelineStage = None) -> list[Lead]:
        """
        Flexible search with optional filters.
        Used by the React frontend to filter the leads table.

        Args:
            q: free-text search across name, email, and phone
               (case-insensitive substring match)
            pipeline_stage: filter by CRM pipeline stage (e.g. "audited", "email_sent")
        """
        query = db.query(Lead)
        if niche:
            query = query.filter(Lead.niche.ilike(f"%{niche}%"))
        if city:
            query = query.filter(Lead.city.ilike(f"%{city}%"))
        if tier:
            query = query.filter(Lead.tier == tier)
        if min_score is not None:
            query = query.filter(Lead.final_score >= min_score)
        if pipeline_stage:
            query = query.filter(Lead.pipeline_stage == pipeline_stage)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (Lead.name.ilike(like)) |
                (Lead.email.ilike(like)) |
                (Lead.phone.ilike(like))
            )
        return query.order_by(Lead.final_score.desc()).all()

    @staticmethod
    def search_paginated(db: Session, niche: str = None, city: str = None,
                          tier: LeadTier = None, min_score: int = None,
                          q: str = None, pipeline_stage: PipelineStage = None,
                          year: int = None, month: int = None,
                          stuck_only: bool = False, batch_id: int = None,
                          skip: int = 0, limit: int = 100) -> tuple[list[Lead], int]:
        """
        Same filters as search(), but does pagination at the SQL level
        (LIMIT/OFFSET) instead of loading every matching row into Python.
        Returns (leads_for_this_page, total_matching_count) — the total
        is used by the frontend to render "X of Y leads" and page numbers.

        `year` / `month` filter by `scraped_at`: pass `year` alone for a
        full calendar year, or `year` + `month` together for one month.

        `stuck_only`: "Stuck Leads" filter — leads with genuinely no
        outreach path available: no email AND no phone, or on the
        Do-Not-Contact list. (A phone-only lead is NOT stuck — Smart
        First-Touch Routing already reaches those via SMS/call.)
        """
        query = db.query(Lead)
        if batch_id is not None:
            query = query.filter(Lead.import_batch_id == batch_id)
        if niche:
            query = query.filter(Lead.niche.ilike(f"%{niche}%"))
        if city:
            query = query.filter(Lead.city.ilike(f"%{city}%"))
        if tier:
            query = query.filter(Lead.tier == tier)
        if min_score is not None:
            query = query.filter(Lead.final_score >= min_score)
        if pipeline_stage:
            query = query.filter(Lead.pipeline_stage == pipeline_stage)
        if q:
            like = f"%{q}%"
            query = query.filter(
                (Lead.name.ilike(like)) |
                (Lead.email.ilike(like)) |
                (Lead.phone.ilike(like))
            )
        if year is not None:
            if month is not None:
                since = datetime(year, month, 1)
                until = datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
            else:
                since = datetime(year, 1, 1)
                until = datetime(year + 1, 1, 1)
            query = query.filter(Lead.scraped_at >= since, Lead.scraped_at < until)
        if stuck_only:
            no_email = (Lead.email == None) | (Lead.email == "")
            no_phone = (Lead.phone == None) | (Lead.phone == "")
            query = query.filter(
                ((no_email) & (no_phone)) | (Lead.do_not_contact == True)
            )

        total = query.count()
        leads = (
            query.order_by(func.coalesce(Lead.import_batch_id, 0).desc(), Lead.id.asc())   # newest import first, in the order pasted
            .offset(skip)
            .limit(limit)
            .all()
        )
        return leads, total


class DNCCrud:
    """All database operations for the DNC (Do-Not-Contact) list."""

    @staticmethod
    def is_on_dnc(db: Session, email: str = None,
                  phone: str = None, domain: str = None) -> bool:
        """
        Check if a lead matches any DNC entry.
        Returns True if ANY of email/phone/domain matches.
        """
        query = db.query(DNCList)
        conditions = []
        if email:
            conditions.append(DNCList.email == email)
        if phone:
            conditions.append(DNCList.phone == phone)
        if domain:
            conditions.append(DNCList.domain == domain)

        if not conditions:
            return False

        from sqlalchemy import or_
        return query.filter(or_(*conditions)).first() is not None

    @staticmethod
    def add_to_dnc(db: Session, email: str = None,
                   phone: str = None, domain: str = None,
                   reason: str = "manual") -> DNCList:
        """Add a new entry to the DNC list."""
        entry = DNCList(email=email, phone=phone, domain=domain, reason=reason)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        logger.info(f"Added to DNC: email={email} phone={phone} reason={reason}")
        return entry

    @staticmethod
    def get_all(db: Session) -> list[DNCList]:
        """Return all DNC entries."""
        return db.query(DNCList).order_by(DNCList.added_at.desc()).all()