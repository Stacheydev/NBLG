"""Shared test fixtures.

Tests NEVER touch the network.  Every store fixture in tests/fixtures/ is
real measured data captured by tests/capture_fixtures.py.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

from exclusions import load_exclusions  # noqa: E402


@pytest.fixture(scope="session")
def exclusions():
    return load_exclusions()


def load_store(label):
    """Load one captured store fixture."""
    path = FIXTURE_DIR / f"{label}.json"
    if not path.exists():
        pytest.skip(f"fixture {label}.json not captured")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture
def store():
    return load_store


def qualify_store(label, exclusions, **overrides):
    """Qualify a captured store entirely offline."""
    import qualification

    data = load_store(label)
    analysis = dict(data["analysis"])
    analysis.update(overrides.pop("analysis", {}))

    return qualification.qualify(
        data["url"],
        analysis,
        exclusions,
        fetch_external=False,
        products=overrides.pop("products", data["products"]),
        followers=overrides.pop("followers", data["instagram_followers"]),
    )


def make_analysis(**overrides):
    """A minimal analyze_site()-shaped dict for synthetic cases."""
    analysis = {
        "is_shopify": True,
        "is_ecommerce": True,
        "is_lebanon": True,
        "business_name": "Test Store",
        "email": None,
        "phone": None,
        "whatsapp": None,
        "instagram": None,
        "city": None,
        "industry": "general",
        "lebanon_signals": {
            "strong": ["lebanese_phone"], "claim": [], "medium": [],
            "us_conflict": False,
        },
        "hreflang_count": 0,
        "has_sentry": False,
        "has_store_locator": False,
        "corporate_terms": [],
        "fetch_ok": True,
    }
    analysis.update(overrides)
    return analysis
