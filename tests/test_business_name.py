"""Business-name extraction.

Candidates are discovered through deep links - `/products/...`,
`/collections/...` - and every regression below is a real shape that
put the wrong name into the database:

    'Just a moment'      a Cloudflare interstitial   (ubuy.com.lb)
    'Shop By Lifestyle'  a navigation label          (livgood.com)
    'Lebanon Collection' a collection title          (scentsofpalestine)

The rule the extractor follows: site-level identity beats page-level
text, and when nothing identifies the site, fall back to the domain
rather than to whatever text happened to be on the page.
"""

import pytest
from bs4 import BeautifulSoup

import scraper


def name_of(html, url="https://example-lb.com/"):
    return scraper.extract_business_name(BeautifulSoup(html, "html.parser"), url)


# ============================================================
# SITE IDENTITY BEATS PAGE TITLE
# ============================================================

def test_product_page_does_not_store_the_product_name():
    """The headline defect: og:title on a product page is the product."""
    html = (
        '<html><head><title>Blue Summer Dress - Maison Zara LB</title>'
        '<meta property="og:title" content="Blue Summer Dress">'
        '</head><body></body></html>'
    )
    assert name_of(html, "https://maisonzara.com/products/blue-dress") == \
        "Maison Zara LB"


def test_json_ld_organization_wins():
    html = (
        '<html><head><title>All Products | The Brand</title>'
        '<meta property="og:title" content="All Products">'
        '<script type="application/ld+json">'
        '{"@type":"Organization","name":"Cedar Home Beirut"}'
        '</script></head><body></body></html>'
    )
    assert name_of(html) == "Cedar Home Beirut"


def test_json_ld_store_inside_a_graph_is_found():
    html = (
        '<html><head><title>Some Product</title>'
        '<script type="application/ld+json">'
        '{"@graph":[{"@type":"Product","name":"Some Product"},'
        '{"@type":"Store","name":"Zaatar Co"}]}'
        '</script></head><body></body></html>'
    )
    assert name_of(html) == "Zaatar Co"


def test_a_product_node_alone_is_never_the_business_name():
    """JSON-LD Product describes the item, not the shop."""
    html = (
        '<html><head><title>Handmade Soap | Saida Naturals</title>'
        '<script type="application/ld+json">'
        '{"@type":"Product","name":"Handmade Olive Soap"}'
        '</script></head><body></body></html>'
    )
    assert name_of(html) == "Saida Naturals"


def test_og_site_name_beats_the_page_title():
    html = (
        '<html><head><meta property="og:site_name" content="Beirut Bikes">'
        '<title>Red Mountain Bike | Beirut Bikes</title>'
        '</head><body></body></html>'
    )
    assert name_of(html) == "Beirut Bikes"


def test_malformed_json_ld_does_not_crash_extraction():
    html = (
        '<html><head><script type="application/ld+json">{not json}</script>'
        '<title>Byblos Ceramics</title></head><body></body></html>'
    )
    assert name_of(html) == "Byblos Ceramics"


# ============================================================
# PAGE FURNITURE IS NEVER A BUSINESS NAME
# ============================================================

def test_navigation_label_is_not_a_business_name():
    """'Shop By Lifestyle' is in the live database for livgood.com."""
    html = (
        '<html><head><title>LivGood</title></head><body>'
        '<header><a href="/">Shop By Lifestyle</a>'
        '<a href="/c">Shop All</a></header></body></html>'
    )
    assert name_of(html, "https://livgood.com/") == "LivGood"


def test_a_generic_nav_link_never_wins_over_the_domain():
    """With no site identity at all, the domain beats nav text."""
    html = (
        '<html><head></head><body><header>'
        '<a href="/">Shop By Lifestyle</a></header></body></html>'
    )
    url = "https://byblosceramics.com/"
    assert name_of(html, url) == scraper.name_from_domain(url)


def test_header_logo_element_is_used():
    html = (
        '<html><head><title>Widget</title></head><body>'
        '<header><a class="site-header__logo">Qadmous Goods</a></header>'
        '</body></html>'
    )
    assert name_of(html, "https://qadmous.com/products/x") == "Qadmous Goods"


