"""Business identity resolution.

The live database contains "Fattal Online" twice (rows 22 and 48) on two
different domains, with an identical name, email, phone and Instagram
handle.  The old system compared hostnames, so it saw two businesses.
"""

import pytest

import identity


# Real values from northbound.db rows 22 and 48.
FATTAL_A = {
    "id": 22,
    "business_name": "Fattal Online",
    "domain": "fattal-online.myshopify.com",
    "email": "info@fattalonline.com",
    "phone": "+9613655267",
    "instagram": "fattalonline",
}
FATTAL_B = {
    "id": 48,
    "business_name": "Fattal Online",
    "domain": "fattalonline.com",
    "email": "info@fattalonline.com",
    "phone": "+9613655267",
    "instagram": "fattalonline",
}


def test_the_real_fattal_duplicate_is_detected():
    same, reason = identity.same_business(FATTAL_A, FATTAL_B)
    assert same
    assert reason


def test_find_duplicates_groups_the_fattal_rows():
    others = [
        {"id": 10, "business_name": "Qatfa Lebanon", "domain": "qatfalebanon.com"},
        {"id": 14, "business_name": "Istahly", "domain": "istahly.com"},
    ]
    clusters = identity.find_duplicates([FATTAL_A] + others + [FATTAL_B])
    assert len(clusters) == 1
    assert {r["id"] for r in clusters[0]} == {22, 48}


# ============================================================
# ANY ONE STRONG IDENTIFIER IS ENOUGH
# ============================================================

@pytest.mark.parametrize("field,value", [
    ("email", "hello@shop.com"),
    ("instagram", "shoplb"),
    ("phone", "+96170123456"),
])
def test_a_single_strong_identifier_merges(field, value):
    a = {"business_name": "Shop One", "domain": "one.com", field: value}
    b = {"business_name": "Totally Different", "domain": "two.com", field: value}
    assert identity.same_business(a, b)[0]


def test_phone_and_whatsapp_are_the_same_identifier_space():
    a = {"business_name": "A", "domain": "a.com", "phone": "+9613111222"}
    b = {"business_name": "B", "domain": "b.com", "whatsapp": "009613111222"}
    assert identity.same_business(a, b)[0]


def test_phone_normalization_handles_lebanese_formats():
    assert (identity.normalize_phone("+961 3 655 267")
            == identity.normalize_phone("009613655267")
            == identity.normalize_phone("+9613655267"))


def test_instagram_normalization_ignores_dots_and_case():
    assert identity.normalize_instagram("Curly.Square") == "curlysquare"
    assert identity.normalize_instagram("curly.square/") == "curlysquare"


# ============================================================
# UNRELATED BUSINESSES MUST NOT BE MERGED
# ============================================================

def test_unrelated_businesses_are_not_merged():
    a = {"business_name": "Beirut Bakery", "domain": "beirutbakery.com",
         "phone": "+9611111111"}
    b = {"business_name": "Beirut Books", "domain": "beirutbooks.com",
         "phone": "+9612222222"}
    assert not identity.same_business(a, b)[0]


def test_identical_names_alone_do_not_merge():
    """Two unrelated shops can share a name.  Without a matching city or
    industry there is nothing to justify a merge."""
    a = {"business_name": "Beirut Bakery", "domain": "one.com"}
    b = {"business_name": "Beirut Bakery", "domain": "two.com"}
    assert not identity.same_business(a, b)[0]


def test_similar_names_merge_when_corroborated():
    """Same brand, different domains, same city - safe to merge."""
    a = {"business_name": "Fattal Online", "domain": "one.com", "city": "Beirut"}
    b = {"business_name": "Fattal Online Store", "domain": "two.com", "city": "Beirut"}
    same, reason = identity.same_business(a, b)
    assert same
    assert "corroborated" in reason


def test_near_miss_names_stay_separate():
    """0.89 similarity is below the 0.90 threshold: deliberately
    conservative, because a wrong merge silently destroys a lead."""
    a = {"business_name": "Beirut Bakery", "domain": "one.com", "city": "Beirut"}
    b = {"business_name": "Beirut Bakerie", "domain": "two.com", "city": "Beirut"}
    assert not identity.same_business(a, b)[0]


def test_myshopify_stores_are_not_all_the_same_business():
    """The critical trap: treating *.myshopify.com as a registrable
    domain would collapse every Shopify store into one business."""
    a = {"business_name": "Store A", "domain": "store-a.myshopify.com"}
    b = {"business_name": "Store B", "domain": "store-b.myshopify.com"}
    assert not identity.same_business(a, b)[0]
    assert identity.registrable_domain("store-a.myshopify.com") == "store-a.myshopify.com"


def test_registrable_domain_handles_two_part_tlds():
    assert identity.registrable_domain("shop.example.com") == "example.com"
    assert identity.registrable_domain("istyle.com.lb") == "istyle.com.lb"
    assert identity.registrable_domain("www.example.com") == "example.com"


