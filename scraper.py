"""
scraper.py - Website discovery + analysis for the North Bound lead finder.

Responsibilities:
  * search the web for candidate stores (DuckDuckGo via the free `ddgs` lib)
  * fetch each candidate page ONCE and pull every signal we need:
      - is it Shopify? is it a real storefront?
      - is it a business in / shipping to Lebanon?
      - clean root URL + an accurate business name
      - contact details: email, phone, WhatsApp, Instagram
      - physical city in Lebanon + industry/category

Changed in this version:
  * clean_url()/root_url() always return the root domain (no subpages)
  * extract_business_name() pulls the real brand name from the page
    (title -> og:title -> meta description -> h1 -> header/footer) and
    filters out generic text like "Home", "Shop", "Products"
  * is_lebanon_business() now recognises Lebanese cities, ".lb" domains,
    "+961" phones, and "Lebanon" next to shipping/delivery/located/based
    words, while filtering out US towns named Lebanon (PA, TN, ...)
  * new: extract_whatsapp(), extract_city(), extract_industry()
"""

import json
import re
import time
from difflib import SequenceMatcher
from html import unescape
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
# ddgs never returns an empty list: a query with nothing left to give
# raises, and so does a timeout.  The two must be told apart, so the
# exception classes are imported rather than the messages matched.
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

# ============================================================
# SEARCH SETTINGS
# ============================================================

# Discovery queries, cheapest and highest-yield first.
#
# The original five saturated: by the third run 94% of what they returned
# was already in the database and a run saved nothing.  Queries are now
# ordered so a run that stops early (it found its target) has still spent
# its requests on the most productive ones, and the long tail below only
# runs when the good queries are exhausted.
KEYWORDS = [
    # --- platform-explicit: highest Shopify hit rate ---
    '"Lebanon" "powered by Shopify"',
    "site:myshopify.com Lebanon",
    "site:myshopify.com Beirut",
    '"Beirut" "powered by Shopify"',

    # --- commerce phrasing ---
    '"Lebanon" "shop now"',
    '"Lebanon" "online store"',
    '"Lebanon" "buy online"',
    '"Lebanon" "free delivery"',
    '"delivery all over Lebanon" shop',
    '"shipping within Lebanon" store',
    '"order online" Lebanon store',

    # --- Lebanese locale markers ---
    '"Lebanon" online store "LBP"',
    '"+961" "add to cart"',
    '"+961" online shop',

    # --- cities: local merchants often name their city ---
    '"Beirut" "online store"',
    '"Tripoli Lebanon" online shop',
    '"Saida" OR "Sidon" Lebanon online store',
    '"Jounieh" online store',
    '"Byblos" OR "Jbeil" online shop',
    '"Zahle" Lebanon online store',

    # --- categories where Lebanese SMEs cluster ---
    'Lebanon online store handmade',
    'Lebanon online store jewelry',
    'Lebanon online store fashion boutique',
    'Lebanon online store beauty skincare',
    'Lebanon online store electronics',
    'Lebanon online store home decor',
    'Lebanon online store kids toys',
    'Lebanon online store food gourmet',
]

MAX_RESULTS_PER_QUERY = 10

# How deep to read each query.  DuckDuckGo returns ten results a page and
# discovery used to read only the first, which is what exhausted the
# search: 28 queries x page 1 is a CLOSED pool of ~195 domains, and once
# the database knew ~90% of it a run could not find anything new no
# matter how often it ran.
#
# Measured before changing it: pages 2-5 of eight of these same queries
# exposed 208 domains absent from the database, against 20 from page 1 of
# all 28.  A sample of those deep-page domains qualified at 29% under the
# EXISTING rules - so the pool was the bottleneck, not the filters.
#
# 4 is deliberately modest: precision falls with depth (page 4 carries
# noticeably more foreign stores), and every page is a request that has
# to fit inside the workflow's generator timeout.
PAGES_PER_QUERY = 4

# Seconds to wait before the single retry of a failed search page.  Short
# because the failures measured were transient timeouts, not rate limits.
SEARCH_RETRY_BACKOFF = 2

HEADERS = {
    "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
}

# Hosts that can never be a real business candidate: search engines, ad
# platforms, social networks, wikis, etc. Filtering these out before the
# network check saves requests and keeps junk (e.g. Bing ad-click URLs)
# out of the results entirely.  (Unchanged - leave alone.)
IGNORED_HOSTS = {
    "bing.com", "google.com", "duckduckgo.com", "yahoo.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "linkedin.com", "tiktok.com", "wikipedia.org",
    "pinterest.com", "maps.google.com",
}

# Shopify detection (unchanged - it works fine, leave alone.)
SHOPIFY_SIGNALS = [
    "cdn.shopify.com",
    "shopify-section",
    "shopify-payment-button",
    "myshopify.com",
]

ECOMMERCE_SIGNALS = [
    "add to cart",
    "addtocart",
    "add-to-cart",
    "/cart",
    "checkout",
    "buy now",
    "shopping cart",
]

# ============================================================
# CONTACT PATTERNS (email/phone/WhatsApp/Instagram)
# ============================================================

EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9][a-zA-Z0-9._%+-]*@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
)
IGNORED_EMAIL_DOMAINS = {
    "sentry.io", "ingest.sentry.io", "wixpress.com", "example.com",
    "godaddy.com", "schema.org", "domain.com",
    # Shopify app vendors whose support address is embedded in themes and
    # is not the merchant's own contact (DB row 16 stored one of these).
    "gist-apps.com", "shopify.com", "sentry-cdn.com",
}

# Free mailbox providers.  A business using one is almost always small:
# no large brand publishes a gmail address as its contact.
FREE_EMAIL_DOMAINS = {
    "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "live.com",
    "icloud.com", "aol.com", "protonmail.com", "hotmail.fr", "yahoo.fr",
}
IGNORED_EMAIL_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif",
    ".css", ".js", ".map",   # CSS/JS noise like "name@11.css"
)

# wa.me/961... or whatsapp.com/send?phone=961...  ->  the digits
WHATSAPP_PATTERN = re.compile(
    r"(?:wa\.me/|whatsapp\.com/send\?phone=)(\d{8,15})"
)
# A plain Lebanese phone number in the page text (+961 / 00961 prefix)
PHONE_PATTERN = re.compile(
    r"(?:\+961|00961)[\s.-]?\d{1,2}[\s.-]?\d{3}[\s.-]?\d{3}"
)

INSTAGRAM_PATTERN = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)")
IGNORED_INSTAGRAM_PATHS = {
    "p", "reel", "reels", "explore", "accounts", "tv", "stories", "share",
    "direct", "about", "developer", "legal", "privacy",
}
# Instagram usernames: letters, digits, underscore, period; max 30 chars.
VALID_INSTAGRAM_HANDLE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.]{0,29}$")

# Platform / theme / app accounts that appear in Shopify markup but are
# never the merchant's own account.
NON_BUSINESS_INSTAGRAM_HANDLES = {
    "instagram", "shopify", "shopifyplus", "meta", "facebook", "klaviyo",
    "gorgias", "yotpo", "judgeme", "oberlo", "printful", "mailchimp",
    "tiktok", "youtube", "pinterest", "twitter", "linkedin", "whatsapp",
}

# Minimum contextual evidence before a handle is trusted as the
# business's own.  A footer/social link (+4) clears it on its own; a bare
# mention buried in the raw HTML (0) does not.
MIN_INSTAGRAM_CONFIDENCE = 3

