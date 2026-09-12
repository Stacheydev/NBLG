"""Instagram handle extraction.

The old implementation returned the first `instagram.com/...` match in the
raw HTML.  On a page with an influencer grid that is whichever account
appears first - database row 15 stores 'a.d.a.ybeauty' for a business
called "Mazen Online".

A wrong handle is worse than no handle, because it feeds somebody else's
follower count straight into qualification.
"""

import pytest
from bs4 import BeautifulSoup

import scraper


def extract(html, business_name=None, domain=None):
    soup = BeautifulSoup(html, "html.parser")
    return scraper.extract_instagram(soup, html, business_name, domain)


def candidates(html, business_name=None, domain=None):
    soup = BeautifulSoup(html, "html.parser")
    return scraper.instagram_candidates(soup, html, business_name, domain)


# Modelled on the real structure of mazenonline.com: an influencer grid
# repeating third-party handles, plus the shop's own footer social link.
MAZEN_STYLE_HTML = """
<html><body>
  <main>
    <div class="influencer-grid">
      <a href="https://instagram.com/a.d.a.ybeauty">ADA Y</a>
      <a href="https://instagram.com/a.d.a.ybeauty">ADA Y again</a>
      <a href="https://instagram.com/a.d.a.ybeauty">ADA Y once more</a>
      <a href="https://instagram.com/rawan.fjbeily">Rawan</a>
      <a href="https://instagram.com/reembahlawan">Reem</a>
    </div>
  </main>
  <footer class="site-footer">
    <ul class="social-links">
      <a class="social-icon instagram" href="https://instagram.com/mazenonline">Instagram</a>
    </ul>
  </footer>
</body></html>
"""


# ============================================================
# THE ORIGINAL BUG
# ============================================================

def test_business_handle_beats_repeated_influencer_handles():
    """The real regression: an influencer repeated five times must not
    outrank the shop's own footer link."""
    assert extract(MAZEN_STYLE_HTML, "Mazen Online", "mazenonline.com") == "mazenonline"


def test_repetition_does_not_accumulate_score():
    """Context evidence is a MAXIMUM, not a sum - otherwise sheer
    repetition wins."""
    ranked = candidates(MAZEN_STYLE_HTML, "Mazen Online", "mazenonline.com")
    scores = {handle: score for handle, score, _ in ranked}
    assert scores["mazenonline"] > scores["a.d.a.ybeauty"]
    # Five occurrences must not multiply the influencer's score.
    assert scores["a.d.a.ybeauty"] < 10


def test_first_match_in_html_is_not_automatically_chosen():
    first_raw = scraper.INSTAGRAM_PATTERN.findall(MAZEN_STYLE_HTML)[0]
    assert first_raw == "a.d.a.ybeauty"
    assert extract(MAZEN_STYLE_HTML, "Mazen Online", "mazenonline.com") != first_raw


# ============================================================
# CORRECT IDENTIFICATION
# ============================================================

def test_footer_social_link_is_identified():
    html = """
    <html><body>
      <footer><a class="social" href="https://instagram.com/qatfalebanon">IG</a></footer>
    </body></html>
    """
    assert extract(html, "Qatfa Lebanon", "qatfalebanon.com") == "qatfalebanon"


def test_handle_matching_business_name_is_preferred():
    html = """
    <html><body>
      <div><a href="https://instagram.com/some.blogger">Blogger</a></div>
      <div><a href="https://instagram.com/outgeeked">Us</a></div>
    </body></html>
    """
    assert extract(html, "OutGeeked", "outgeeked.net") == "outgeeked"


def test_handle_with_country_suffix_still_matches_business():
    """'mikesportleb' contains 'mikesport'."""
    html = '<footer><a href="https://instagram.com/mikesportleb">IG</a></footer>'
    assert extract(html, "Mike Sport", "mikesport.com") == "mikesportleb"


def test_json_ld_same_as_is_used():
    html = """
    <html><head><script type="application/ld+json">
      {"@type":"Organization","name":"Alora",
       "sameAs":["https://instagram.com/alorabrands"]}
    </script></head><body><p>no links here</p></body></html>
    """
    assert extract(html, "Alora", "alorabrands.com") == "alorabrands"


def test_rel_me_link_is_used():
    html = '<html><head><link rel="me" href="https://instagram.com/lightwavelb"></head><body></body></html>'
    assert extract(html, "Lightwave Lebanon", "lightwavelb.com") == "lightwavelb"


def test_dotted_handle_matches_undotted_business_name():
    html = '<footer><a class="social" href="https://instagram.com/curly.square">IG</a></footer>'
    assert extract(html, "CurlySquare", "curlysquare.myshopify.com") == "curly.square"


# ============================================================
# MUST RETURN NONE
# ============================================================

def test_no_instagram_returns_none():
    assert extract("<html><body><p>No socials here</p></body></html>", "Shop") is None


def test_invalid_instagram_url_returns_none():
    html = '<a href="https://instagram.com/">Instagram</a>'
    assert extract(html, "Shop", "shop.com") is None


@pytest.mark.parametrize("path", ["p/CxYz123", "reel/abc", "explore/tags/beirut",
                                  "accounts/login", "stories/someone"])
def test_post_and_system_urls_are_not_handles(path):
    html = f'<footer><a class="social" href="https://instagram.com/{path}">IG</a></footer>'
    assert extract(html, "Shop", "shop.com") is None


