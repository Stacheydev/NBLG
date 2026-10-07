"""Contact priority: the heuristic that orders already-qualified leads.

Nothing here touches the network or the database - score() is a pure
function of two dicts.

The point of this suite is not that the numbers are "right" (they are
judgement, not fitted coefficients - see scoring.py's docstring). It is that
they are DETERMINISTIC and that the two invariants hold: an unknown signal
is never scored as zero, and scoring can never reject a lead.
"""

import itertools

import pytest

import qualification
import scoring


# ============================================================
# HARNESS
# ============================================================

def signals(**overrides):
    """A qualified lead's signals, as qualification.collect_signals() shapes
    them. Defaults are all-unknown so each test adds only what it exercises."""
    record = {
        "domain": "shop.com",
        "business_name": "Shop",
        "website": "https://shop.com",
        "instagram": None,
        "instagram_url": None,
        "instagram_followers": None,
        "is_shopify": True,
        "is_available": True,
        "lebanon_signals": {
            "strong": [], "claim": [], "medium": [], "us_conflict": False,
        },
        "dominant_vendor": None,
        "dominant_share": None,
    }
    record.update(overrides)
    return record


def analysis(**overrides):
    """scraper.analyze_site() output, reduced to the keys scoring reads."""
    record = {
        "email": None,
        "phone": None,
        "whatsapp": None,
        "city": None,
        "industry": None,
        "hreflang_count": 0,
        "has_sentry": False,
        "has_store_locator": False,
    }
    record.update(overrides)
    return record


def leb(tier):
    """A lebanon_signals dict whose strongest tier is `tier`."""
    empty = {"strong": [], "claim": [], "medium": [], "us_conflict": False}
    if tier == scoring.TIER_STRONG:
        return {**empty, "strong": ["lebanese_phone"]}
    if tier == scoring.TIER_CLAIM:
        return {**empty, "claim": ["lebanon_in_domain"]}
    if tier == scoring.TIER_MEDIUM:
        return {**empty, "medium": ["lbp_currency"]}
    return empty


# ============================================================
# CONTACTABILITY - every combination
# ============================================================

CHANNELS = {
    "instagram": (lambda s, a: s.update({"instagram_url": "https://instagram.com/x"}), 2.0),
    "whatsapp": (lambda s, a: a.update({"whatsapp": "+96170123456"}), 2.0),
    "email": (lambda s, a: a.update({"email": "hi@shop.com"}), 1.5),
    "phone": (lambda s, a: a.update({"phone": "+96170123456"}), 0.5),
}


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
def test_every_contactability_combination(count):
    """All 16 subsets of the four channels score their capped sum."""
    for combo in itertools.combinations(sorted(CHANNELS), count):
        s, a = signals(), analysis()
        for name in combo:
            CHANNELS[name][0](s, a)

        expected = min(
            sum(CHANNELS[name][1] for name in combo),
            scoring.WEIGHTS["contactability"]["cap"],
        )
        reach, _ = scoring._contactability(a, s)
        assert reach == expected, combo


def test_contactability_cap_binds_with_all_four_channels():
    """2.0 + 2.0 + 1.5 + 0.5 is 6.0, which is exactly the cap - and the cap
    must hold even if a weight is later raised."""
    s, a = signals(), analysis()
    for setter, _ in CHANNELS.values():
        setter(s, a)

    reach, detail = scoring._contactability(a, s)
    assert reach == scoring.WEIGHTS["contactability"]["cap"]
    for token in ("ig", "wa", "email", "phone"):
        assert token in detail


def test_no_channels_at_all_scores_zero_reach_but_not_zero_overall():
    """Unreachable is the one thing that SHOULD score zero in its component -
    but the lead still has a floor score, because it is still qualified."""
    result = scoring.score(signals(), analysis())
    reach, detail = scoring._contactability(analysis(), signals())
    assert reach == 0.0
    assert detail == "none"
    assert result.score >= scoring.SCORE_MIN


