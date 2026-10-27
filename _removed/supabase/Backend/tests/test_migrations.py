"""The PostgreSQL migrations match the models, and the SQLite -> Supabase copy works."""
import os

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

import database
from database import (
    Base, EmailLog, InboundMessage, IndustryProfile, Lead, LeadTier, OutreachMessage,
    OutreachQueueEntry, PipelineStage,
)
from scripts.copy_sqlite_to_postgres import copy_database

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def alembic_config(connection) -> Config:
    config = Config(os.path.join(BACKEND, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture()
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'migrate.db'}")
    yield eng
    eng.dispose()


def test_migrations_build_exactly_the_tables_the_models_describe(engine):
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "head")

    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables)

    # If this fails, a model was changed without a migration: run
    #   alembic revision --autogenerate -m "what changed"
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


def test_migrations_can_be_undone_and_redone(engine):
    with engine.begin() as connection:
        config = alembic_config(connection)
        command.upgrade(config, "head")
        command.downgrade(config, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "head")
    assert len(inspect(engine).get_table_names()) > 10


# ─── Copying the data ─────────────────────────────────────────────────────────

def make_source(tmp_path):
    source = create_engine(f"sqlite:///{tmp_path / 'source.db'}")
    Base.metadata.create_all(bind=source)
    with Session(source) as db:
        profile = IndustryProfile(id=1, name="Dropship Suppliers", slug="dropship-suppliers", config={"search": {}})
        lead = Lead(id=7, name="Cool Air", email="a@b.com", tier=LeadTier.HOT, final_score=90,
                    pipeline_stage=PipelineStage.EMAIL_SENT, profile_id=1, signals={"emergency": True},
                    score_breakdown={"rules": [{"key": "x"}]})
        db.add_all([profile, lead])
        db.flush()
        db.add_all([
            EmailLog(lead_id=7, subject="Hi", replied=True),
            OutreachQueueEntry(lead_id=7, profile_id=1, status="queued", tier="hot", score=90),
            InboundMessage(id=3, message_key="<k1>", channel="email", lead_id=7, body="yes", label="interested"),
        ])
        db.flush()
        db.add(OutreachMessage(lead_id=7, step_key="reply_3", channel="email", body="Thanks", status="draft",
                               kind="reply", in_reply_to_id=3))
        db.commit()
    source.dispose()
    return f"sqlite:///{tmp_path / 'source.db'}"


def test_the_copy_moves_every_table_and_keeps_ids_and_values(tmp_path):
    source_url = make_source(tmp_path)
    target = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    Base.metadata.create_all(bind=target)
    with Session(target) as db:      # what start-up seeds before the copy
        db.add(IndustryProfile(id=1, name="Dropship Suppliers", slug="dropship-suppliers", config={}))
        db.commit()

    copied = copy_database(source_url, target, log=lambda line: None)
    assert copied["leads"] == 1 and copied["outreach_messages"] == 1 and copied["industry_profiles"] == 1

    with Session(target) as db:
        lead = db.get(Lead, 7)
        assert (lead.name, lead.tier, lead.pipeline_stage) == ("Cool Air", LeadTier.HOT, PipelineStage.EMAIL_SENT)
        assert lead.signals == {"emergency": True} and lead.score_breakdown["rules"][0]["key"] == "x"
        assert db.query(EmailLog).one().replied is True
        message = db.query(OutreachMessage).one()
        assert message.in_reply_to_id == 3 and message.kind == "reply"
        assert db.query(IndustryProfile).one().config == {"search": {}}      # the copied profile replaced the seeded one


def test_the_copy_refuses_a_target_that_already_has_leads_unless_merging(tmp_path):
    source_url = make_source(tmp_path)
    target = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    Base.metadata.create_all(bind=target)
    copy_database(source_url, target, log=lambda line: None)

    with pytest.raises(ValueError, match="already contains leads"):
        copy_database(source_url, target, log=lambda line: None)

    copy_database(source_url, target, merge=True, log=lambda line: None)           # idempotent
    with Session(target) as db:
        assert db.query(Lead).count() == 1 and db.query(OutreachMessage).count() == 1


def test_a_wrong_source_is_explained(tmp_path):
    empty = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    empty.connect().close()
    target = create_engine(f"sqlite:///{tmp_path / 'target.db'}")
    with pytest.raises(ValueError, match="no leads table"):
        copy_database(f"sqlite:///{tmp_path / 'empty.db'}", target, log=lambda line: None)


def test_postgres_connection_settings():
    """SSL is required for a remote Postgres (Supabase) but not for a local one; pools stay small."""
    remote = "postgresql://postgres.abc:pw@aws-0-us-east-1.pooler.supabase.com:5432/postgres"

    url, args, pool = database.engine_settings(remote)
    assert url == remote and args == {"sslmode": "require"} and pool["pool_size"] == 5

    assert database.engine_settings(remote.replace("postgresql://", "postgres://"))[0] == remote   # old-style scheme
    assert database.engine_settings("postgresql://u:p@localhost:5432/db")[1] == {}
    assert database.engine_settings("postgresql://u:p@127.0.0.1:5432/db")[1] == {}
    assert database.engine_settings(remote + "?sslmode=disable")[1] == {}                           # respected if set

    url, args, pool = database.engine_settings("sqlite:///./data/leads.db")
    assert args == {"check_same_thread": False} and pool == {}


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs a PostgreSQL TEST_DATABASE_URL")
def test_every_table_has_row_level_security_on_postgres():
    """Supabase exposes public tables through its web API; row level security keeps them closed."""
    from sqlalchemy import text

    url = os.environ["TEST_DATABASE_URL"]
    eng = create_engine(url)
    Base.metadata.drop_all(bind=eng)
    with eng.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        command.upgrade(alembic_config(connection), "head")
    with eng.connect() as connection:
        rows = connection.execute(text(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> 'alembic_version'")).all()
    assert {name for name, _ in rows} == set(Base.metadata.tables)
    assert [name for name, secure in rows if not secure] == []
    eng.dispose()
