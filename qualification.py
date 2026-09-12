"""qualification.py - the four NorthBound rules, as a binary decision.

A candidate is a lead, or it is not.  There is no score, no band and no
"review" state: those existed to feed a calibration workflow that is no
longer part of the product.

    Rule 1  Shopify            the site must run Shopify
    Rule 2  Lebanon            the business must be IN Lebanon
    Rule 3  not a huge brand   SMEs only, not brands or their outlets
    Rule 4  not already known  handled by database.py, not here

Rules 1-3 live here.  Rule 4 needs the database and is applied by
main.py through database.find_existing_business().

The three brand tests in Rule 3 are the ones the project already
measured and implemented; nothing new was invented for them:

  * the 250-brand exclusion list  (195 international, 55 Lebanese groups)
  * mono-brand catalogue          (an outlet/franchise, not an SME)
  * brand-scale Instagram following

Everything else the old scorer weighed - catalogue size, store locators,
enterprise tooling, "sells own brand", the .myshopify.com bonus - was
soft evidence used to rank, and ranking is not part of this product.
"""

from dataclasses import dataclass, field

import scraper
from exclusions import is_excluded

# ============================================================
# THRESHOLDS
# ============================================================

# Instagram following that only a national/global brand reaches.
#
# The project's own measurements (IMPLEMENTATION_REPORT.md, and the
# sample stored in the database) found every legitimate SME at or below
# 45K followers, while the large brands started at 112K.  100K sits in
# that gap.
#
# The previous value was 250_000, which was not a Rule 3 filter at all:
# it let Marie France Lingerie through at 179K followers.  That store is
# a major Lebanese brand and is exactly what Rule 3 exists to exclude.
BRAND_FOLLOWERS = 100_000

# A store selling almost nothing but one international brand is that
# brand's outlet, not an independent business.
MONO_BRAND_SHARE = 0.80

# Shopify's default store name: the merchant never configured the shop,
# so there is no business here to contact.
PLACEHOLDER_NAMES = {
    "my store", "my shop", "your store", "store name", "shopify store",
    "example store", "my new store", "test store", "demo store",
}

# Machine-readable reasons, so main.py can count outcomes without
# parsing English.
NOT_SHOPIFY = "not_shopify"
OUTSIDE_LEBANON = "outside_lebanon"
LARGE_BRAND = "large_brand"
PLACEHOLDER = "placeholder_store"
STORE_CLOSED = "store_closed"


@dataclass
class Decision:
    """Why a candidate is, or is not, a lead."""
    qualified: bool
    reason: str = None          # one of the constants above, or None
    detail: str = None          # human-readable, for the run log
    signals: dict = field(default_factory=dict)


def _rejected(reason, detail, signals):
    return Decision(qualified=False, reason=reason, detail=detail,
                    signals=signals)


# ============================================================
# SIGNAL COLLECTION
# ============================================================

def collect_signals(url, analysis, fetch_external=True,
                    products=None, followers=None):
    """Gather what the three rules need for one candidate.

    `analysis` is the dict from scraper.analyze_site().  The two external
    lookups can be injected (for tests) or skipped entirely; when they
    fail they stay None, which every rule treats as "no evidence" rather
    than as evidence of smallness.
    """
    domain = scraper.clean_url(url)
    name = analysis.get("business_name") or scraper.name_from_domain(url)

    if fetch_external and products is None:
        products = scraper.fetch_products_json(url)
    if fetch_external and followers is None:
        followers = scraper.fetch_instagram_followers(analysis.get("instagram"))

    return {
        "domain": domain,
        "business_name": name,
        "website": scraper.root_url(url),
        "instagram": analysis.get("instagram"),
        "instagram_url": scraper.instagram_url(analysis.get("instagram")),
        "instagram_followers": followers,
        "is_shopify": bool(analysis.get("is_shopify")),
        "is_available": analysis.get("is_available", True),
        "lebanon_signals": analysis.get("lebanon_signals")
        or {"strong": [], "claim": [], "medium": [], "us_conflict": False},
        "dominant_vendor": products.get("dominant_vendor") if products else None,
        "dominant_share": products.get("dominant_share") if products else None,
    }


# ============================================================
# THE RULES
# ============================================================

def decide(signals, exclusions):
    """Apply rules 1-3 to an already-collected signal record.

    This is the single source of truth for whether a candidate is a
    lead.  Rules are checked cheapest-first and the first failure wins,
    so the reported reason is the primary one.
    """
    # --- Rule 1: Shopify ------------------------------------------
    if not signals.get("is_shopify"):
        return _rejected(NOT_SHOPIFY, "not a Shopify store", signals)

    # --- Storefront must actually be open -------------------------
    # A suspended or not-yet-opened store is not a business anyone can
    # be sold to today.  Checked right after Shopify because a closed
    # Shopify store still carries every other signal - it would sail
    # through the remaining rules and land in the lead list.
    if not signals.get("is_available", True):
        return _rejected(
            STORE_CLOSED, "storefront is closed or suspended", signals
        )

    # --- Rule 2: Lebanon ------------------------------------------
    if not scraper.passes_lebanon_gate(signals["lebanon_signals"]):
        return _rejected(
            OUTSIDE_LEBANON,
            "no evidence the business is in Lebanon",
            signals,
        )

    # --- Rule 3: not a huge brand ---------------------------------
    name = (signals.get("business_name") or "").strip()

    if name.lower() in PLACEHOLDER_NAMES:
        return _rejected(
            PLACEHOLDER, f"unconfigured store name {name!r}", signals
        )

    match = is_excluded(name, signals.get("domain"), exclusions)
    if match:
        return _rejected(
            LARGE_BRAND, f"known brand: {match['name']}", signals
        )

    vendor = signals.get("dominant_vendor")
    share = signals.get("dominant_share") or 0.0
    if vendor and share >= MONO_BRAND_SHARE:
        match = is_excluded(
            vendor, None, exclusions,
            scopes=("international",),
            allow_generic_word=True,
        )
        if match:
            return _rejected(
                LARGE_BRAND,
                f"outlet for {match['name']} ({share:.0%} of catalogue)",
                signals,
            )

    # Strictly greater: the rule is "more than 100,000".  An account
    # sitting exactly on the line is not over it.
    followers = signals.get("instagram_followers")
    if followers is not None and followers > BRAND_FOLLOWERS:
        return _rejected(
            LARGE_BRAND, f"{followers:,} Instagram followers", signals
        )

    return Decision(qualified=True, signals=signals)


def qualify(url, analysis, exclusions, fetch_external=True,
            products=None, followers=None):
    """Qualify one candidate.  The ONLY way a lead is judged."""
    signals = collect_signals(
        url, analysis,
        fetch_external=fetch_external,
        products=products,
        followers=followers,
    )
    return decide(signals, exclusions)
