"""Test setup: run against a throwaway in-memory SQLite database, never the real one."""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite://"  # set before config/database are imported

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy.pool import StaticPool

import database


@pytest.fixture()
def db():
    """A clean in-memory database for one test."""
    # One shared in-memory connection so every session sees the same tables.
    engine = database.create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    database.Base.metadata.create_all(bind=engine)
    session = database.sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def seeded_db(db):
    """A database that already has the default Industry Profile, as after app start."""
    from profile_service import bootstrap_profiles
    bootstrap_profiles(db)
    return db


@pytest.fixture(autouse=True)
def fixed_settings(monkeypatch):
    """Tests must not depend on the real Settings page values."""
    import queue_service
    import scoring_engine
    monkeypatch.setattr(scoring_engine, "get_score_thresholds", lambda: (70, 40))
    monkeypatch.setattr(queue_service, "get_app_settings", lambda: {})


@pytest.fixture(autouse=True)
def schedule_store(monkeypatch):
    """Sending rules are kept in memory during tests; the real settings file is never written."""
    import scheduling
    store: dict = {}
    monkeypatch.setattr(scheduling, "get_app_settings", lambda: dict(store))
    monkeypatch.setattr(scheduling, "save_app_settings", lambda data: store.update(data))
    return store


@pytest.fixture()
def clock(monkeypatch):
    """Freeze "now" (naive UTC) for everything that plans, lists or sends. Returns a setter."""
    from datetime import datetime

    import queue_service
    import scheduling
    state = {"now": datetime(2026, 10, 12, 14, 0)}      # Monday 10:00 in New York
    monkeypatch.setattr(scheduling, "utcnow", lambda: state["now"])
    monkeypatch.setattr(queue_service, "utcnow", lambda: state["now"])

    def set_now(dt):
        state["now"] = dt
    return set_now