# ============================================================
# QUALIFICATION SIGNAL PATTERNS
# ============================================================

HREFLANG_PATTERN = re.compile(r'hreflang="([a-zA-Z\-]+)"')

# Error monitoring implies a maintained engineering setup.  Weak on its
# own - it is only worth -1 in the score.
SENTRY_PATTERN = re.compile(r"sentry\.io|@sentry|sentry-cdn")

STORE_LOCATOR_PATTERN = re.compile(
    r"store locator|our stores|our branches|find a store|store finder|"
    r"/pages/(stores|locations|branches|our-stores|store-locator)"
)

# Legal-entity vocabulary, scanned over the CONTACT/FOOTER block only.
# Deliberately narrow, after measurement:
#   * "international" is excluded - "international shipping" appears on
#     perfectly ordinary small stores
#   * bare "holding" is excluded - it matched inside ordinary markup on
#     two legitimate SMEs (Qatfa, MJ BoardGames)
CORPORATE_TERMS = re.compile(
    r"\b(s\.a\.l\.?|s\.a\.r\.l\.?|holdings|franchisee|"
    r"group of companies|group sal)\b"
)

# Applied only to the business NAME and the products.json vendor, where
# "International"/"Group" really do indicate corporate scale.  Mike Sport's
# own vendor string is literally "Mike Sport International".
CORPORATE_NAME_TERMS = re.compile(
    r"\b(international|holding|holdings|group|s\.a\.l\.?|s\.a\.r\.l\.?|"
    r"franchise|corporation)\b",
    re.IGNORECASE,
)

# ============================================================
# LEBANON SIGNALS
# ============================================================

# Lebanese phone country code: +961 / 00961
LEBANON_PHONE_PATTERN = re.compile(r"(?:\+961|00961)")

# Well-known Lebanese cities.  Kept to names that are unlikely to appear
# inside unrelated English words (so "tyre"/"sour" are excluded).
LEBANESE_CITIES = [
    "beirut", "tripoli", "sidon", "saida", "jounieh", "jouneih",
    "zahle", "zahleh", "byblos", "jbeil", "jbail", "batroun",
    "nabatieh", "baabda", "achrafieh", "ashrafieh", "hamra",
    "gemmayzeh", "dbayeh", "antelias", "broummana", "bikfaya",
    "zgharta", "amchit", "faraya", "deir el qamar",
]

# "Lebanon" is only a *country* signal when it appears next to
# shipping / delivery / located / based words.  A bare mention
# ("Lebanon, PA") is usually the US town and is filtered out.
CONTEXT_NEAR_LEBANON = re.compile(
    r"\b(shipping|delivery|ship|deliver|located|based|dispatch)\b"
    r"[^.!?]{0,80}\blebanon\b",
    re.IGNORECASE,
)

# Known US false positives for the word "Lebanon".  There are US towns
# named Lebanon in many states (PA, TN, OH, IN, MO, NH, KY, ...), so we
# cover the common ones.  Two-letter codes "in" and "or" are left out
# because they are ordinary English words and would cause false negatives.
US_LEBANON_CONTEXT = re.compile(
    r"mt(\.)?\s*lebanon|"
    r"\blebanon\b[^.]{0,25}\b("
    r"pa|pennsylvania|tn|tennessee|ohio|oh|new york|ny|texas|tx|"
    r"missouri|mo|indiana|kentucky|ky|new hampshire|nh|oregon|"
    r"virginia|va|wisconsin|wi|kansas|ks|maine|new jersey|nj|"
    r"nebraska|ne|connecticut|ct|usa|us"
    r")\b",
    re.IGNORECASE,
)

# ============================================================
# BUSINESS NAME FILTERS (aggressive)
# ============================================================

# Words that can appear in a brand name but never make a name on their
# own.  If EVERY word in a candidate is one of these, the name is generic
# ("Fashion Lebanon", "Online Clothing Stores", "Home") and is rejected.
GENERIC_NAME_WORDS = {
    "home", "shop", "shops", "store", "stores", "online", "shopping",
    "products", "product", "collections", "collection", "welcome",
    "cart", "checkout", "search", "blog", "about", "contact", "login",
    "register", "account", "privacy", "terms", "service", "returns",
    "refund", "shipping", "policy", "faq", "new", "sale", "featured",
    "page", "not", "found", "error", "dresses", "dress", "jackets",
    "shoes", "bags", "accessories", "clothing", "clothes", "fashion",
    "lebanon", "beirut", "official", "site", "website", "catalog",
    "catalogue", "gallery", "lookbook", "reviews", "testimonials",
    "offers", "promotions", "men", "women", "kids", "baby", "best",
    "top", "buy", "shopify", "powered", "by", "wear", "apparel",
    "outfit", "browse", "promo", "free", "shipping", "worldwide",
    "delivery", "discount", "coupon", "code", "exchange", "guarantee",
    "warranty", "exclusive", "extra", "limited", "instant", "clearance",
    "clear", "offer",
    # single-word category labels ("Furniture", "Beauty", ...) are page
    # headings, not brand names - let the domain fallback provide one.
    "furniture", "electronics", "jewelry", "jewellery", "beauty",
    "toys", "food", "coffee", "gifts", "books", "art", "perfume",
    "skincare", "makeup", "cosmetics", "handbags", "watches", "homeware",
    "stationery", "sports", "fitness", "health", "pharmacy", "flowers",
    "candles", "decor", "automotive", "pets", "wine", "chocolate",
    "honey", "olive", "spices", "book", "official", "online",
    # common call-to-action words ("Shop now", "Learn more", "Sign up")
    "now", "here", "more", "click", "read", "learn", "get", "started",
    "subscribe", "sign", "up", "join", "order", "view", "today", "soon",
    "back", "news", "drop",
    # small stopwords - a real brand name needs more than these
    "the", "a", "an", "to", "in", "on", "at", "for", "with", "and",
    "of", "all", "our", "your", "we", "is", "are",
}

# Phrases that mean the page is an error / password page, never a brand.
ERROR_NAME_TERMS = (
    "currently unavailable", "unavailable", "not found", "error",
    "something went wrong", "looks like you", "maintenance",
    "under construction", "coming soon", "suspended", "access denied",
    "forbidden", "page cannot", "this page", "no products",
    "there are no", "oops", "sorry", "404", "503", "null",
    "store is currently", "the store is",
    # Bot-check / interstitial pages.  These are served INSTEAD of the
    # site, so their title is never a business name.  'ubuy.com.lb' was
    # stored as "Just a moment" because of this.
    "just a moment", "attention required", "checking your browser",
    "verify you are human", "please wait", "cloudflare",
    "enable javascript", "redirecting", "are you a robot",
    "security check", "ddos protection",
)

MAX_NAME_LEN = 50      # longer than this is a sentence, not a brand
MAX_NAME_WORDS = 6     # more than this is a description
MAX_NAME_PUNCT = 2     # more , ; : than this means it's a sentence

# Suffixes Shopify/Dawn themes commonly tack onto the <title>.  Stripped
# after the separator split so "Fashion Store | Online" -> "Fashion Store".
SHOPIFY_TITLE_SUFFIXES = (
    "| online store", "| shopify", " - shopify",
    "| powered by shopify", " - powered by shopify",
    "online store", "official store", "official website",
    "official online store", "homepage", "home page",
    "| buy online", " - buy online", "shopify",
    "| official site", "| lebanon", "| beirut",
    "| official website", " - official", "| official",
    "| online", " - online", "official", "online",
)