def test_a_bare_instagram_handle_counts_without_a_built_url():
    """collect_signals() can carry a handle whose instagram_url is None."""
    reach, _ = scoring._contactability(analysis(), signals(instagram="shoplb"))
    assert reach == scoring.WEIGHTS["contactability"]["instagram"]


# ============================================================
# RELEVANCE
# ============================================================

@pytest.mark.parametrize("tier,expected", [
    (scoring.TIER_STRONG, 2.0),
    (scoring.TIER_CLAIM, 1.0),
    (scoring.TIER_MEDIUM, 0.5),
    (scoring.TIER_NONE, 0.0),
])
def test_each_lebanon_tier_scores_its_weight(tier, expected):
    points, _ = scoring._relevance(signals(lebanon_signals=leb(tier)))
    assert points == expected


def test_strongest_lebanon_tier_wins_when_several_are_present():
    """passes_lebanon_gate() ranks strong over claim over medium; relevance
    must use the same precedence rather than summing them."""
    both = {
        "strong": ["lb_domain"], "claim": ["lebanon_in_domain"],
        "medium": ["lbp_currency"], "us_conflict": False,
    }
    assert scoring.lebanon_tier(both) == scoring.TIER_STRONG
    points, _ = scoring._relevance(signals(lebanon_signals=both))
    assert points == scoring.WEIGHTS["relevance"][scoring.TIER_STRONG]


@pytest.mark.parametrize("share,own_brand", [
    (0.79, False),
    (0.80, True),   # threshold is inclusive
    (0.81, True),
    (1.00, True),
])
def test_own_brand_threshold(share, own_brand):
    points, detail = scoring._relevance(signals(dominant_share=share))
    expected = scoring.WEIGHTS["relevance"]["own_brand"] if own_brand else 0.0
    assert points == expected
    assert ("own-brand" in detail) is own_brand


def test_relevance_combines_lebanon_and_own_brand():
    points, detail = scoring._relevance(
        signals(lebanon_signals=leb(scoring.TIER_STRONG), dominant_share=0.95)
    )
    assert points == 3.0
    assert "strong" in detail and "own-brand" in detail


def test_relevance_cap_binds():
    points, _ = scoring._relevance(
        signals(lebanon_signals=leb(scoring.TIER_STRONG), dominant_share=1.0)
    )
    assert points <= scoring.WEIGHTS["relevance"]["cap"]


# ============================================================
# AUDIENCE
# ============================================================

@pytest.mark.parametrize("followers,expected,detail", [
    (None, 0.5, "unknown"),   # unknown is NEUTRAL, never zero
    (0, 0.5, "0"),
    (999, 0.5, "999"),
    (1000, 1.0, "1000"),      # threshold is inclusive
    (8200, 1.0, "8200"),
    (95000, 1.0, "95000"),    # no penalty for being large
])
def test_audience_bands(followers, expected, detail):
    points, got = scoring._audience(signals(instagram_followers=followers))
    assert points == expected
    assert got == detail


def test_a_large_account_is_never_penalised():
    """"Big brands reply less" is exactly the unvalidated claim this module
    refuses to encode. 95k must not score below 2k."""
    big, _ = scoring._audience(signals(instagram_followers=95_000))
    small, _ = scoring._audience(signals(instagram_followers=2_000))
    assert big >= small


# ============================================================
# UNKNOWN SIGNALS ARE NEVER ZERO
# ============================================================

def test_unknown_optional_lookups_still_contribute_their_neutral_weight():
    """products.json 404 and an Instagram rate-limit must not score as zero.

    Note the total here IS 1: with no contact channel and no Lebanon evidence
    the only component in play is the neutral audience weight. That is the
    correct answer for a lead with nothing known about it - see the next test
    for why no REAL qualified lead looks like this.
    """
    result = scoring.score(signals(), analysis())
    assert "audience=0.5(unknown)" in result.reasons
    assert scoring._audience(signals())[0] == 0.5