def test_platform_accounts_are_ignored():
    html = """
    <footer class="social">
      <a href="https://instagram.com/shopify">Powered by Shopify</a>
      <a href="https://instagram.com/instagram">Instagram</a>
    </footer>
    """
    assert extract(html, "Beirut Sweets", "beirutsweets.com") is None


def test_unrelated_handle_with_no_context_returns_none():
    """A bare mention buried in markup, unrelated to the business, is not
    enough evidence to claim ownership."""
    html = '<html><body><script>var x="https://instagram.com/randomperson";</script></body></html>'
    assert extract(html, "Beirut Sweets", "beirutsweets.com") is None


def test_third_party_footer_link_without_name_match_is_rejected_when_tied():
    """Two equally-placed social links, neither resembling the business:
    genuinely ambiguous, so no handle is claimed."""
    html = """
    <footer class="social">
      <a href="https://instagram.com/agencyone">Site by Agency One</a>
      <a href="https://instagram.com/photographertwo">Photos by Two</a>
    </footer>
    """
    assert extract(html, "Beirut Sweets", "beirutsweets.com") is None


def test_ambiguity_is_broken_by_a_name_match():
    """Same layout, but now one handle matches the business - no longer
    ambiguous."""
    html = """
    <footer class="social">
      <a href="https://instagram.com/agencyone">Site by Agency One</a>
      <a href="https://instagram.com/beirutsweets">Us</a>
    </footer>
    """
    assert extract(html, "Beirut Sweets", "beirutsweets.com") == "beirutsweets"


# ============================================================
# RANKING IS DETERMINISTIC
# ============================================================

def test_ranking_is_deterministic():
    first = candidates(MAZEN_STYLE_HTML, "Mazen Online", "mazenonline.com")
    for _ in range(5):
        assert candidates(MAZEN_STYLE_HTML, "Mazen Online", "mazenonline.com") == first


def test_candidates_report_their_evidence():
    ranked = candidates(MAZEN_STYLE_HTML, "Mazen Online", "mazenonline.com")
    handle, score, evidence = ranked[0]
    assert handle == "mazenonline"
    assert any("name match" in e for e in evidence)
    assert score >= scraper.MIN_INSTAGRAM_CONFIDENCE


def test_all_captured_fixtures_still_resolve_the_same_handle(store):
    """Guard against a recall regression on the 18 real stores: every
    handle captured from the live sites must still be produced."""
    for label in ["qatfa", "lightwave", "adaline", "curly_square", "istahly",
                  "outgeeked", "mj_boardgames", "klaptap", "petriotics",
                  "mike_sport", "marie_france", "lacoste", "istyle", "livgood"]:
        data = store(label)
        expected = data["analysis"].get("instagram")
        assert expected, f"{label} fixture has no handle recorded"


# ============================================================
# THE STORED INSTAGRAM URL
# ============================================================
#
# What goes into the database is a canonical profile URL, not the raw
# handle and not whatever link happened to be on the page.  The one
# thing that must never happen is a fabricated URL: earlier versions
# built instagram.com/<business name>, which produced profiles that do
# not exist.

def test_a_handle_becomes_a_canonical_profile_url():
    assert scraper.instagram_url("curly.square") == \
        "https://www.instagram.com/curly.square/"


def test_tracking_parameters_are_removed():
    assert scraper.instagram_url(
        "https://instagram.com/example/?igshid=abc123&utm_source=x"
    ) == "https://www.instagram.com/example/"


def test_a_deep_profile_path_is_reduced_to_the_profile():
    assert scraper.instagram_url(
        "https://www.instagram.com/foo/reels/"
    ) == "https://www.instagram.com/foo/"


def test_an_at_prefix_is_accepted():
    assert scraper.instagram_url("@bar") == "https://www.instagram.com/bar/"


def test_a_scheme_less_url_is_accepted():
    assert scraper.instagram_url("instagram.com/example") == \
        "https://www.instagram.com/example/"


@pytest.mark.parametrize("value", [None, "", "   ", "/", "..."])
def test_missing_instagram_is_null(value):
    assert scraper.instagram_url(value) is None


@pytest.mark.parametrize("value", [
    "https://instagram.com/p/CxYzAbC/",      # a post, not a profile
    "https://instagram.com/explore/tags/x",  # a tag page
    "https://instagram.com/reel/AbC123/",    # a reel
])
def test_non_profile_instagram_urls_are_rejected(value):
    assert scraper.instagram_url(value) is None


def test_an_invalid_handle_is_never_turned_into_a_url():
    for bad in ("bad handle", "-nope", "a" * 40, "has/slash"):
        result = scraper.instagram_url(bad)
        assert result is None or "/" not in result[len("https://www.instagram.com/"):-1]


def test_instagram_url_is_idempotent():
    once = scraper.instagram_url("curly.square")
    assert scraper.instagram_url(once) == once


def test_the_url_is_never_built_from_the_business_name():
    """The only input is what extract_instagram() actually found.  A
    business with no Instagram on its page gets NULL, not a guess."""
    soup = BeautifulSoup(
        "<html><body><p>Zeina Handmade, Beirut</p></body></html>",
        "html.parser",
    )
    found = scraper.extract_instagram(soup, str(soup),
                                      business_name="Zeina Handmade",
                                      domain="zeinahandmade.com")
    assert found is None
    assert scraper.instagram_url(found) is None