# ============================================================
# INDUSTRY / CATEGORY DETECTION
# ============================================================

# Each category has a list of words.  The category with the most matches
# in the visible page text (plus URL path + meta keywords) wins.
INDUSTRY_KEYWORDS = [
    ("fashion", [
        "dress", "fashion", "clothing", "apparel", "shirt", "skirt",
        "blouse", "jacket", "jeans", "couture", "wear", "abaya", "hijab",
        "modest", "trouser", "outfit",
    ]),
    ("jewelry & watches", [
        "jewelry", "jewellery", "jewel", "gold", "silver", "necklace",
        "bracelet", "earring", "ring", "diamond", "watch", "timepiece",
        "bijoux",
    ]),
    ("beauty & cosmetics", [
        "cosmetic", "skincare", "beauty", "makeup", "make-up", "perfume",
        "fragrance", "serum", "lotion", "haircare", "shampoo", "cream",
    ]),
    ("electronics", [
        "electronics", "phone", "mobile", "smartphone", "laptop",
        "computer", "gaming", "gadget", "camera", "headphone", "audio",
        "console", "appliance", "printer",
    ]),
    ("home & furniture", [
        "furniture", "home decor", "decor", "sofa", "cushion", "lamp",
        "kitchen", "bedding", "linen", "carpet", "rug", "vase", "curtain",
        "pillow",
    ]),
    ("food & grocery", [
        "grocery", "food", "supermarket", "snack", "coffee", "chocolate",
        "olive oil", "honey", "spice", "delicatessen", "cheese", "wine",
        "organic",
    ]),
    ("health & pharmacy", [
        "pharmacy", "vitamin", "supplement", "health", "wellness",
        "medical", "dental", "protein", "pharma",
    ]),
    ("sports & fitness", [
        "sport", "gym", "fitness", "yoga", "cycling", "running",
        "football", "soccer", "tennis", "workout", "outdoor", "hiking",
    ]),
    ("toys & kids", [
        "toy", "kids", "baby", "child", "stroller", "diaper", "lego",
        "puzzle", "plush",
    ]),
    ("books & stationery", [
        "book", "novel", "stationery", "publisher", "literature",
        "notebook", "reading",
    ]),
    ("art & crafts", [
        "art", "craft", "painting", "canvas", "handmade", "handicraft",
        "ceramic", "pottery", "sculpture",
    ]),
    ("pet supplies", [
        "pet", "dog", "cat", "aquarium", "veterinary", "leash",
    ]),
    ("automotive", [
        "car", "auto", "automotive", "vehicle", "tire", "spare part",
    ]),
    ("flowers & gifts", [
        "flower", "gift", "bouquet", "gift card", "chocolatier",
    ]),
]


# ============================================================
# EXTERNAL SIGNAL FETCHERS
# ============================================================
#
# Both of these are best-effort.  They return None (meaning "unknown")
# on any failure, and qualification.py treats unknown as NEUTRAL - never
# as grounds for rejection.  A lead must not be dropped because
# Instagram rate-limited us.

def fetch_products_json(url, timeout=12):
    """Read /products.json to find out WHOSE products the store sells.

    Shopify exposes this publicly on effectively every storefront (it
    returned HTTP 200 on 17/17 stores tested during the investigation).

    Returns {"available", "product_count", "vendors", "dominant_vendor",
    "dominant_share"} or None when unavailable.

    NOTE: product_count is recorded for diagnostics only.  It is NOT a
    qualification signal - measurement showed Mike Sport with 5 products
    and legitimate SMEs with 1000+, so catalogue size is not predictive
    of company size and is deliberately never scored.
    """
    root = root_url(url)

    try:
        response = requests.get(
            f"{root}/products.json?limit=250",
            headers=HEADERS,
            timeout=timeout,
        )
        if response.status_code != 200:
            return None
        data = response.json()
    except Exception:
        return None

    products = data.get("products") if isinstance(data, dict) else None
    if not isinstance(products, list) or not products:
        return None

    vendors = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        vendor = (product.get("vendor") or "").strip()
        if vendor:
            vendors[vendor] = vendors.get(vendor, 0) + 1

    if not vendors:
        return None

    total = sum(vendors.values())
    dominant_vendor, dominant_count = max(vendors.items(), key=lambda kv: kv[1])

    return {
        "available": True,
        "product_count": len(products),
        "vendors": vendors,
        "dominant_vendor": dominant_vendor,
        "dominant_share": dominant_count / total if total else 0.0,
    }


INSTAGRAM_FOLLOWER_JSON = re.compile(r'"edge_followed_by":\s*\{"count":\s*(\d+)\}')
INSTAGRAM_FOLLOWER_TEXT = re.compile(
    r'([\d][\d.,]*)\s*([KMkm]?)\s*[Ff]ollowers'
)


def _parse_follower_text(number, suffix):
    """'117K' -> 117000, '8,839' -> 8839, '9M' -> 9000000."""
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    multiplier = {"k": 1_000, "m": 1_000_000}.get(suffix.lower(), 1)
    return int(value * multiplier)


def fetch_instagram_followers(handle, timeout=15):
    """Public follower count for an Instagram handle, or None if unknown.

    Measurement during the investigation found a clean separation around
    100K followers between large Lebanese brands (Mike Sport 117K, Marie
    France 179K, Lacoste 9M) and legitimate SMEs (all <= 45K).

    Returns None - never 0 - for an invalid handle, a private or missing
    account, a rate-limited response, or an unexpected page format.  0 is
    a real measurement; None means "we do not know", and the two must not
    be confused by the scorer.
    """
    if not handle:
        return None

    handle = str(handle).strip().strip("/").split("/")[0].split("?")[0]

    if not handle or not VALID_INSTAGRAM_HANDLE.match(handle):
        return None

    if handle.lower() in IGNORED_INSTAGRAM_PATHS:
        return None

    try:
        response = requests.get(
            f"https://www.instagram.com/{handle}/",
            headers=HEADERS,
            timeout=timeout,
        )
    except Exception:
        return None

    if response.status_code != 200:
        return None

    match = INSTAGRAM_FOLLOWER_JSON.search(response.text)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None

    match = INSTAGRAM_FOLLOWER_TEXT.search(response.text)
    if match:
        return _parse_follower_text(match.group(1), match.group(2))

    return None


# ============================================================
# URL HELPERS
# ============================================================