def test_logo_image_alt_text_is_used():
    html = (
        '<html><head><title>Blue Dress</title></head><body>'
        '<div class="header__logo"><img alt="Tripoli Textiles" src="/l.png">'
        '</div></body></html>'
    )
    assert name_of(html, "https://tripolitex.com/products/x") == \
        "Tripoli Textiles"


# ============================================================
# INTERSTITIALS AND ERROR PAGES
# ============================================================

@pytest.mark.parametrize("title", [
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "Checking your browser before accessing",
    "Please wait...",
    "Access denied",
    "404 Not Found",
])
def test_interstitial_titles_fall_back_to_the_domain(title):
    """'Just a moment' is in the live database for ubuy.com.lb."""
    url = "https://byblosceramics.com/"
    html = f"<html><head><title>{title}</title></head><body></body></html>"
    name = name_of(html, url)
    assert name == scraper.name_from_domain(url)
    for junk in ("moment", "cloudflare", "denied", "404", "wait"):
        assert junk not in name.lower()


def test_an_empty_page_falls_back_to_the_domain():
    assert name_of("<html></html>", "https://qatfalebanon.com/") == \
        "Qatfa Lebanon"


# ============================================================
# CLEANING
# ============================================================

def test_shopify_suffixes_are_stripped():
    html = ('<html><head><meta property="og:site_name" '
            'content="Adaline | Online Store"></head></html>')
    assert name_of(html) == "Adaline"


def test_welcome_to_prefix_is_stripped():
    html = ('<html><head><meta property="og:site_name" '
            'content="Welcome to Klaptap"></head></html>')
    assert name_of(html) == "Klaptap"


def test_a_sentence_is_not_a_business_name():
    html = (
        '<html><head><title>We ship all over Lebanon within 48 hours, '
        'contact us today</title></head></html>'
    )
    url = "https://saidasweets.com/"
    assert name_of(html, url) == scraper.name_from_domain(url)


def test_names_are_whitespace_normalised():
    html = ('<html><head><meta property="og:site_name" '
            'content="  Cedar   Crafts  "></head></html>')
    assert name_of(html) == "Cedar Crafts"


def test_prose_is_not_a_business_name():
    """A lowercase multi-word fragment is a sentence, not a brand.  This
    is what splitting a marketing title on its commas produces."""
    for prose in ("contact us today", "free shipping on all orders",
                  "we deliver across lebanon"):
        ok, reason = scraper.validate_business_name(prose)
        assert not ok and reason == "prose", prose

    # A lowercase one-word brand is still valid ('lebanonshop' is real).
    assert scraper.validate_business_name("lebanonshop")[0]


# ============================================================
# HTML ENTITIES
# ============================================================
#
# BeautifulSoup decodes attribute and element text, but NOT the contents
# of a <script> tag - so a name arriving via JSON-LD still carries raw
# entities. Splitting before decoding turned "RUSH &amp; REEZ" into
# "RUSH &amp", which is what got stored for rushandreez.com.

def test_an_ampersand_entity_in_json_ld_is_decoded():
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Organization","name":"RUSH &amp; REEZ"}'
        '</script></head></html>'
    )
    assert name_of(html) == "RUSH & REEZ"


def test_an_ampersand_entity_in_og_site_name_is_decoded():
    html = ('<html><head><meta property="og:site_name" '
            'content="Bed &amp; Bath Beirut"></head></html>')
    assert name_of(html) == "Bed & Bath Beirut"


def test_a_numeric_entity_is_decoded():
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Store","name":"Caf&#233; Younes"}'
        '</script></head></html>'
    )
    assert name_of(html) == "Café Younes"


def test_an_entity_name_is_never_truncated_at_the_semicolon():
    """The regression: ';' is a name separator, so an undecoded entity
    was split in half."""
    for html_name, expected in [
        ("Salt &amp; Pepper", "Salt & Pepper"),
        ("Rose &amp; Thyme LB", "Rose & Thyme LB"),
    ]:
        html = (f'<html><head><meta property="og:site_name" '
                f'content="{html_name}"></head></html>')
        got = name_of(html)
        assert got == expected, got
        assert "amp" not in got