def test_solid_lebanon_evidence_clears_the_floor_even_with_nothing_else_known():
    """Every lead reaching score() has passed passes_lebanon_gate(), so its
    lebanon_signals is never empty. With strong or claim-tier evidence the
    relevance points alone lift it off the floor despite every optional
    lookup having failed."""
    for tier in (scoring.TIER_STRONG, scoring.TIER_CLAIM):
        result = scoring.score(signals(lebanon_signals=leb(tier)), analysis())
        assert result.score > scoring.SCORE_MIN, tier
        assert "unknown" in result.reasons


def test_weak_evidence_and_no_contact_channel_sits_at_the_floor():
    """Deliberate, not a gap: medium-tier Lebanon evidence (0.5) plus a
    neutral unknown audience (0.5) is 1.0. A lead nobody can contact, whose
    only Lebanon evidence is suggestive, belongs last in the list - and the
    floor is where 'last' lives."""
    result = scoring.score(signals(lebanon_signals=leb(scoring.TIER_MEDIUM)),
                           analysis())
    assert result.score == scoring.SCORE_MIN
    assert result.band == scoring.LOW


@pytest.mark.parametrize("key", ["instagram_followers", "dominant_share"])
def test_an_unknown_signal_scores_strictly_above_its_worst_known_value(key):
    """None must never be worse than the lowest real measurement, because
    None means the lookup failed - not that the value is bad."""
    unknown = scoring.score(signals(**{key: None}), analysis()).score
    if key == "instagram_followers":
        worst_known = scoring.score(signals(instagram_followers=0), analysis()).score
    else:
        worst_known = scoring.score(signals(dominant_share=0.0), analysis()).score
    assert unknown >= worst_known


def test_missing_keys_entirely_do_not_raise():
    """Partial records must score, not explode."""
    assert scoring.score({}, {}).score >= scoring.SCORE_MIN
    assert scoring.score(None, None).score >= scoring.SCORE_MIN
    assert scoring.score({"lebanon_signals": None}, {}).score >= scoring.SCORE_MIN


# ============================================================
# CLAMPING AND ROUNDING
# ============================================================

def test_a_maximal_lead_scores_exactly_ten():
    s, a = signals(
        instagram_url="https://instagram.com/x",
        instagram_followers=8200,
        lebanon_signals=leb(scoring.TIER_STRONG),
        dominant_share=0.94,
    ), analysis(whatsapp="+96170123456", email="hi@shop.com", phone="+96170111")
    result = scoring.score(s, a)
    assert result.score == scoring.SCORE_MAX
    assert result.band == scoring.HIGH


def test_the_floor_is_one_never_zero():
    """An unreachable lead with no evidence still scores 1: it is qualified,
    so it belongs in the list, just last."""
    result = scoring.score(signals(instagram_followers=None), analysis())
    assert result.score >= 1


def test_score_is_always_within_range():
    for combo in itertools.product([None, 0, 500, 1000, 50_000],
                                   [None, 0.0, 0.5, 0.8, 1.0],
                                   [scoring.TIER_NONE, scoring.TIER_MEDIUM,
                                    scoring.TIER_CLAIM, scoring.TIER_STRONG]):
        followers, share, tier = combo
        result = scoring.score(
            signals(instagram_followers=followers, dominant_share=share,
                    lebanon_signals=leb(tier)),
            analysis(whatsapp="+961701", email="a@b.com"),
        )
        assert scoring.SCORE_MIN <= result.score <= scoring.SCORE_MAX, combo


def test_half_values_round_up_not_to_even():
    """Every weight is a multiple of 0.5, so half-values are the common case.
    The builtin round() would send 2.5 to 2 and 5.5 to 6 - inconsistent."""
    assert scoring._round_half_up(2.5) == 3
    assert scoring._round_half_up(5.5) == 6
    assert scoring._round_half_up(0.5) == 1
    assert scoring._round_half_up(2.4) == 2
    assert scoring._round_half_up(2.6) == 3


# ============================================================
# BANDING
# ============================================================

