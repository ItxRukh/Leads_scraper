"""
The default "Dropship Suppliers" profile must score every lead exactly like
the original hardcoded scoring did. The reference below is that original
formula, copied from scripts/auditor.py as it was before profiles existed.
"""
import itertools

import pytest

from database import Lead, LeadTier
from scoring_engine import score_lead


@pytest.fixture()
def db(seeded_db):
    return seeded_db


def legacy_score(lead):
    """(base, bonus, incomplete) exactly as the pre-profile auditor computed them."""
    base = 0
    if lead.dropship_mentioned: base += 50
    if lead.processing_fast:    base += 10
    if lead.delivery_fast:      base += 10
    if lead.rating >= 4.0:      base += 10
    if lead.review_count >= 20: base += 10
    if lead.website and lead.email: base += 10
    elif lead.website or lead.email: base += 5

    bonus = 0
    if lead.linkedin_found:        bonus += 15
    if lead.email_verified:        bonus += 10
    if lead.sells_on_marketplace:  bonus += 20

    incomplete = (not lead.website) or (bool(lead.website) and not lead.website_crawled)
    return base, bonus, incomplete


def legacy_tier(final, hot=70, warm=40):
    return LeadTier.HOT if final >= hot else LeadTier.WARM if final >= warm else LeadTier.COLD


def test_engine_matches_original_scoring_for_every_combination(db):
    # website state: none / present but not crawled / present and crawled
    website_states = [(None, False), ("https://x.com", False), ("https://x.com", True)]
    combos = itertools.product(
        [False, True],            # dropship_mentioned
        [False, True],            # processing_fast
        [False, True],            # delivery_fast
        [3.9, 4.0, 4.8],          # rating
        [19, 20],                 # review_count
        website_states,
        [None, "a@x.com"],        # email
        [False, True],            # linkedin_found
        [False, True],            # email_verified
        [False, True],            # sells_on_marketplace
    )
    checked = 0
    for ds, pf, df, rating, reviews, (site, crawled), email, li, ev, mk in combos:
        lead = Lead(
            name="L", rating=rating, review_count=reviews, website=site,
            website_crawled=crawled, email=email, dropship_mentioned=ds,
            processing_fast=pf, delivery_fast=df, linkedin_found=li,
            email_verified=ev, sells_on_marketplace=mk,
        )
        res = score_lead(db, lead)
        exp_base, exp_bonus, exp_incomplete = legacy_score(lead)
        where = dict(ds=ds, pf=pf, df=df, rating=rating, reviews=reviews,
                     site=site, crawled=crawled, email=email, li=li, ev=ev, mk=mk)
        assert (res.base, res.bonus) == (exp_base, exp_bonus), where
        assert res.incomplete == exp_incomplete, where
        assert res.tier == legacy_tier(exp_base + exp_bonus), where
        checked += 1
    assert checked == 2304