def clean_url(url):
    """Return the bare root domain (hostname only) for deduplication.

    'https://www.Example.com/path?x=1#frag'  ->  'example.com'
    'https://store.example.com/dresses'      ->  'store.example.com'

    Subdomains are preserved; only a leading 'www.' is stripped, and any
    path / query / fragment is dropped.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()

    if not host:
        # Input had no scheme (e.g. 'example.com/dresses'): urlparse puts
        # the whole thing in `.path`.  Take the first path segment.
        host = (parsed.path or "").lower().split("/")[0]

    if host.startswith("www."):
        host = host[4:]

    if ":" in host:  # drop any explicit port, e.g. example.com:8080
        host = host.split(":")[0]

    return host


def root_url(url):
    """The business's root website - scheme + host, no path/query/fragment.

    'https://fashionstore.com/collections/dresses'
        -> 'https://fashionstore.com'
    'https://www.example.com/products/x'
        -> 'https://www.example.com'

    Unlike clean_url() this KEEPS a leading 'www.', because this value is
    the site's actual address and is what gets stored and visited.
    clean_url() strips it, because that value is the deduplication key
    and 'www.shop.com' and 'shop.com' are one business.
    """
    parsed = urlparse(url if "://" in url else "https://" + url)
    scheme = parsed.scheme or "https"

    host = parsed.netloc.lower()
    if not host:
        host = (parsed.path or "").lower().split("/")[0]
    if ":" in host:  # drop any explicit port
        host = host.split(":")[0]

    return f"{scheme}://{host}" if host else f"{scheme}://{clean_url(url)}"


# ============================================================
# SEARCH
# ============================================================

def is_ignored_host(url):
    host = urlparse(url).netloc.lower().replace("www.", "")

    return any(
        host == ignored or host.endswith("." + ignored)
        for ignored in IGNORED_HOSTS
    )


# A closed storefront is not a lead: there is no business to sell to
# today.  Detection is deliberately narrow, because a false positive
# throws away a real lead.
#
# Shopify answers 402 Payment Required for a suspended or unpaid store -
# that is the platform's own signal and needs no text matching.  The text
# markers below are only consulted for the <title>, never the whole page:
# a LIVE store (qatfalebanon.com) contains the word "password" in its
# HTML, so body-text matching would reject working shops.
CLOSED_STATUS_CODES = {401, 402, 403, 503}

CLOSED_TITLE_MARKERS = (
    "store unavailable",
    "currently unavailable",
    "this store is unavailable",
    "opening soon",
    "coming soon",
    "under construction",
    "store is closed",
    "temporarily closed",
    "account suspended",
)


def store_is_available(response, html):
    """False when the storefront is closed, suspended or not yet open."""
    if response.status_code in CLOSED_STATUS_CODES:
        return False

    # Shopify redirects a password-protected store to /password.
    final = str(getattr(response, "url", "") or "").lower()
    if final.rstrip("/").endswith("/password"):
        return False

    match = re.search(r"<title[^>]*>(.*?)</title>", html or "",
                      re.IGNORECASE | re.DOTALL)
    if match:
        title = match.group(1).strip().lower()
        if any(marker in title for marker in CLOSED_TITLE_MARKERS):
            return False

    return True


#: _search_page outcome meaning "this query has nothing left to give",
#: as distinct from an empty list, which means "this page failed twice".
#: A sentinel rather than None so the two cannot be confused by accident.
EXHAUSTED = object()


def _search_page(ddgs, keyword, page, max_results):
    """One page of search results.  Retried once, never raises.

    Returns the list of results, EXHAUSTED when the query has run out of
    pages, or [] when the page failed twice.

    ddgs signals "no more results" by raising rather than by returning an
    empty list, and a timeout raises too, so the two are told apart by
    class:

      TimeoutException / RatelimitException   transient - worth retrying
      DDGSException (the base class itself)   "No results found." - the
                                              query is out of pages, and
                                              retrying cannot change that
      anything else                           unexpected, so treated as
                                              transient: one retry, then
                                              give up on the page

    Retries are capped at exactly one, so a query costs at most
    2 x PAGES_PER_QUERY requests and a run can never loop here.
    """
    for attempt in (1, 2):
        try:
            return ddgs.text(keyword, max_results=max_results, page=page)
        except (TimeoutException, RatelimitException):
            pass  # transient - fall through to the retry
        except DDGSException:
            # The base class, i.e. not one of the two transient
            # subclasses above: this query is simply out of results.
            return EXHAUSTED
        except Exception:
            pass  # unknown failure, treated as transient

        if attempt == 1:
            time.sleep(SEARCH_RETRY_BACKOFF)

    return []


def discover(queries=None, max_results=None, on_query=None,
             pages=None):
    """Yield candidate sites one at a time, query by query.

    A generator rather than a list so a run can stop the moment it has
    found what it needs: with 28 queries a full sweep is a lot of
    requests, and most runs will hit their target long before the end.
    Queries are only sent as the consumer keeps asking.

    Each query is read to PAGES_PER_QUERY pages deep, in order, page 1
    first.  Pagination of a query stops early the moment that query
    reports no more results - there is nothing behind an exhausted page,
    and asking for page 4 of a query that ended at page 2 only wastes a
    request.  A page that FAILED twice is skipped instead, because a
    timeout says nothing about whether the next page has results.

    Each DOMAIN is yielded at most once per run.  Deduplication is on
    clean_url(), not on the raw URL: search results are overwhelmingly
    deep links, so one shop arrives as several URLs - /products/x,
    /collections/y, the homepage - and main.py reduces every one of them
    to the same domain before doing anything with it.  Keying on the URL
    spent candidate slots re-yielding shops already seen (15 of 210 in
    one measured sweep).
    """
    queries = list(queries if queries is not None else KEYWORDS)
    max_results = max_results or MAX_RESULTS_PER_QUERY
    pages = pages or PAGES_PER_QUERY
    seen = set()

    with DDGS() as ddgs:
        for keyword in queries:
            if on_query:
                on_query(keyword)

            for page in range(1, pages + 1):
                results = _search_page(ddgs, keyword, page, max_results)

                if results is EXHAUSTED:
                    break  # no more pages for this query

                for position, result in enumerate(results, start=1):
                    url = result.get("href")
                    title = result.get("title")

                    if not url or is_ignored_host(url):
                        continue

                    domain = clean_url(url)
                    if not domain or domain in seen:
                        continue

                    seen.add(domain)
                    yield {
                        "business": title,
                        "website": url,
                        "sources": [{"query": keyword, "position": position,
                                     "title": title, "page": page}],
                    }

                time.sleep(1)  # be polite to DDG between pages


def search_websites(queries=None, max_results=None):
    """Every candidate, as a list.  Thin wrapper over discover()."""
    return list(discover(queries=queries, max_results=max_results))


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze_site(url):
    """Fetch a candidate site once and pull every signal we need from it:
    Shopify detection, storefront check, Lebanon check, the real business
    name, and all contact/location/industry details.

    One network request, everything derived from that single response.

    The request always goes to the ROOT of the site, never to the deep
    link discovery happened to return.  Search results are usually
    product or collection pages, and analysing one of those was the
    root cause of two measured defects: the business name came out as a
    product name, and the Lebanon signals missed the contact details
    that live in the homepage footer.
    """
    url = root_url(url)

    result = {
        "is_shopify": False,
        "is_ecommerce": False,
        "is_lebanon": False,
        "business_name": None,
        "email": None,
        "phone": None,
        "whatsapp": None,
        "instagram": None,
        "city": None,
        "industry": None,
        # Qualification signals derived from the same single response.
        "lebanon_signals": {
            "strong": [], "claim": [], "medium": [], "us_conflict": False
        },
        "hreflang_count": 0,
        "has_sentry": False,
        "has_store_locator": False,
        "corporate_terms": [],
        "fetch_ok": False,
        "is_available": True,
    }

    try:
        # Some search URLs come back without a scheme - add https first.
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=10
        )

        html = response.text
        result["is_available"] = store_is_available(response, html)

    except Exception:
        # Any failure (timeout, DNS, SSL, ...) just returns the empty
        # result; the caller skips this site and moves on.
        return result

    result["fetch_ok"] = True

    soup = BeautifulSoup(html, "html.parser")
    lower_html = html.lower()

    # Signals that need the raw markup, taken before get_visible_text()
    # destroys the tree (it calls decompose() on script/style/head).
    result["hreflang_count"] = len(set(HREFLANG_PATTERN.findall(html)))
    result["has_sentry"] = bool(SENTRY_PATTERN.search(lower_html))
    result["has_store_locator"] = bool(STORE_LOCATOR_PATTERN.search(lower_html))

    contact_text = get_contact_text(soup)

    # Legal entity names live in the footer, not scattered through the
    # markup - scanning the whole page produced false positives.
    result["corporate_terms"] = sorted(
        set(match.group(0).lower() for match in CORPORATE_TERMS.finditer(contact_text))
    )

    # --- Shopify / storefront detection (unchanged) ---
    result["is_shopify"] = any(
        signal in lower_html
        for signal in SHOPIFY_SIGNALS
    )

    result["is_ecommerce"] = result["is_shopify"] or any(
        signal in lower_html
        for signal in ECOMMERCE_SIGNALS
    )

    # --- Contact details ---
    result["business_name"] = extract_business_name(soup, url)
    result["email"] = extract_email(html)
    result["phone"] = extract_phone(html)
    result["whatsapp"] = extract_whatsapp(html)
    result["instagram"] = extract_instagram(
        soup, html, result["business_name"], clean_url(url)
    )

    # Visible text (scripts/styles/head stripped) for text-only signals.
    visible_text = get_visible_text(soup)

    result["city"] = extract_city(visible_text)
    result["industry"] = extract_industry(url, soup, visible_text)

    # --- Lebanon detection (ranked signals, no bare-mention acceptance) ---
    result["lebanon_signals"] = lebanon_signals(
        url,
        lower_html,
        result["email"],
        result["instagram"],
        result["whatsapp"],
        contact_text,
        result["business_name"],
    )
    result["is_lebanon"] = passes_lebanon_gate(result["lebanon_signals"])

    return result


# ============================================================
# LEBANON DETECTION
# ============================================================

LEBANON_TOKEN = re.compile(r"(^|[^a-z])(lb|leb|lebanon|lebanese)([^a-z]|$)")


def lebanon_signals(url, lower_html, email, instagram, whatsapp,
                    contact_text, business_name=None):
    """Collect ranked evidence that this is a Lebanese business.

    Three tiers, returned as {"strong", "claim", "medium", "us_conflict"}:

      strong - registry- or telecom-backed, cannot be faked incidentally
      claim  - the merchant identifying ITSELF as Lebanese (domain, name,
               email address it chose).  A foreign store's country
               dropdown cannot produce one of these
      medium - suggestive, convincing only in pairs

    A bare "Lebanon" anywhere in the HTML is deliberately NOT a signal at
    any tier.  That was the old rule, and it accepted every foreign store
    whose checkout lists Lebanon among its shipping countries - which is
    how a Michigan food shop and a Chinese electronics store ended up in
    the leads table.
    """
    host = clean_url(url)
    strong = []
    claim = []
    medium = []

    # --- STRONG: registry / telecom backed ---
    if host.endswith(".lb"):
        strong.append("lb_domain")

    if LEBANON_PHONE_PATTERN.search(lower_html):
        strong.append("lebanese_phone")

    if whatsapp and re.sub(r"\D", "", whatsapp).startswith("961"):
        strong.append("lebanese_whatsapp")

    if email and email.lower().split("@")[-1].endswith(".lb"):
        strong.append("lb_email_domain")

    # --- CLAIM: deliberate self-identification ---
    # Domain says lebanon/lebanese - but not "mt-lebanon" (US town).
    if ("lebanon" in host or "lebanese" in host) and "mt-lebanon" not in host:
        claim.append("lebanon_in_domain")

    # The merchant put "lb"/"lebanon" in the mailbox it publishes, e.g.
    # "curlysquare.lb@gmail.com" or "moromartlebanon@gmail.com".
    if email and "@" in email:
        local_part = email.lower().split("@")[0]
        if LEBANON_TOKEN.search(local_part) or "lebanon" in local_part:
            claim.append("lb_in_email_local")

    # The business named itself "... Lebanon".  A US store with a country
    # dropdown does not do this.
    if business_name and re.search(
        r"\b(lebanon|lebanese|beirut)\b", business_name.lower()
    ):
        claim.append("lebanon_in_business_name")

    # --- MEDIUM: convincing in pairs, not alone ---
    if instagram:
        handle = instagram.lower()
        if re.search(r"(^|[._])(lb|leb|lebanon|lebanese)([._]|$)", handle):
            medium.append("lebanese_instagram_handle")

    # A Lebanese city inside a contact / address / footer block, NOT just
    # anywhere on the page (a blog post mentioning Beirut is not evidence).
    for city in LEBANESE_CITIES:
        if re.search(rf"\b{re.escape(city)}\b", contact_text):
            medium.append(f"city_in_contact_block:{city}")
            break

    if re.search(r"\blbp\b|lebanese pound", lower_html):
        medium.append("lbp_currency")

    if CONTEXT_NEAR_LEBANON.search(lower_html):
        medium.append("lebanon_shipping_context")

    return {
        "strong": strong,
        "claim": claim,
        "medium": medium,
        "us_conflict": bool(US_LEBANON_CONTEXT.search(lower_html)),
    }


def passes_lebanon_gate(signals):
    """Pass on one STRONG signal, one CLAIM, or two MEDIUM signals.

    An explicit US-Lebanon mention ("Lebanon, PA") vetoes anything that
    rests on claim or medium evidence, but cannot override a .lb domain
    or a +961 phone number, which no US business has.
    """
    if signals.get("strong"):
        return True
    if signals.get("us_conflict"):
        return False
    if signals.get("claim"):
        return True
    return len(signals.get("medium", [])) >= 2


# ============================================================
# BUSINESS NAME EXTRACTION
# ============================================================

def _iter_json_ld(soup):
    """Every JSON-LD object on the page, including nested @graph nodes."""
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue

        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                stack.extend(node.get("@graph", []) or [])
                yield node


# Schema.org types that name the BUSINESS rather than the current page.
# 'Product', 'CollectionPage', 'ItemList' and friends are deliberately
# absent: those name what is being sold, which is exactly the failure
# this extractor exists to prevent.
ORGANISATION_LD_TYPES = {
    "organization", "store", "localbusiness", "onlinestore",
    "corporation", "retailstore", "shoppingcenter",
}


def _json_ld_business_name(soup):
    """Business name from structured data, preferring Organization.

    Shopify themes emit an Organization (or Store) node describing the
    SHOP, and separately a Product/BreadcrumbList node describing the
    page.  Reading the former is the single most reliable identity
    signal on the page, which is why it is consulted first.
    """
    website_name = None

    for node in _iter_json_ld(soup):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        types = {str(t).lower() for t in types if t}

        name = node.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        if types & ORGANISATION_LD_TYPES:
            return name
        if "website" in types and website_name is None:
            website_name = name

    return website_name


def _logo_business_name(soup):
    """Brand text from the header logo only - never generic nav links.

    The previous version walked EVERY <a> in the header and footer, so a
    navigation label ("Shop By Lifestyle") could win.  Only elements that
    actually mark themselves as the site's logo/brand are considered.
    """
    for tag in soup.find_all(["header", "div", "a", "span", "h1"], limit=200):
        marker = " ".join(
            tag.get("class", []) + [tag.get("id") or ""]
        ).lower()
        if not marker:
            continue
        if not any(word in marker for word in
                   ("logo", "brand", "site-name", "shop-name", "store-name")):
            continue

        image = tag.find("img")
        if image and image.get("alt"):
            yield image["alt"]

        text = tag.get_text(" ", strip=True)
        if text:
            yield text


def extract_business_name(soup, url):
    """Find the store's real brand name.

    The ordering is the fix for a measured defect: candidates are
    discovered through deep links (`/products/...`, `/collections/...`),
    and on those pages `og:title` and `<title>` describe the PAGE, not
    the shop.  Ranking `og:title` second meant a product page stored the
    product's name as the business.

    Site-level identity signals therefore come first, and page-level ones
    are used only as a fallback:

      1. JSON-LD Organization / Store / LocalBusiness   (the shop itself)
      2. JSON-LD WebSite
      3. <meta property="og:site_name">                 (site, not page)
      4. <meta name="application-name">
      5. header logo / brand element                    (never nav links)
      6. <title>, brand-last                            (page-level)
      7. <meta property="og:title">                     (page-level)
      8. the domain name itself                         (always safe)
    """
    site_level = [
        _json_ld_business_name(soup),
    ]

    og_site = soup.find("meta", property="og:site_name")
    if og_site and og_site.get("content"):
        site_level.append(og_site["content"])

    app_name = soup.find("meta", attrs={"name": "application-name"})
    if app_name and app_name.get("content"):
        site_level.append(app_name["content"])

    site_level.extend(_logo_business_name(soup))

    for source in site_level:
        name = clean_business_name(source)
        if name:
            return name

    # Page-level fallbacks.  On a Shopify title the brand conventionally
    # trails the page name ("Blue Dress - Maison Zara"), so the LAST
    # segment is tried before the first.
    if soup.title and soup.title.string:
        name = clean_business_name(soup.title.string, prefer_last=True)
        if name:
            return name

    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        name = clean_business_name(og_title["content"], prefer_last=True)
        if name:
            return name

    # Last resort: the domain.  Always safe, never fabricated from text
    # that happened to be on the page.
    return name_from_domain(url)


def clean_business_name(raw, prefer_last=False):
    """Aggressively clean a raw title/og:title/logo string into a brand.

    - normalises whitespace, drops boilerplate prefixes ("Welcome to ")
    - splits on common separators and keeps the first part that passes
      validation, so "Dresses | Fashion Store" -> "Fashion Store"
      and "Fashion Lebanon, Online Clothing Stores" -> (None -> domain)
    - strips Shopify suffixes ("| Online Store", " - Shopify", ...)
    - returns None when nothing looks like a real brand name

    `prefer_last` reverses the segment order.  Shopify titles put the
    page first and the brand last ("Blue Dress - Maison Zara"), so when
    the string is a page-level title the trailing segment is the brand.
    """
    if not raw:
        return None

    # Decode entities BEFORE splitting.  BeautifulSoup decodes attribute
    # and element text, but NOT the contents of a <script> tag, so a name
    # arriving via JSON-LD still carries raw entities.  Splitting first
    # turned "RUSH &amp; REEZ" into "RUSH &amp" on the ';' separator.
    text = unescape(str(raw))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None

    if text.lower().startswith("welcome to "):
        text = text[11:].strip()

    # A whole string that is an error/interstitial page must never yield
    # a name from one of its fragments ("Just a moment..." -> "moment").
    ok, _reason = validate_business_name(text)
    if not ok and _reason in ("error message", "generic collection heading"):
        return None

    # Separators that usually split "brand | tagline" / "brand - tagline".
    parts = re.split(r"\s*[|»›·●◆▪–—\-:;,]\s*", text)
    if prefer_last:
        parts = list(reversed(parts))

    for part in parts:
        candidate = part.strip(" \t.,;:!?\"'«»")
        if not candidate:
            continue
        candidate = strip_shopify_suffix(candidate)
        if not candidate:
            continue
        ok, _reason = validate_business_name(candidate)
        if ok:
            return candidate

    return None


def validate_business_name(name):
    """Return (is_valid, reason).  Rejects the classic "not a brand" cases:
    error messages, names too long / with too many words / too much
    punctuation, and names made entirely of generic words."""
    if not name:
        return False, "empty"

    low = name.strip().lower()

    for term in ERROR_NAME_TERMS:
        if term in low:
            return False, "error message"

    if len(name) > MAX_NAME_LEN:
        return False, "too long"

    if len(name.split()) > MAX_NAME_WORDS:
        return False, "too many words"

    if sum(name.count(c) for c in ",;:") > MAX_NAME_PUNCT:
        return False, "too much punctuation"

    # Generic collection headings like "Shop by Lifestyle"
    if re.match(r"^(shop by|shop for|shop our|shop the|shop all|browse|all about)\b", low):
        return False, "generic collection heading"

    # Prose, not a brand.  A multi-word fragment with no capitalised word
    # in it is a sentence ("contact us today", "free shipping on all
    # orders"), which is what splitting a marketing <title> on its commas
    # tends to produce.  A single lowercase token is left alone, because
    # lowercase one-word brands are real ("lebanonshop").
    words = name.split()
    if len(words) > 1 and not any(w[:1].isupper() for w in words):
        return False, "prose"

    if is_all_generic(name):
        return False, "generic"

    return True, None


def is_all_generic(name):
    """True when EVERY word in the name is a generic term - e.g.
    "Online Clothing Stores" or "Fashion Lebanon".  A name like
    "The Good Store" is fine because "good" is not generic."""
    words = name.split()
    if not words:
        return True
    return all(
        w.strip(".,!?;:'\"()").lower() in GENERIC_NAME_WORDS
        for w in words
    )


def strip_shopify_suffix(name):
    """Remove trailing Shopify/Dawn boilerplate like '| Online Store'."""
    name = name.strip()
    lower = name.lower()

    for suffix in SHOPIFY_TITLE_SUFFIXES:
        if lower.endswith(suffix):
            name = name[: -len(suffix)].strip()
            lower = name.lower()

    return name or None


# Words used to split glued domain names into readable words:
# "fashionstore.com" -> "Fashion Store".  Matched longest-first so that
# "lebanon" is found before "ban" or similar partials.
KNOWN_DOMAIN_WORDS = (
    "electronics", "accessories", "timepieces", "jewellery",
    "furniture", "cosmetics", "skincare", "clothing", "lebanese",
    "fragrance", "boutique", "handmade", "official", "lebanon",
    "fashion", "jewelry", "kitchen", "grocery", "beauty", "online",
    "apparel", "sneakers", "flowers", "candles", "watches", "shoes",
    "store", "market", "gifts", "home", "shop", "wear", "kids",
    "baby", "food", "plus", "bags", "lb", "good", "house", "studio",
    "beirut", "lifestyle", "collection",
)


def _split_compound(token):
    """Split glued words like 'fashionstore' -> ['fashion', 'store']."""
    text = token
    for word in sorted(KNOWN_DOMAIN_WORDS, key=len, reverse=True):
        text = text.replace(word, " " + word + " ")
    return [w for w in text.split() if w]


def name_from_domain(url):
    """Last-resort brand name built from the domain, e.g.
    'fashionstore.com' -> 'Fashion Store',
    'clothing-lebanon.com' -> 'Clothing Lebanon',
    'qatfalebanon.com' -> 'Qatfa Lebanon'."""
    host = clean_url(url)

    parts = host.split(".")

    # Actual TLD / platform pieces that are never brand words.
    drop = {
        "com", "co", "net", "org", "io", "lb", "myshopify",
        "ae", "sa", "me", "us", "uk", "ca", "info",
    }
    # Tokens that are meaningless when they appear as a WHOLE domain part
    # (shop.example.com) but are kept when part of a word like
    # "fashionstore".
    prefix_drop = {"www", "store", "shop", "my"}

    words = []
    for part in parts:
        if part in drop or part in prefix_drop:
            continue
        for token in part.split("-"):
            token = token.strip()
            if not token:
                continue
            for word in _split_compound(token):
                if word and word not in drop:
                    words.append(word)

    if not words:  # nothing left (e.g. only TLDs) - use the raw host
        words = [host]

    return " ".join(w.capitalize() for w in words)


# ============================================================
# CONTACT EXTRACTION
# ============================================================

def extract_email(html):
    for match in EMAIL_PATTERN.findall(html):

        domain = match.split("@")[-1].lower()

        # Subdomain-aware: a Sentry DSN looks like
        # "<key>@o4506196830715904.ingest.us.sentry.io", which an exact
        # host comparison misses.  That is how a monitoring key ended up
        # stored as Marie France's contact email.
        if any(
            domain == ignored or domain.endswith("." + ignored)
            for ignored in IGNORED_EMAIL_DOMAINS
        ):
            continue

        if match.lower().endswith(IGNORED_EMAIL_SUFFIXES):
            continue

        return match

    return None


def extract_phone(html):
    """Plain Lebanese phone number found in the page text."""
    match = PHONE_PATTERN.search(html)

    if match:
        return match.group(0)

    return None


def extract_whatsapp(html):
    """WhatsApp number from a wa.me / whatsapp.com/send link."""
    match = WHATSAPP_PATTERN.search(html)

    if match:
        return "+" + match.group(1).lstrip("+")

    return None


def _normalize_handle_text(text):
    """'Curly.Square' / 'CurlySquare' -> 'curlysquare' for comparison."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _handle_matches_business(handle, business_name, domain):
    """How closely a handle resembles the business it should belong to.

    Returns 'exact', 'contains', 'similar' or None.  'mikesportleb'
    contains 'mikesport'; 'a.d.a.ybeauty' resembles nothing about
    "Mazen Online".
    """
    normalized = _normalize_handle_text(handle)
    if not normalized:
        return None

    targets = []
    if business_name:
        targets.append(_normalize_handle_text(business_name))
    if domain:
        # First label only: 'curlysquare.myshopify.com' -> 'curlysquare'
        targets.append(_normalize_handle_text(domain.split(".")[0]))

    for target in targets:
        if not target or len(target) < 4:
            continue
        if normalized == target:
            return "exact"
        if target in normalized or normalized in target:
            return "contains"
        if SequenceMatcher(None, normalized, target).ratio() >= 0.80:
            return "similar"

    return None


