import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

import profile_service as svc
from database import IndustryProfile, Lead
from profile_schema import ProfileConfig, ProfileIn, ScoringRule


# ─── Schema validation ────────────────────────────────────────────────────────

def rule(**kw):
    base = dict(key="r1", label="Rule", kind="field", points=5,
                params={"field": "rating", "op": ">=", "value": 4})
    base.update(kw)
    return base


def test_valid_rule_passes():
    assert ScoringRule.model_validate(rule()).points == 5


@pytest.mark.parametrize("bad", [
    rule(params={"field": "rating", "op": "~"}),                       # unknown operator
    rule(kind="keyword", params={"any": []}),                          # no keywords
    rule(kind="regex", params={"pattern": "("}),                       # broken regex
    rule(kind="ai", params={}),                                        # no question
    rule(kind="builtin", params={"name": "nope"}),                     # unknown detector
    rule(kind="intent", params={"event": "nope"}),                     # unknown event
    rule(key="Bad Key"),                                               # key format
    rule(points=500),                                                  # out of range
    rule(requires=["fax"]),                                            # unknown requirement
])
def test_invalid_rules_rejected(bad):
    with pytest.raises(ValidationError):
        ScoringRule.model_validate(bad)


def test_duplicate_keys_and_threshold_order_rejected():
    with pytest.raises(ValidationError):
        ProfileConfig.model_validate({"scoring": {"rules": [rule(), rule()]}})
    with pytest.raises(ValidationError):
        ProfileConfig.model_validate({"scoring": {"hot_threshold": 40, "warm_threshold": 70}})


def test_query_template_needs_a_placeholder():
    with pytest.raises(ValidationError):
        ProfileConfig.model_validate({"search": {"query_template": "plumbers"}})


# ─── Default profile and migration ────────────────────────────────────────────

def test_default_profile_matches_original_scoring_points():
    cfg = svc._default_profile_config()
    base = sum(r.points for r in cfg.scoring.rules if r.group == "base")
    bonus = sum(r.points for r in cfg.scoring.rules if r.group == "bonus")
    assert base == 100          # the original base score maximum
    assert bonus == 45          # 15 LinkedIn + 10 email verified + 20 marketplace


def test_bootstrap_seeds_once_and_migrates_existing_leads(db):
    db.add(Lead(name="Old Lead", dropship_mentioned=True, processing_fast=True,
                sells_on_amazon=True, sells_on_marketplace=True))
    db.commit()

    svc.bootstrap_profiles(db)
    svc.bootstrap_profiles(db)  # second run must change nothing

    profiles = db.query(IndustryProfile).all()
    assert len(profiles) == 1 and profiles[0].slug == svc.DEFAULT_PROFILE_SLUG

    lead = db.query(Lead).one()
    assert lead.profile_id == profiles[0].id
    assert lead.signals["dropship_mentioned"] is True
    assert lead.signals["sells_on_amazon"] is True
    assert lead.signals["delivery_fast"] is False


# ─── Service ──────────────────────────────────────────────────────────────────

def new_profile(db, name="HVAC"):
    return svc.create_profile(db, ProfileIn(name=name, config={"search": {"query_template": "HVAC {city}"}}))


def test_create_edit_duplicate_archive(db):
    p = new_profile(db)
    assert p.slug == "hvac" and p.version == 1

    edited = svc.update_profile(db, p.id, ProfileIn(name="HVAC US", config={}))
    assert edited.version == 2 and edited.name == "HVAC US"

    copy = svc.duplicate_profile(db, p.id)
    assert copy.id != p.id and copy.name == "HVAC US (copy)"

    assert svc.set_archived(db, p.id, True).is_active is False
    assert [x.id for x in svc.list_profiles(db)] == [copy.id]
    assert len(svc.list_profiles(db, include_archived=True)) == 2


def test_duplicate_names_conflict(db):
    new_profile(db, "HVAC")
    with pytest.raises(svc.ProfileConflict):
        new_profile(db, "hvac")


def test_export_import_round_trip(db):
    p = new_profile(db)
    data = svc.export_profile(db, p.id)
    imported = svc.import_profile(db, data)
    assert imported.id != p.id
    assert imported.name != p.name           # never overwrites
    assert imported.config == p.config
    with pytest.raises(ValueError):
        svc.import_profile(db, {"format": "something-else"})


# ─── API ──────────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(db):
    import profiles_api
    from database import get_db

    app = FastAPI()
    app.include_router(profiles_api.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_api_flow(client):
    created = client.post("/profiles", json={"name": "Real Estate", "config": {}})
    assert created.status_code == 201
    pid = created.json()["id"]

    assert client.get(f"/profiles/{pid}").json()["name"] == "Real Estate"
    assert client.post("/profiles", json={"name": "Real Estate"}).status_code == 409
    assert client.get("/profiles/9999").status_code == 404

    bad = {"name": "Bad", "config": {"scoring": {"rules": [rule(kind="ai", params={})]}}}
    assert client.post("/profiles", json=bad).status_code == 422

    exported = client.get(f"/profiles/{pid}/export").json()
    assert client.post("/profiles/import", json=exported).status_code == 201

    assert client.post(f"/profiles/{pid}/archive").json()["is_active"] is False
    assert pid not in [p["id"] for p in client.get("/profiles").json()]
    assert client.post(f"/profiles/{pid}/restore").json()["is_active"] is True
