"""scoring.py - contact priority, as a heuristic.

    score(signals, analysis) -> ScoreResult(score, band, reasons)

WHAT THIS IS

A heuristic ordering of ALREADY-QUALIFIED leads, to answer one question:

    "Which qualified lead should Hadi contact first?"

WHAT THIS IS NOT

It is NOT a reply-probability model.  It is NOT a "chance of response".
It is NOT a likelihood of replying.  The project has 1,636 labelled
QUALIFICATION outcomes and zero labelled OUTREACH outcomes, so nothing here
can be calibrated against whether a business actually answered.  Every
weight below is a judgement about what is observable, not a fitted
coefficient - which is why the components are deliberately boring:

  contactability   a lead with no channel cannot be contacted at all.  This
                   is a precondition, not a prediction.
  relevance        how confident we are this is a Lebanese SME that owns its
                   own storefront, i.e. that North Bound's work applies.
  audience         whether there is an established audience to reference.
                   Flat: no penalty for being large, because "big brands
                   reply less" is exactly the kind of unvalidated claim this
                   module refuses to encode.

Signals that could plausibly matter but are NOT weighted - has_sentry,
hreflang_count, has_store_locator, industry, city - are persisted by main.py
at weight zero.  They are recorded so the score can be retuned against real
outreach outcomes later without re-fetching every site.  Acting on them now
would be overfitting to a hunch.

RULES THIS MODULE KEEPS

  * Qualification is untouched.  score() only ever sees leads that already
    passed, and has no return value that can reject one.
  * An unknown signal is never scored as zero.  fetch_instagram_followers()
    returns None when Instagram rate-limits us just as readily as when an
    account has no followers; treating that as zero would punish a lead for
    our own throttling.  qualification.py already treats unknown as neutral
    and this mirrors it.
  * Deterministic.  No clock, no randomness, no network, no I/O.
  * Every weight lives in WEIGHTS, so retuning is editing numbers in one
    place rather than reading the logic.
"""

import math
from dataclasses import dataclass

# ============================================================
# BANDS
# ============================================================

HIGH = "high"
MEDIUM = "medium"
LOW = "low"

#: Lowest score that earns each band, highest band first.
BANDS = ((8, HIGH), (5, MEDIUM), (1, LOW))

SCORE_MIN = 1
SCORE_MAX = 10

# Lebanon evidence tiers, as scraper.lebanon_signals() ranks them.
TIER_STRONG = "strong"
TIER_CLAIM = "claim"
TIER_MEDIUM = "medium"
TIER_NONE = "none"


# ============================================================
# WEIGHTS - the only place to edit when retuning
# ============================================================

WEIGHTS = {
    # Can we reach this business at all?  Capped, so a lead with every
    # channel cannot crowd out relevance and audience entirely.
    "contactability": {
        "instagram": 2.0,
        "whatsapp": 2.0,
        "email": 1.5,
        "phone": 0.5,
        "cap": 6.0,
    },
    # Is this clearly the kind of business North Bound can help?
    "relevance": {
        TIER_STRONG: 2.0,
        TIER_CLAIM: 1.0,
        TIER_MEDIUM: 0.5,
        TIER_NONE: 0.0,
        "own_brand": 1.0,
        # A catalogue this dominated by one vendor is the shop's own label.
        # Qualification has already rejected international-brand outlets, so
        # a high share here means the merchant owns what it sells - and
        # therefore owns the storefront decisions.
        "own_brand_threshold": 0.80,
        "cap": 3.0,
    },
    # Is there an established audience to reference in outreach?  Flat on
    # purpose - see the module docstring.
    "audience": {
        "established": 1.0,
        "small": 0.5,
        "unknown": 0.5,
        "established_threshold": 1000,
        "cap": 1.0,
    },
}


@dataclass(frozen=True)
class ScoreResult:
    """One lead's heuristic contact priority."""

    score: int          # 1-10
    band: str           # HIGH / MEDIUM / LOW
    reasons: str        # human-readable component breakdown


