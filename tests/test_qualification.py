"""The core behavioural contract: who is a lead, and who must not be.

Every store here is real measured data, captured from the live site by
tests/capture_fixtures.py.  The must-NOT-reject half of this file is the
important half - it is what stops a fix for large-brand false positives
from quietly turning into a false-negative problem.

The contract is binary.  There is no score and no REVIEW band: a
candidate is a lead or it is not, and `Decision.reason` says which rule
turned it away.
"""

import pytest

import qualification
from qualification import (
    LARGE_BRAND,
    NOT_SHOPIFY,
    OUTSIDE_LEBANON,
    PLACEHOLDER,
)
from conftest import make_analysis, qualify_store


# ============================================================
# RULE 1 - SHOPIFY
# ============================================================

def test_non_shopify_site_is_not_a_lead(exclusions):
    analysis = make_analysis(business_name="Beirut Books", is_shopify=False)
    decision = qualification.qualify(
        "https://beirutbooks.com", analysis, exclusions,
        fetch_external=False, products=None, followers=1_000,
    )
    assert not decision.qualified
    assert decision.reason == NOT_SHOPIFY


def test_shopify_is_checked_before_anything_else(exclusions):
    """A non-Shopify site is rejected for that, not for a later rule,
    so the run summary attributes it correctly."""
    analysis = make_analysis(
        business_name="Nike", is_shopify=False,
        lebanon_signals={"strong": [], "claim": [], "medium": [],
                         "us_conflict": True},
    )
    decision = qualification.qualify(
        "https://nike.com", analysis, exclusions,
        fetch_external=False, products=None, followers=5_000_000,
    )
    assert decision.reason == NOT_SHOPIFY


# ============================================================
# RULE 2 - LEBANON
# ============================================================

def test_non_lebanese_business_is_not_a_lead(exclusions):
    """Maureen Abood is a Michigan business selling Lebanese food - the
    exact false positive a bare 'Lebanon' keyword match lets through."""
    decision = qualify_store("maureen_abood_us", exclusions)
    assert not decision.qualified
    assert decision.reason == OUTSIDE_LEBANON


def test_us_town_named_lebanon_is_not_lebanon(exclusions):
    analysis = make_analysis(
        business_name="Lebanon PA Books",
        lebanon_signals={"strong": [], "claim": ["lebanon_in_business_name"],
                         "medium": [], "us_conflict": True},
    )
    decision = qualification.qualify(
        "https://lebanonbooks.com", analysis, exclusions,
        fetch_external=False, products=None, followers=800,
    )
    assert not decision.qualified
    assert decision.reason == OUTSIDE_LEBANON


# ============================================================
# RULE 3 - NOT A HUGE BRAND
# ============================================================

@pytest.mark.parametrize("label", [
    "lacoste",       # mono-brand outlet + exclusion list + 9M followers
    "swarovski",     # mono-brand outlet + exclusion list
    "istyle",        # mono-brand Apple reseller - name matching misses it
    "marie_france",  # 179K followers
    "mike_sport",    # 117K followers
    "fattal",        # major Lebanese distribution group
])
def test_large_brands_are_not_leads(label, exclusions):
    decision = qualify_store(label, exclusions)
    assert not decision.qualified, f"{label} was qualified"
    assert decision.reason == LARGE_BRAND, (
        f"{label} rejected for {decision.reason}, expected {LARGE_BRAND}"
    )


@pytest.mark.parametrize("label,expected_fragment", [
    ("lacoste", "Lacoste"),
    ("swarovski", "Swarovski"),
    ("istyle", "Apple"),
])
def test_mono_brand_outlets_name_the_brand(label, expected_fragment, exclusions):
    decision = qualify_store(label, exclusions)
    assert expected_fragment in decision.detail


def test_brand_scale_following_is_not_a_lead(exclusions):
    analysis = make_analysis(business_name="Some Store")
    decision = qualification.qualify(
        "https://somestore.com", analysis, exclusions,
        fetch_external=False, products=None, followers=1_500_000,
    )
    assert not decision.qualified
    assert decision.reason == LARGE_BRAND


def test_the_brand_follower_threshold_sits_above_every_measured_sme(exclusions):
    """45K was the largest legitimate SME in the measured sample and 112K
    the smallest brand.  The threshold must stay inside that gap."""
    assert 45_000 < qualification.BRAND_FOLLOWERS <= 112_000


def test_placeholder_store_name_is_not_a_lead(exclusions):
    """Shopify's default name - the merchant never configured the shop."""
    analysis = make_analysis(business_name="My Store")
    decision = qualification.qualify(
        "https://xd1c1w-03.myshopify.com", analysis, exclusions,
        fetch_external=False, products=None, followers=None,
    )
    assert not decision.qualified
    assert decision.reason == PLACEHOLDER


# ============================================================
# MUST NOT REJECT - legitimate Lebanese SMEs
# ============================================================

@pytest.mark.parametrize("label", [
    "qatfa", "lightwave", "adaline", "moromart", "curly_square",
    "istahly", "outgeeked", "mj_boardgames", "petriotics", "klaptap",
    "livgood",
])
def test_legitimate_smes_are_leads(label, exclusions):
    decision = qualify_store(label, exclusions)
    assert decision.qualified, (
        f"{label} was wrongly rejected: {decision.reason} - {decision.detail}"
    )


