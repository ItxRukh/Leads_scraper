import pytest

from database import Lead, DNCCrud
from scripts.auditor import calculate_score


@pytest.fixture()
def db(seeded_db):
    # These tests score with the default (original dropship) profile
    return seeded_db


def make_lead(**kw):
    base = dict(
        name="Test Co", email="owner@testco.com", website="https://testco.com",
        website_crawled=True, rating=4.5, review_count=50,
    )
    base.update(kw)
    return Lead(**base)


def test_perfect_base_score(db):
    lead = make_lead(dropship_mentioned=True, processing_fast=True, delivery_fast=True)
    base, bonus, _, incomplete, _ = calculate_score(lead, db)
    assert base == 100
    assert bonus == 0
    assert incomplete is False


def test_missing_website_marks_score_incomplete(db):
    lead = make_lead(website=None, website_crawled=False)
    _, _, _, incomplete, reason = calculate_score(lead, db)
    assert incomplete is True
    assert reason


def test_bonus_points(db):
    lead = make_lead(linkedin_found=True, email_verified=True, sells_on_marketplace=True)
    _, bonus, _, _, _ = calculate_score(lead, db)
    assert bonus == 15 + 10 + 20


def test_dnc_lead_scores_zero(db):
    DNCCrud.add_to_dnc(db, email="owner@testco.com", reason="test")
    db.commit()
    lead = make_lead(dropship_mentioned=True)
    base, bonus, summary, _, _ = calculate_score(lead, db)
    assert (base, bonus) == (0, 0)
    assert "DNC" in summary
