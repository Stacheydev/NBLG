"""The Lebanon gate.

The old rule accepted a bare "Lebanon" anywhere in the HTML, so any
foreign store whose checkout lists Lebanon among its shipping countries
qualified as Lebanese.  That is how a Michigan food shop and a Chinese
electronics store ended up in the leads table.
"""

from scraper import lebanon_signals, passes_lebanon_gate, get_contact_text
from bs4 import BeautifulSoup


def signals_for(url="https://example.com", html="", email=None,
                instagram=None, whatsapp=None, contact_text="", name=None):
    return lebanon_signals(url, html.lower(), email, instagram, whatsapp,
                           contact_text.lower(), name)


# ============================================================
# THE HOLE THAT LET FOREIGN BUSINESSES IN
# ============================================================

def test_bare_lebanon_mention_is_not_enough():
    signals = signals_for(html="<p>We love Lebanon and its food.</p>")
    assert not passes_lebanon_gate(signals)


def test_country_dropdown_listing_lebanon_is_not_enough():
    html = """
    <select name="country">
      <option>Jordan</option><option>Kuwait</option>
      <option>Lebanon</option><option>Oman</option>
    </select>
    """
    assert not passes_lebanon_gate(signals_for(html=html))


def test_foreign_store_shipping_to_lebanon_is_not_enough():
    html = "<p>We ship worldwide including Lebanon, Jordan and Egypt.</p>"
    signals = signals_for(url="https://usstore.com", html=html)
    # "shipping ... lebanon" is a single MEDIUM signal - not sufficient alone.
    assert not passes_lebanon_gate(signals)


def test_us_lebanon_town_is_rejected():
    html = "<p>Visit our store in Lebanon, PA 17042</p>"
    signals = signals_for(html=html)
    assert signals["us_conflict"]
    assert not passes_lebanon_gate(signals)


def test_us_conflict_cannot_override_a_lebanese_phone():
    """A +961 number is telecom-backed; a stray "Lebanon, PA" string in a
    blog post must not veto it."""
    html = "<p>Call +961 1 234 567</p><p>also Lebanon, PA</p>"
    signals = signals_for(html=html)
    assert "lebanese_phone" in signals["strong"]
    assert passes_lebanon_gate(signals)


# ============================================================
# STRONG SIGNALS - each sufficient alone
# ============================================================

def test_lb_domain_is_strong():
    signals = signals_for(url="https://shop.example.com.lb")
    assert "lb_domain" in signals["strong"]
    assert passes_lebanon_gate(signals)


def test_lebanese_phone_is_strong():
    signals = signals_for(html="<p>+961 3 456 789</p>")
    assert "lebanese_phone" in signals["strong"]
    assert passes_lebanon_gate(signals)


def test_lebanese_whatsapp_is_strong():
    signals = signals_for(whatsapp="+9613111222")
    assert "lebanese_whatsapp" in signals["strong"]
    assert passes_lebanon_gate(signals)


def test_foreign_whatsapp_is_not_a_lebanon_signal():
    """A Chinese number (+86) is in the live database as a false positive."""
    signals = signals_for(whatsapp="+8615922995145")
    assert "lebanese_whatsapp" not in signals["strong"]
    assert not passes_lebanon_gate(signals)


def test_lb_email_domain_is_strong():
    signals = signals_for(email="info@istyle.com.lb")
    assert "lb_email_domain" in signals["strong"]


# ============================================================
# CLAIM SIGNALS - the merchant identifying itself
# ============================================================

def test_lebanon_in_domain_is_a_claim():
    signals = signals_for(url="https://qatfalebanon.com")
    assert "lebanon_in_domain" in signals["claim"]
    assert passes_lebanon_gate(signals)


def test_mt_lebanon_is_not_a_claim():
    """mt-lebanon is a Pennsylvania town."""
    signals = signals_for(url="https://mt-lebanon-books.com")
    assert "lebanon_in_domain" not in signals["claim"]


def test_lb_in_email_local_part_is_a_claim():
    """Curly Square publishes curlysquare.lb@gmail.com - the .lb is in the
    mailbox, not the domain."""
    signals = signals_for(email="curlysquare.lb@gmail.com")
    assert "lb_in_email_local" in signals["claim"]
    assert passes_lebanon_gate(signals)


def test_lebanon_in_business_name_is_a_claim():
    signals = signals_for(name="Adaline Lebanon")
    assert "lebanon_in_business_name" in signals["claim"]


def test_claim_is_vetoed_by_us_conflict():
    signals = signals_for(
        url="https://lebanonstore.com",
        html="<p>Our shop in Lebanon, Tennessee</p>",
    )
    assert signals["claim"]
    assert not passes_lebanon_gate(signals)


# ============================================================
# MEDIUM SIGNALS - only convincing in pairs
# ============================================================

def test_one_medium_signal_is_not_enough():
    signals = signals_for(instagram="shop.lb")
    assert len(signals["medium"]) == 1
    assert not passes_lebanon_gate(signals)


def test_two_medium_signals_pass():
    signals = signals_for(
        instagram="shop.lb",
        html="<p>we deliver across lebanon</p>",
    )
    assert len(signals["medium"]) >= 2
    assert passes_lebanon_gate(signals)


def test_city_is_only_counted_inside_a_contact_block():
    """A blog post mentioning Beirut is not evidence of an address."""
    body_only = signals_for(html="<p>A history of Beirut architecture</p>")
    assert not any(s.startswith("city_in_contact") for s in body_only["medium"])

    in_footer = signals_for(contact_text="Our shop: Hamra Street, Beirut")
    assert any(s.startswith("city_in_contact") for s in in_footer["medium"])


def test_get_contact_text_scopes_to_footer_and_contact_blocks():
    html = """
    <html><body>
      <main><p>An article about Beirut nightlife</p></main>
      <footer>Visit us in Jounieh, Lebanon</footer>
    </body></html>
    """
    text = get_contact_text(BeautifulSoup(html, "html.parser"))
    assert "jounieh" in text
    assert "nightlife" not in text