@pytest.mark.parametrize("score,band", [
    (1, scoring.LOW), (2, scoring.LOW), (3, scoring.LOW), (4, scoring.LOW),
    (5, scoring.MEDIUM), (6, scoring.MEDIUM), (7, scoring.MEDIUM),
    (8, scoring.HIGH), (9, scoring.HIGH), (10, scoring.HIGH),
])
def test_every_score_maps_to_its_band(score, band):
    assert scoring.band_for(score) == band


def test_band_boundaries():
    """The two edges that decide what Hadi contacts first."""
    assert scoring.band_for(4) == scoring.LOW
    assert scoring.band_for(5) == scoring.MEDIUM
    assert scoring.band_for(7) == scoring.MEDIUM
    assert scoring.band_for(8) == scoring.HIGH


# ============================================================
# THE WORKED EXAMPLES FROM THE PLAN
# ============================================================

def test_worked_example_high():
    """IG + WhatsApp + email, strong Lebanon, own-brand, 8.2k followers."""
    result = scoring.score(
        signals(instagram_url="https://instagram.com/x",
                instagram_followers=8200,
                lebanon_signals=leb(scoring.TIER_STRONG),
                dominant_share=0.94),
        analysis(whatsapp="+96170123456", email="hi@shop.com"),
    )
    assert result.score == 10 and result.band == scoring.HIGH


def test_worked_example_medium():
    """Instagram only, strong Lebanon, own-brand, followers unknown."""
    result = scoring.score(
        signals(instagram_url="https://instagram.com/x",
                lebanon_signals=leb(scoring.TIER_STRONG),
                dominant_share=0.9),
        analysis(),
    )
    assert result.score == 6 and result.band == scoring.MEDIUM


def test_worked_example_low():
    """Email only, medium-tier Lebanon, nothing else known."""
    result = scoring.score(
        signals(lebanon_signals=leb(scoring.TIER_MEDIUM)),
        analysis(email="hi@shop.com"),
    )
    assert result.score == 3 and result.band == scoring.LOW


# ============================================================
# REASONS
# ============================================================

def test_reasons_name_every_component():
    result = scoring.score(
        signals(instagram_url="https://instagram.com/x",
                instagram_followers=8200,
                lebanon_signals=leb(scoring.TIER_STRONG)),
        analysis(whatsapp="+96170123456"),
    )
    assert "reach=" in result.reasons
    assert "relevance=" in result.reasons
    assert "audience=" in result.reasons


def test_reasons_show_which_channels_were_found():
    result = scoring.score(
        signals(instagram_url="https://instagram.com/x"),
        analysis(email="hi@shop.com"),
    )
    assert "ig" in result.reasons and "email" in result.reasons
    assert "wa" not in result.reasons


def test_reasons_distinguish_unknown_from_measured_zero():
    """The whole point of recording reasons: a 6/10 built on three unknowns
    must be visibly different from a 6/10 built on measurements."""
    unknown = scoring.score(signals(), analysis()).reasons
    measured = scoring.score(signals(instagram_followers=0), analysis()).reasons
    assert "audience=0.5(unknown)" in unknown
    assert "audience=0.5(0)" in measured


def test_reasons_are_a_single_line():
    """They go into one TEXT column and one HTML title attribute."""
    reasons = scoring.score(signals(), analysis()).reasons
    assert "\n" not in reasons and "\t" not in reasons


# ============================================================
# UNWEIGHTED SIGNALS MUST NOT MOVE THE SCORE
# ============================================================

@pytest.mark.parametrize("field,value", [
    ("has_sentry", True),
    ("has_store_locator", True),
    ("hreflang_count", 12),
    ("industry", "fashion"),
    ("city", "Beirut"),
])
def test_recorded_but_unweighted_signals_do_not_change_the_score(field, value):
    """These are persisted for future retuning at weight zero. If one of them
    starts moving the score, that is overfitting to an unvalidated hunch."""
    base = scoring.score(signals(), analysis())
    with_signal = scoring.score(signals(), analysis(**{field: value}))
    assert with_signal.score == base.score
    assert with_signal.reasons == base.reasons