def test_small_shopify_store_qualifies(exclusions):
    analysis = make_analysis(
        business_name="Zeina Handmade",
        email="zeina.handmade@gmail.com",
        whatsapp="+9617011223",
    )
    decision = qualification.qualify(
        "https://zeinahandmade.myshopify.com", analysis, exclusions,
        fetch_external=False, products=None, followers=800,
    )
    assert decision.qualified


# ============================================================
# EDGE CASES - the traps a naive size filter falls into
# ============================================================

def test_large_catalogue_does_not_reject_an_sme(exclusions):
    """OutGeeked has 1000+ products and 37 vendors and is a good lead.
    Catalogue size must not influence the decision at all."""
    assert qualify_store("outgeeked", exclusions).qualified


def test_dropshipper_shaped_sme_is_a_lead(exclusions):
    """Petriotics: 1000+ products, 42 third-party vendors, no own brand."""
    assert qualify_store("petriotics", exclusions).qualified


def test_strong_instagram_alone_does_not_reject_an_sme(exclusions):
    """Curly Square has 32K followers - well-followed, still an SME."""
    assert qualify_store("curly_square", exclusions).qualified


def test_good_website_does_not_reject_an_sme(exclusions):
    """Istahly runs a heavy, polished storefront with Klaviyo.  Website
    quality is deliberately not a qualification signal."""
    assert qualify_store("istahly", exclusions).qualified


def test_a_store_locator_does_not_reject_an_sme(exclusions):
    """Having a branch is not being a brand.  This was a -3 scoring
    penalty; under binary rules it carries no weight at all."""
    analysis = make_analysis(
        business_name="Beirut Coffee Roasters",
        has_store_locator=True,
        whatsapp="+9613111222",
    )
    decision = qualification.qualify(
        "https://beirutcoffee.com", analysis, exclusions,
        fetch_external=False, products=None, followers=1_500,
    )
    assert decision.qualified


def test_a_mono_brand_catalogue_of_an_unknown_brand_is_a_lead(exclusions):
    """The mono-brand rule targets outlets of KNOWN international brands.
    A shop selling only its own label is the target customer."""
    products = {
        "available": True, "product_count": 40,
        "vendors": {"Zeina Handmade": 40},
        "dominant_vendor": "Zeina Handmade", "dominant_share": 1.0,
    }
    analysis = make_analysis(business_name="Zeina Handmade")
    decision = qualification.qualify(
        "https://zeinahandmade.com", analysis, exclusions,
        fetch_external=False, products=products, followers=2_000,
    )
    assert decision.qualified


# ============================================================
# MISSING / FAILED EXTERNAL SIGNALS MUST FAIL SAFE
# ============================================================

def test_missing_follower_count_is_neutral_not_fatal(exclusions):
    """Instagram being unreachable must never reject a lead."""
    analysis = make_analysis(
        business_name="Saida Sweets",
        email="saidasweets@gmail.com",
        whatsapp="+9617012345",
    )
    decision = qualification.qualify(
        "https://saidasweets.com", analysis, exclusions,
        fetch_external=False, products=None, followers=None,
    )
    assert decision.qualified


def test_missing_products_json_is_neutral(exclusions):
    analysis = make_analysis(business_name="Byblos Ceramics")
    decision = qualification.qualify(
        "https://byblosceramics.com", analysis, exclusions,
        fetch_external=False, products=None, followers=3_000,
    )
    assert decision.qualified


def test_zero_followers_is_not_treated_as_a_brand(exclusions):
    """0 is a measurement, None means 'we do not know'.  Neither is
    evidence of being a huge brand."""
    analysis = make_analysis(business_name="New Shop LB")
    decision = qualification.qualify(
        "https://newshoplb.com", analysis, exclusions,
        fetch_external=False, products=None, followers=0,
    )
    assert decision.qualified


# ============================================================
# THE DECISION CARRIES NO SCORE
# ============================================================

def test_decision_is_binary(exclusions):
    """Guard against a score creeping back in: ranking is not part of
    this product, and a partially-scored decision is the shape that
    quietly reintroduces it."""
    decision = qualify_store("qatfa", exclusions)
    assert isinstance(decision.qualified, bool)
    assert not hasattr(decision, "score")
    assert not hasattr(decision, "outcome")


# ============================================================
# THE 100K BOUNDARY IS STRICT
# ============================================================

def test_exactly_100k_followers_still_qualifies(exclusions):
    """The rule is 'more than 100,000'.  An account sitting exactly on
    the line is not over it."""
    analysis = make_analysis(business_name="Borderline Store")
    decision = qualification.qualify(
        "https://borderline.com", analysis, exclusions,
        fetch_external=False, products=None, followers=100_000,
    )
    assert decision.qualified


def test_one_follower_over_the_line_is_disqualified(exclusions):
    analysis = make_analysis(business_name="Borderline Store")
    decision = qualification.qualify(
        "https://borderline.com", analysis, exclusions,
        fetch_external=False, products=None, followers=100_001,
    )
    assert not decision.qualified
    assert decision.reason == LARGE_BRAND


def test_an_unknown_follower_count_does_not_disqualify(exclusions):
    """Confirmed product decision: if the count cannot be retrieved we
    qualify rather than guess.  A fabricated count is worse."""
    analysis = make_analysis(business_name="Saida Sweets",
                             instagram="saidasweets")
    decision = qualification.qualify(
        "https://saidasweets.com", analysis, exclusions,
        fetch_external=False, products=None, followers=None,
    )
    assert decision.qualified