# ============================================================
# COMPONENTS
# ============================================================

def lebanon_tier(lebanon_signals):
    """The strongest tier of Lebanon evidence present.

    Mirrors the precedence in scraper.passes_lebanon_gate(): a registry- or
    telecom-backed signal outranks a self-identification claim, which
    outranks merely suggestive evidence.  Returned as its own value because
    main.py persists it alongside the score.
    """
    signals = lebanon_signals or {}

    if signals.get("strong"):
        return TIER_STRONG
    if signals.get("claim"):
        return TIER_CLAIM
    if signals.get("medium"):
        return TIER_MEDIUM
    return TIER_NONE


def _contactability(analysis, signals):
    """Points for the channels we could actually open a conversation on."""
    weights = WEIGHTS["contactability"]
    points = 0.0
    found = []

    # Instagram comes from signals, which has already validated the handle;
    # analysis["instagram"] is the raw candidate.
    if signals.get("instagram_url") or signals.get("instagram"):
        points += weights["instagram"]
        found.append("ig")
    if analysis.get("whatsapp"):
        points += weights["whatsapp"]
        found.append("wa")
    if analysis.get("email"):
        points += weights["email"]
        found.append("email")
    if analysis.get("phone"):
        points += weights["phone"]
        found.append("phone")

    capped = min(points, weights["cap"])
    return capped, (",".join(found) if found else "none")


def _relevance(signals):
    """Points for confidence this is a Lebanese SME that owns its storefront."""
    weights = WEIGHTS["relevance"]
    tier = lebanon_tier(signals.get("lebanon_signals"))
    points = weights[tier]
    found = [tier] if tier != TIER_NONE else []

    # dominant_share is None when products.json was unreachable.  Unknown is
    # not evidence of a reseller, so it simply scores nothing here rather
    # than being penalised.
    share = signals.get("dominant_share")
    if share is not None and share >= weights["own_brand_threshold"]:
        points += weights["own_brand"]
        found.append("own-brand")

    capped = min(points, weights["cap"])
    return capped, (",".join(found) if found else "none")


def _audience(signals):
    """Points for an established audience.  Unknown is neutral, never zero."""
    weights = WEIGHTS["audience"]
    followers = signals.get("instagram_followers")

    if followers is None:
        return min(weights["unknown"], weights["cap"]), "unknown"

    if followers >= weights["established_threshold"]:
        return min(weights["established"], weights["cap"]), f"{followers}"

    return min(weights["small"], weights["cap"]), f"{followers}"


def band_for(score):
    """The band a 1-10 score falls into."""
    for floor, name in BANDS:
        if score >= floor:
            return name
    return LOW


def _round_half_up(value):
    """Round .5 upwards, always.

    NOT the builtin round(), which rounds half to EVEN: round(2.5) is 2 and
    round(5.5) is 6.  Every weight here is a multiple of 0.5, so half-values
    are the common case rather than an edge case, and banker's rounding
    would make two leads one component apart score the same.
    """
    return math.floor(value + 0.5)


# ============================================================
# THE SCORE
# ============================================================

def score(signals, analysis=None):
    """Heuristic contact priority for one qualified lead.

    `signals` is qualification.collect_signals() output; `analysis` is
    scraper.analyze_site() output.  Both are read-only here.  Either may be
    missing keys - every component treats absent as unknown and neutral, so
    this never raises on a partial record.
    """
    signals = signals or {}
    analysis = analysis or {}

    reach, reach_detail = _contactability(analysis, signals)
    relevance, relevance_detail = _relevance(signals)
    audience, audience_detail = _audience(signals)

    total = reach + relevance + audience
    final = max(SCORE_MIN, min(SCORE_MAX, _round_half_up(total)))

    reasons = (
        f"reach={reach:g}({reach_detail}) "
        f"relevance={relevance:g}({relevance_detail}) "
        f"audience={audience:g}({audience_detail})"
    )

    return ScoreResult(score=final, band=band_for(final), reasons=reasons)