def _clean_handle(raw):
    """Take the account name out of an instagram.com URL fragment."""
    if not raw:
        return None

    handle = str(raw).strip().strip("/")
    handle = handle.split("?")[0].split("#")[0]
    handle = handle.split("/")[0]

    if not handle or not VALID_INSTAGRAM_HANDLE.match(handle):
        return None
    if handle.lower() in IGNORED_INSTAGRAM_PATHS:
        return None
    if handle.lower() in NON_BUSINESS_INSTAGRAM_HANDLES:
        return None

    return handle


def _social_context_score(tag):
    """Points for a link sitting where a business's own socials live."""
    score = 0
    evidence = []

    # rel="me" is the explicit "this account is mine" signal.
    rel = " ".join(tag.get("rel") or []).lower()
    if "me" in rel.split():
        score += 3
        evidence.append("rel=me")

    # The link's own attributes advertising it as a social link.
    attributes = " ".join(
        str(tag.get(attr) or "")
        for attr in ("class", "id", "aria-label", "title", "name")
    ).lower()
    if isinstance(tag.get("class"), list):
        attributes += " " + " ".join(tag.get("class")).lower()
    if re.search(r"instagram|social|follow", attributes):
        score += 3
        evidence.append("social link attributes")

    # An ancestor that is the footer/header or a social/contact block.
    for parent in tag.parents:
        name = getattr(parent, "name", None)
        if name in ("footer", "header", "nav"):
            score += 4
            evidence.append(f"inside <{name}>")
            break
        parent_attrs = ""
        if hasattr(parent, "get"):
            classes = parent.get("class")
            parent_attrs = " ".join(classes if isinstance(classes, list) else [])
            parent_attrs += " " + str(parent.get("id") or "")
        if re.search(r"social|footer|contact|follow", parent_attrs.lower()):
            score += 4
            evidence.append("inside social/footer block")
            break

    return score, evidence