def test_subdomains_of_one_site_are_the_same_business():
    a = {"business_name": "Aishti", "domain": "shop.aishti.com"}
    b = {"business_name": "Aishti", "domain": "aishti.com"}
    assert identity.same_business(a, b)[0]


def test_identity_key_prefers_domain_then_falls_back():
    assert identity.identity_key({"domain": "shop.example.com"}) == "domain:example.com"
    assert identity.identity_key({"instagram": "shop.lb"}) == "instagram:shoplb"
    assert identity.identity_key({"business_name": "Nothing Else"}) is not None


def test_business_name_normalization_drops_country_noise():
    """'Adaline Lebanon' and 'Adaline' are the same brand."""
    assert (identity.normalize_business_name("Adaline Lebanon")
            == identity.normalize_business_name("Adaline"))


# ============================================================
# REGRESSIONS FOUND BY THE FIRST RETROACTIVE DRY RUN
# ============================================================

def test_placeholder_names_are_not_an_identity():
    """Four rows in the live database are called "My Store".  The first
    dry run merged two of them: "My Store" normalizes to "my", which
    matched, and both had industry "general", which appeared to
    corroborate it."""
    a = {"business_name": "My Store", "domain": "midnightss.myshopify.com",
         "city": "Beirut", "industry": "general", "instagram": "midnights"}
    b = {"business_name": "My Store", "domain": "faridresponds.myshopify.com",
         "industry": "general"}
    assert not identity.same_business(a, b)[0]


@pytest.mark.parametrize("name", ["My Store", "my store", "Shopify Store", "My Shop"])
def test_placeholder_names_normalize_to_nothing(name):
    assert identity.normalize_business_name(name) is None


def test_unknown_industry_cannot_corroborate_a_name_match():
    """'general' is extract_industry()'s fallback for "nothing matched" -
    an absence of information, not a shared attribute."""
    a = {"business_name": "Beirut Trading", "domain": "one.com",
         "industry": "general"}
    b = {"business_name": "Beirut Trading", "domain": "two.com",
         "industry": "general"}
    assert not identity.same_business(a, b)[0]


def test_real_industry_still_corroborates():
    a = {"business_name": "Beirut Trading", "domain": "one.com",
         "industry": "fashion"}
    b = {"business_name": "Beirut Trading", "domain": "two.com",
         "industry": "fashion"}
    assert identity.same_business(a, b)[0]


def test_very_short_names_are_not_an_identity():
    assert identity.normalize_business_name("AB") is None


# ============================================================
# THE STORED VALUE IS A URL, NOT A HANDLE
# ============================================================
#
# The database stores canonical profile URLs.  Naively splitting one on
# '/' yields 'https:' for every row, which made every business match
# every other business - a false merge that silently swallowed a real
# lead ('allbrandsfactoryoutlet.com' was absorbed into 'FromLebanon').

def test_a_profile_url_normalises_to_its_handle():
    assert identity.normalize_instagram(
        "https://www.instagram.com/qatfalebanon/"
    ) == "qatfalebanon"


def test_a_url_and_a_bare_handle_are_the_same_identifier():
    assert identity.normalize_instagram("https://www.instagram.com/Curly.Square/") \
        == identity.normalize_instagram("curly.square")


def test_two_different_profile_urls_are_different_identifiers():
    """The regression: every URL must not collapse to the same token."""
    a = identity.normalize_instagram("https://www.instagram.com/qatfalebanon/")
    b = identity.normalize_instagram("https://www.instagram.com/la2taa/")
    assert a != b
    assert "https" not in (a, b)


def test_two_businesses_with_different_instagram_urls_do_not_merge():
    one = {"business_name": "FromLebanon", "domain": "fromlebanon.co",
           "instagram": "https://www.instagram.com/fromlebanon.co/"}
    two = {"business_name": "All Brands Factory Outlet",
           "domain": "allbrandsfactoryoutlet.com",
           "instagram": "https://www.instagram.com/allbrandsfactoryoutlet/"}
    assert not identity.same_business(one, two)[0]


def test_the_same_business_on_two_domains_still_merges_via_url():
    one = {"business_name": "Qatfa Lebanon", "domain": "qatfalebanon.com",
           "instagram": "https://www.instagram.com/qatfalebanon/"}
    two = {"business_name": "Qatfa", "domain": "qatfa.myshopify.com",
           "instagram": "https://www.instagram.com/qatfalebanon/"}
    same, reason = identity.same_business(one, two)
    assert same and "instagram" in reason


@pytest.mark.parametrize("value", [
    "https://instagram.com/p/CxYzAbC/",
    "https://instagram.com/explore/tags/beirut",
    "https://instagram.com/reel/AbC123/",
    "https://example.com/about-us",
])
def test_non_profile_urls_identify_nothing(value):
    assert identity.normalize_instagram(value) is None