# ============================================================
# INVARIANTS: SCORING CANNOT AFFECT QUALIFICATION
# ============================================================

def test_score_does_not_mutate_its_inputs():
    """main.py passes the live dicts straight through; a mutation here would
    corrupt what add_lead() and find_existing_business() then read."""
    s = signals(instagram_url="https://instagram.com/x", instagram_followers=5000)
    a = analysis(email="hi@shop.com", whatsapp="+961701")
    before = (repr(sorted(s.items(), key=str)), repr(sorted(a.items(), key=str)))

    scoring.score(s, a)

    assert (repr(sorted(s.items(), key=str)),
            repr(sorted(a.items(), key=str))) == before


def test_scoring_exposes_no_way_to_reject_a_lead():
    """ScoreResult carries a score, a band and reasons - and nothing that
    main.py could interpret as 'skip this lead'."""
    result = scoring.score(signals(), analysis())
    assert set(result.__dataclass_fields__) == {"score", "band", "reasons"}
    assert not hasattr(result, "qualified")
    assert not hasattr(result, "reason")


def test_scoring_never_returns_a_falsy_score():
    """A 0 or None score could read as 'unqualified' at a call site."""
    for followers in (None, 0, 1, 1000, 10**9):
        result = scoring.score(signals(instagram_followers=followers), analysis())
        assert result.score


def test_qualification_decision_is_unchanged_by_the_presence_of_scoring():
    """scoring imports nothing from qualification and vice versa. A lead's
    qualified/not verdict must be identical whether or not scoring ran."""
    import exclusions

    exclusion_list = exclusions.load_exclusions()
    site = {
        "is_shopify": True,
        "is_available": True,
        "business_name": "Qatfa",
        "instagram": "qatfa",
        "lebanon_signals": leb(scoring.TIER_STRONG),
    }

    first = qualification.qualify(
        "https://qatfa.com", site, exclusion_list,
        fetch_external=False, products=None, followers=None,
    )
    scoring.score(first.signals, site)
    second = qualification.qualify(
        "https://qatfa.com", site, exclusion_list,
        fetch_external=False, products=None, followers=None,
    )

    assert first.qualified == second.qualified
    assert first.reason == second.reason


def test_scoring_is_deterministic():
    """Same inputs, same output, every time - no clock, no randomness."""
    s = signals(instagram_url="https://instagram.com/x",
                instagram_followers=4200,
                lebanon_signals=leb(scoring.TIER_CLAIM),
                dominant_share=0.85)
    a = analysis(email="hi@shop.com", phone="+961701")
    results = {scoring.score(s, a) for _ in range(25)}
    assert len(results) == 1


# ============================================================
# WEIGHTS ARE EDITABLE IN ONE PLACE
# ============================================================

def test_the_documented_weights_are_what_is_actually_applied():
    """Guards against the table in the plan drifting from the code."""
    c = scoring.WEIGHTS["contactability"]
    assert (c["instagram"], c["whatsapp"], c["email"], c["phone"], c["cap"]) \
        == (2.0, 2.0, 1.5, 0.5, 6.0)

    r = scoring.WEIGHTS["relevance"]
    assert (r[scoring.TIER_STRONG], r[scoring.TIER_CLAIM],
            r[scoring.TIER_MEDIUM], r["own_brand"],
            r["own_brand_threshold"], r["cap"]) == (2.0, 1.0, 0.5, 1.0, 0.80, 3.0)

    a = scoring.WEIGHTS["audience"]
    assert (a["established"], a["small"], a["unknown"],
            a["established_threshold"], a["cap"]) == (1.0, 0.5, 0.5, 1000, 1.0)


def test_component_caps_sum_to_the_maximum_score():
    """6 + 3 + 1 = 10. If a cap is retuned without adjusting the others the
    score can no longer reach 10, which would silently break banding."""
    total = (scoring.WEIGHTS["contactability"]["cap"]
             + scoring.WEIGHTS["relevance"]["cap"]
             + scoring.WEIGHTS["audience"]["cap"])
    assert total == scoring.SCORE_MAX