def instagram_candidates(soup, html, business_name=None, domain=None):
    """Rank every Instagram handle on the page.

    Returns [(handle, score, evidence)] sorted by score descending, then
    by the order the handle first appeared, so the result is fully
    deterministic.

    Scored evidence, strongest first:
      +4  link sits in the footer/header/nav or a social block
      +3  link carries rel="me" or social/instagram attributes
      +3  handle appears in structured metadata (JSON-LD sameAs, og:see_also)
      +3  handle matches the business name or domain exactly / by containment
      +1  handle merely resembles the business name
       0  handle only appears loose in the raw HTML
    """
    candidates = {}
    order = []

    def register(handle, score, evidence, kind="context"):
        """Record evidence for a handle.

        Context scores are kept as a MAXIMUM, never summed.  An affiliate
        grid can repeat one influencer's handle dozens of times, and
        summing would let sheer repetition outrank the business's own
        account - which is exactly how 'a.d.a.ybeauty' beat 'mazenonline'
        on mazenonline.com.
        """
        if not handle:
            return
        key = handle.lower()
        if key not in candidates:
            candidates[key] = {
                "handle": handle, "context": 0, "structured": 0,
                "name": 0, "evidence": [],
            }
            order.append(key)
        entry = candidates[key]
        if kind == "structured":
            entry["structured"] = max(entry["structured"], score)
        else:
            entry["context"] = max(entry["context"], score)
        entry["evidence"].extend(evidence)

    # --- 1. Anchor tags, with their surrounding context ---
    for tag in soup.find_all("a", href=True):
        match = INSTAGRAM_PATTERN.search(tag["href"])
        if not match:
            continue
        handle = _clean_handle(match.group(1))
        if not handle:
            continue
        score, evidence = _social_context_score(tag)
        register(handle, score, evidence or ["plain link"])

    # --- 2. Structured metadata: JSON-LD sameAs ---
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text() or ""
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        for url in _walk_same_as(data):
            match = INSTAGRAM_PATTERN.search(str(url))
            if match:
                handle = _clean_handle(match.group(1))
                register(handle, 3, ["JSON-LD sameAs"], kind="structured")

    # --- 3. og:see_also / link rel=me ---
    for meta in soup.find_all("meta", property="og:see_also"):
        match = INSTAGRAM_PATTERN.search(str(meta.get("content") or ""))
        if match:
            register(_clean_handle(match.group(1)), 3, ["og:see_also"],
                     kind="structured")

    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "me" not in rel.split():
            continue
        match = INSTAGRAM_PATTERN.search(link["href"])
        if match:
            register(_clean_handle(match.group(1)), 3, ["link rel=me"],
                     kind="structured")

    # --- 4. Anything else in the raw HTML, with no context credit ---
    for raw in INSTAGRAM_PATTERN.findall(html or ""):
        register(_clean_handle(raw), 0, ["raw html mention"])

    # --- 5. Business-name resemblance applies to every candidate, once ---
    for entry in candidates.values():
        match_kind = _handle_matches_business(entry["handle"], business_name, domain)
        if match_kind in ("exact", "contains"):
            entry["name"] = 3
            entry["evidence"].append(f"name match ({match_kind})")
        elif match_kind == "similar":
            entry["name"] = 1
            entry["evidence"].append("name resemblance")

    for entry in candidates.values():
        entry["score"] = entry["context"] + entry["structured"] + entry["name"]

    ranked = sorted(
        (candidates[key] for key in order),
        key=lambda e: (-e["score"], order.index(e["handle"].lower())),
    )
    return [(e["handle"], e["score"], sorted(set(e["evidence"]))) for e in ranked]


