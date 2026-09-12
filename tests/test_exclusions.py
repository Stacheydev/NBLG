"""The exclusion list: it must catch real brands without eating SMEs.

The old blacklist failed in both directions at once - "Apple" rejected
"Pineapple Boutique" while "ABC" and "TSC" (two of the largest Lebanese
retail groups) could never match anything at all.
"""

import json

import pytest

from exclusions import (
    ExclusionsError,
    is_excluded,
    load_exclusions,
    normalize_text,
    tokenize,
    domain_labels,
)


# ============================================================
# SUBSTRING FALSE POSITIVES - the SME-protection cases
# ============================================================

@pytest.mark.parametrize("name", [
    "Pineapple Boutique",       # must not match Apple
    "Bossa Nova Beirut",        # must not match Boss
    "Coachella Style LB",       # must not match Coach
    "Brownstone Cafe",          # must not match Browns
    "Creedence Records",        # must not match Creed
    "Caravans Lebanon",         # must not match Vans
    "Mangoes and More",         # must not match Mango
    "Diesel Mechanics Beirut",  # generic word, name context
])
def test_substrings_do_not_reject_legitimate_names(name, exclusions):
    assert is_excluded(name, None, exclusions) is None


@pytest.mark.parametrize("name", [
    "Apple Orchard Farm",       # "Apple" is an ordinary word
    "Target Fitness Lebanon",
    "Mango Juice Beirut",
    "Boss Barbers Hamra",
])
def test_generic_word_brands_never_match_a_business_name(name, exclusions):
    """Brands whose name is an everyday word only match on domain or on a
    products.json vendor - never on a business name."""
    assert is_excluded(name, None, exclusions) is None


# ============================================================
# REAL MATCHES MUST STILL WORK
# ============================================================

@pytest.mark.parametrize("name,domain,expected", [
    ("LACOSTE LEBANON", "lacoste.com.lb", "Lacoste"),
    ("Swarovski Lebanon Online Store", "swarovski.com.lb", "Swarovski"),
    ("Some Shop", "lacoste-lb.com", "Lacoste"),       # brand in a hyphenated domain
    ("Some Shop", "lacoste.com", "Lacoste"),          # explicit domain entry
])
def test_real_brands_are_matched(name, domain, expected, exclusions):
    match = is_excluded(name, domain, exclusions)
    assert match is not None
    assert match["name"] == expected


@pytest.mark.parametrize("name,domain", [
    ("ABC Verdun", "abc.com.lb"),
    ("TSC Signature", None),
])
def test_short_brand_names_work_again(name, domain, exclusions):
    """Under the old rules anything shorter than 4 characters could only
    match a whole string, so these never fired."""
    assert is_excluded(name, domain, exclusions) is not None


def test_generic_word_brand_matches_a_product_vendor(exclusions):
    """iSTYLE's entire catalogue carries vendor "Apple".  That is not a
    coincidence, so vendor context enables the match."""
    assert is_excluded("Apple", None, exclusions,
                       scopes=("international",),
                       allow_generic_word=True) is not None


def test_scope_filter_limits_matching(exclusions):
    """Lebanese groups must not be used for the international-vendor rule."""
    match = is_excluded("ABC", None, exclusions,
                        scopes=("international",), allow_generic_word=True)
    assert match is None or match["scope"] == "international"


# ============================================================
# THE LIST MUST NOT FAIL SILENTLY
# ============================================================

def test_missing_file_raises_instead_of_returning_empty(tmp_path):
    """The old loader swallowed FileNotFoundError and returned an empty
    blacklist, so running from the wrong directory silently disabled all
    brand filtering."""
    with pytest.raises(ExclusionsError):
        load_exclusions(tmp_path / "does_not_exist.json")


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ExclusionsError):
        load_exclusions(path)


def test_structurally_wrong_file_raises(tmp_path):
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps({"nope": []}), encoding="utf-8")
    with pytest.raises(ExclusionsError):
        load_exclusions(path)


def test_load_is_independent_of_working_directory(tmp_path, monkeypatch):
    """The original bug: a relative path meant the cwd decided whether
    brand filtering happened at all."""
    monkeypatch.chdir(tmp_path)
    loaded = load_exclusions()
    assert len(loaded["brands"]) > 0


# ============================================================
# NORMALIZATION HELPERS
# ============================================================

def test_normalize_strips_accents_and_punctuation():
    assert normalize_text("L'Oréal Paris") == "l oreal paris"
    assert normalize_text("Pull & Bear") == "pull bear"


def test_tokenize_splits_into_words():
    assert tokenize("Mike Sport ABC") == ["mike", "sport", "abc"]


def test_domain_labels_split_on_separators():
    assert domain_labels("lacoste-lb.com") == ["lacoste", "lb", "com"]
    assert domain_labels("lacoste.com.lb") == ["lacoste", "com", "lb"]


def test_case_is_normalized(exclusions):
    for variant in ("LACOSTE LEBANON", "lacoste lebanon", "LaCoStE Lebanon"):
        assert is_excluded(variant, None, exclusions) is not None
