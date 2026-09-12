"""Website URL normalisation.

The stored website must be the business's ROOT site, never the deep link
discovery happened to return.  Search results are overwhelmingly product
and collection pages, so this runs on almost every candidate.
"""

import pytest

import scraper


# ============================================================
# ROOT URL
# ============================================================

@pytest.mark.parametrize("discovered,expected", [
    ("https://www.example.com/shop", "https://www.example.com"),
    ("https://www.example.com/products/category/item",
     "https://www.example.com"),
    ("https://example.com/about-us", "https://example.com"),
    ("https://example.com", "https://example.com"),
    ("https://example.com/", "https://example.com"),
])
def test_root_url_drops_the_path(discovered, expected):
    assert scraper.root_url(discovered) == expected


def test_root_url_drops_query_and_fragment():
    assert scraper.root_url(
        "https://example.com/collections/all?sort=price#top"
    ) == "https://example.com"


def test_root_url_keeps_a_subdomain():
    """A subdomain can be a different storefront, so it is part of the
    business's address and must not be flattened away."""
    assert scraper.root_url("https://shop.example.com/products/x") == \
        "https://shop.example.com"


def test_root_url_keeps_a_myshopify_host():
    assert scraper.root_url(
        "https://zeina-handmade.myshopify.com/products/vase"
    ) == "https://zeina-handmade.myshopify.com"


def test_root_url_preserves_the_scheme():
    assert scraper.root_url("http://example.com/x").startswith("http://")
    assert scraper.root_url("https://example.com/x").startswith("https://")


def test_root_url_defaults_to_https_when_no_scheme_is_given():
    assert scraper.root_url("example.com/shop") == "https://example.com"


def test_root_url_is_idempotent():
    once = scraper.root_url("https://www.example.com/products/x")
    assert scraper.root_url(once) == once


# ============================================================
# DOMAIN (the deduplication key)
# ============================================================

@pytest.mark.parametrize("url,expected", [
    ("https://www.Example.com/path?x=1#frag", "example.com"),
    ("https://store.example.com/dresses", "store.example.com"),
    ("https://example.com:8443/shop", "example.com"),
    ("example.com/dresses", "example.com"),
])
def test_clean_url_returns_the_bare_host(url, expected):
    assert scraper.clean_url(url) == expected


def test_www_is_stripped_from_the_domain_but_kept_in_the_website():
    """The domain is the dedup key so 'www.' must not create a twin; the
    stored website keeps whatever the site actually uses."""
    assert scraper.clean_url("https://www.example.com/x") == "example.com"
    assert scraper.root_url("https://www.example.com/x") == \
        "https://www.example.com"


def test_the_same_store_on_two_paths_has_one_domain():
    a = scraper.clean_url("https://example.com/products/one")
    b = scraper.clean_url("https://example.com/collections/two")
    assert a == b


# ============================================================
# CLOSED-STOREFRONT DETECTION
# ============================================================
#
# Detection is deliberately narrow. Shopify answers 402 Payment Required
# for a suspended store, which needs no text matching at all. Text
# markers are read from the <title> ONLY: a live store (qatfalebanon.com)
# contains the word "password" in its body HTML, so body matching would
# throw away working shops.

class FakeResponse:
    def __init__(self, status_code=200, url="https://example.com/"):
        self.status_code = status_code
        self.url = url


@pytest.mark.parametrize("status", [401, 402, 403, 503])
def test_closed_status_codes_mean_unavailable(status):
    assert not scraper.store_is_available(
        FakeResponse(status), "<html><title>Shop</title></html>")


def test_402_is_the_shopify_suspended_signal():
    """Both dead stores in the live database answered 402."""
    assert not scraper.store_is_available(
        FakeResponse(402), "<html><title>Store unavailable</title></html>")


@pytest.mark.parametrize("title", [
    "Store unavailable",
    "This store is currently unavailable.",
    "Opening Soon",
    "Coming soon",
    "Account Suspended",
])
def test_closed_titles_mean_unavailable(title):
    assert not scraper.store_is_available(
        FakeResponse(200), f"<html><head><title>{title}</title></head></html>")


def test_a_password_protected_store_is_unavailable():
    assert not scraper.store_is_available(
        FakeResponse(200, "https://example.com/password"), "<html></html>")


def test_a_live_store_is_available():
    assert scraper.store_is_available(
        FakeResponse(200),
        "<html><head><title>Qatfa Lebanon | Fresh Delivery</title></head>"
        "</html>")


def test_the_word_password_in_the_body_does_not_close_a_live_store():
    """The regression this check was written around: qatfalebanon.com is
    open and its HTML contains 'password' (a login form)."""
    html = ('<html><head><title>Qatfa Lebanon</title></head><body>'
            '<form><input type="password" name="customer[password]">'
            '</form></body></html>')
    assert scraper.store_is_available(FakeResponse(200), html)


def test_a_product_named_coming_soon_does_not_close_a_store():
    """Markers are read from the title only, never the body."""
    html = ('<html><head><title>Byblos Ceramics</title></head><body>'
            '<h2>Coming soon: our winter collection</h2></body></html>')
    assert scraper.store_is_available(FakeResponse(200), html)


def test_missing_title_is_treated_as_available():
    assert scraper.store_is_available(FakeResponse(200), "<html></html>")