def _walk_same_as(data):
    """Yield every sameAs URL found anywhere in a JSON-LD document."""
    if isinstance(data, dict):
        same_as = data.get("sameAs")
        if isinstance(same_as, str):
            yield same_as
        elif isinstance(same_as, list):
            for item in same_as:
                yield item
        for value in data.values():
            yield from _walk_same_as(value)
    elif isinstance(data, list):
        for item in data:
            yield from _walk_same_as(item)


def extract_instagram(soup, html, business_name=None, domain=None):
    """The business's OWN Instagram handle, or None if unsure.

    The old version returned the first instagram.com match in the raw
    HTML, which picked up whichever account happened to appear first -
    an influencer, a theme author, an app vendor.  Database row 15 stores
    'a.d.a.ybeauty' for a business called "Mazen Online" because of this.

    A wrong handle is worse than no handle: it feeds a follower count for
    somebody else's account straight into qualification.  So a candidate
    must reach MIN_INSTAGRAM_CONFIDENCE on contextual evidence, otherwise
    this returns None and the follower signal stays neutral.
    """
    ranked = instagram_candidates(soup, html, business_name, domain)
    if not ranked:
        return None

    handle, score, evidence = ranked[0]
    if score < MIN_INSTAGRAM_CONFIDENCE:
        return None

    # Ambiguity guard: if the runner-up ties the leader and nothing about
    # the business name distinguishes them, we genuinely cannot tell which
    # account belongs to the merchant.  Prefer no signal to a wrong one.
    if len(ranked) > 1 and ranked[1][1] == score:
        if not any(e.startswith("name match") for e in evidence):
            return None

    return handle


def instagram_url(handle_or_url):
    """Canonical profile URL for a handle, or None.

    Accepts either a bare handle ('curly.square') or a full URL, and
    always returns 'https://www.instagram.com/<handle>/'.  Tracking
    parameters, fragments and deep paths are dropped:

        'https://instagram.com/example/?igshid=abc123'
            -> 'https://www.instagram.com/example/'

    Returns None for anything that is not a usable profile handle, so a
    missing Instagram stays NULL instead of becoming a fabricated URL.
    This never invents a handle from the business name - the only input
    is what extract_instagram() actually found on the page.
    """
    if not handle_or_url:
        return None

    text = str(handle_or_url).strip()
    if not text:
        return None

    # Pull the handle out of a full URL if that is what we were given.
    if "instagram.com" in text.lower():
        path = urlparse(text if "//" in text else "https://" + text).path
        text = path.strip("/")
    else:
        text = text.strip("/")

    # First path segment only: '/example/reels' -> 'example'
    # Instagram handles are case-insensitive; store the canonical form so
    # 'Maison123.lebanon' and 'maison123.lebanon' are one URL.
    handle = text.split("/")[0].split("?")[0].split("#")[0]
    handle = handle.lstrip("@").strip().lower()

    if not handle or handle.lower() in IGNORED_INSTAGRAM_PATHS:
        return None
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle):
        return None
    if handle.strip(".") == "":
        return None

    return f"https://www.instagram.com/{handle}/"


# ============================================================
# CITY + INDUSTRY EXTRACTION
# ============================================================

def get_contact_text(soup):
    """Text from the footer / address / contact areas of the page only.

    The Lebanon gate uses this instead of the whole page so that a blog
    post mentioning Beirut, or a country dropdown listing Lebanon, is not
    mistaken for the business's own address.

    Called BEFORE get_visible_text(), which destroys the tree.
    """
    chunks = []

    for tag in soup.find_all(["footer", "address"]):
        chunks.append(tag.get_text(" ", strip=True))

    # Elements whose id/class advertises contact or location content.
    for tag in soup.find_all(
        attrs={"class": re.compile(r"(contact|address|location|footer)", re.I)}
    ):
        chunks.append(tag.get_text(" ", strip=True))
    for tag in soup.find_all(
        attrs={"id": re.compile(r"(contact|address|location|footer)", re.I)}
    ):
        chunks.append(tag.get_text(" ", strip=True))

    return re.sub(r"\s+", " ", " ".join(chunks)).lower()


def get_visible_text(soup):
    """All visible page text (scripts/styles/head removed), lowercased."""
    for tag in soup(["script", "style", "noscript", "svg", "head"]):
        tag.decompose()
    return soup.get_text(" ", strip=True).lower()


CITY_CONTEXT_PATTERN = re.compile(
    r"\b(located in|based in|address|store at|visit us|find us|"
    r"delivering in|delivering to)\b[^.]{0,60}",
    re.IGNORECASE,
)


def extract_city(visible_text):
    """Physical city in Lebanon, best-guessed from the page text.

    Prefers an explicit "located in <City>" / "based in <City>" mention,
    otherwise returns the first Lebanese city name that appears anywhere.
    """
    # Prefer explicit location phrasing.
    for match in CITY_CONTEXT_PATTERN.finditer(visible_text):
        chunk = match.group(0)
        for city in LEBANESE_CITIES:
            if city in chunk:
                return city.title()

    # Fallback: first Lebanese city mentioned anywhere on the page.
    for city in LEBANESE_CITIES:
        if re.search(rf"\b{re.escape(city)}\b", visible_text):
            return city.title()

    return None


def extract_industry(url, soup, visible_text):
    """Best-guess industry from visible text + URL collection path + meta
    keywords.  'fashion' for dresses/clothing, 'food & grocery' for food,
    and so on.  Returns 'general' when nothing scores."""
    scores = {name: 0 for name, _ in INDUSTRY_KEYWORDS}

    path = urlparse(url).path.lower()

    meta_kw = ""
    tag = soup.find("meta", attrs={"name": "keywords"})
    if tag and tag.get("content"):
        meta_kw = str(tag["content"]).lower()

    for name, keywords in INDUSTRY_KEYWORDS:
        for kw in keywords:
            pattern = re.compile(rf"\b{re.escape(kw)}\b")
            # path + keywords weigh double: a /collections/dresses URL is
            # a much stronger signal than one stray word in the text.
            scores[name] += len(pattern.findall(visible_text))
            scores[name] += len(pattern.findall(path)) * 2
            scores[name] += len(pattern.findall(meta_kw)) * 2

    best_name, best_score = "general", 0

    for name, score in scores.items():
        if score > best_score:
            best_name, best_score = name, score

    return best_name
