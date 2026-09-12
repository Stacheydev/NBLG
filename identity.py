"""identity.py - deciding when two discovered records are the same business.

The audit found "Fattal Online" stored twice (rows 22 and 48) because the
old system treated a business as nothing more than its hostname string.
The two rows shared a name, an email, a phone number and an Instagram
handle, and the code saw two unrelated leads.

Confidence hierarchy: any ONE strong identifier is enough to merge, since
two businesses do not share a phone number or an Instagram account.  A
name match alone is deliberately NOT enough ("Beirut Bakery" is not a
unique business), so it requires corroboration.
"""

import re

from exclusions import normalize_text

# Shopify subdomains are a PLATFORM, not a company.  Using the registrable
# domain for these would collapse every myshopify.com store into a single
# business, so the full host is used instead.
PLATFORM_DOMAINS = (
    "myshopify.com", "shopifypreview.com", "wixsite.com",
    "squarespace.com", "bigcartel.com", "webflow.io",
)

NAME_SIMILARITY_THRESHOLD = 0.90


def normalize_phone(phone):
    """Digits only, with Lebanese prefixes unified.

    '+961 3 655 267', '009613655267' and '+9613655267' all become
    '9613655267'.
    """
    if not phone:
        return None
    digits = re.sub(r"\D", "", str(phone))
    if digits.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None
    # Local Lebanese format (03 655 267 / 3655267) -> add country code.
    if not digits.startswith("961"):
        local = digits.lstrip("0")
        if 7 <= len(local) <= 8:
            digits = "961" + local
    return digits or None


def normalize_email(email):
    if not email:
        return None
    email = str(email).strip().lower()
    return email if "@" in email else None


# Instagram paths that are not profiles.  A stored URL pointing at one
# of these identifies no business at all.
NON_PROFILE_INSTAGRAM_PATHS = {
    "p", "reel", "reels", "explore", "stories", "tv", "accounts",
    "direct", "about", "developer", "legal", "privacy",
}


def normalize_instagram(handle):
    """The comparable handle, from either a bare handle or a full URL.

    Instagram treats dots as insignificant for display purposes and
    handles are case-insensitive, so 'Curly.Square' == 'curlysquare'.

    Full URLs must be unwrapped here.  The database stores canonical
    profile URLs, and naively splitting one on '/' yields 'https:' for
    EVERY row - which makes every business look like the same business.
    """
    if not handle:
        return None

    text = str(handle).strip().lower()

    # Unwrap a profile URL down to its handle.
    if "instagram.com" in text:
        _, _, after = text.partition("instagram.com")
        text = after.lstrip("/")
    elif "://" in text:
        # Some other URL entirely - it identifies no Instagram profile.
        return None

    text = text.strip("/").split("/")[0].split("?")[0].split("#")[0]
    text = text.lstrip("@")

    if text in NON_PROFILE_INSTAGRAM_PATHS:
        return None

    text = re.sub(r"[^a-z0-9_.]", "", text)
    text = text.replace(".", "")
    return text or None


def is_platform_domain(domain):
    if not domain:
        return False
    host = domain.lower()
    return any(host == p or host.endswith("." + p) for p in PLATFORM_DOMAINS)


def registrable_domain(domain):
    """Best-effort registrable domain, without a public-suffix dependency.

    'shop.example.com'              -> 'example.com'
    'istyle.com.lb'                 -> 'istyle.com.lb'   (two-part TLD)
    'fattal-online.myshopify.com'   -> unchanged (platform host, see above)
    """
    if not domain:
        return None
    host = domain.lower().strip().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if is_platform_domain(host):
        return host

    parts = host.split(".")
    if len(parts) <= 2:
        return host

    # Handle second-level TLDs like .com.lb / .co.uk / .com.au
    second_level = {"com", "co", "net", "org", "gov", "edu", "ac"}
    if len(parts) >= 3 and parts[-2] in second_level and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# Shopify's default names carry no identity: four rows in the live
# database are called "My Store".  They must never match each other.
NON_IDENTIFYING_NAMES = {
    "my store", "my shop", "your store", "store name", "shopify store",
    "example store", "my new store", "test store", "demo store", "my",
}

# Values that mean "unknown" rather than a real attribute, so they cannot
# corroborate a name match.
UNKNOWN_VALUES = {"", "general", "unknown", "none", "other"}

MIN_IDENTIFYING_NAME_LEN = 4


def normalize_business_name(name):
    """Normalized name with country/store noise removed, so
    'Adaline Lebanon' and 'Adaline' compare equal.

    Returns None when nothing identifying survives - "My Store" reduces
    to "my", which is not a business identity.
    """
    normalized = normalize_text(name)
    if not normalized or normalized in NON_IDENTIFYING_NAMES:
        return None

    noise = {"lebanon", "lebanese", "leb", "lb", "online", "store", "shop",
             "official", "the", "co", "com"}
    tokens = [t for t in normalized.split() if t not in noise]
    stripped = " ".join(tokens) if tokens else normalized

    if stripped in NON_IDENTIFYING_NAMES:
        return None
    if len(stripped.replace(" ", "")) < MIN_IDENTIFYING_NAME_LEN:
        return None

    return stripped


def identifiers(record):
    """Strong identifiers for a lead record (dict-like)."""
    return {
        "domain": registrable_domain(record.get("domain")),
        "phone": normalize_phone(record.get("phone")),
        "whatsapp": normalize_phone(record.get("whatsapp")),
        "email": normalize_email(record.get("email")),
        "instagram": normalize_instagram(record.get("instagram")),
        "name": normalize_business_name(record.get("business_name")),
    }


def identity_key(record):
    """A stable primary key for a business.

    Prefers the registrable domain; falls back through the other strong
    identifiers so a record without a usable domain still gets a key.
    """
    ids = identifiers(record)
    for field in ("domain", "instagram", "email", "phone", "whatsapp"):
        if ids.get(field):
            return f"{field}:{ids[field]}"
    return f"name:{ids['name']}" if ids.get("name") else None


def _name_similarity(a, b):
    from difflib import SequenceMatcher
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def same_business(record_a, record_b):
    """Return (is_same, reason).

    ANY ONE strong identifier match is sufficient - two different
    businesses do not share a phone number, email address or Instagram
    account.  A similar NAME alone is not sufficient and must be
    corroborated by a matching city or industry, so unrelated businesses
    with generic names are never merged.
    """
    a, b = identifiers(record_a), identifiers(record_b)

    for field in ("domain", "email", "instagram"):
        if a.get(field) and a[field] == b.get(field):
            return True, f"same {field}: {a[field]}"

    # Phone and WhatsApp are the same identifier space - compare crosswise.
    a_phones = {a["phone"], a["whatsapp"]} - {None}
    b_phones = {b["phone"], b["whatsapp"]} - {None}
    shared = a_phones & b_phones
    if shared:
        return True, f"same phone: {sorted(shared)[0]}"

    # Name similarity needs corroboration before it can merge anything.
    # normalize_business_name() already returns None for placeholder and
    # degenerate names, so those never reach this branch.
    if a.get("name") and b.get("name"):
        similarity = _name_similarity(a["name"], b["name"])
        if similarity >= NAME_SIMILARITY_THRESHOLD:
            for field in ("city", "industry"):
                va = (record_a.get(field) or "").strip().lower()
                vb = (record_b.get(field) or "").strip().lower()
                # "general" is extract_industry()'s fallback for "nothing
                # matched" - it is an absence of information, so it cannot
                # corroborate anything.
                if va in UNKNOWN_VALUES or vb in UNKNOWN_VALUES:
                    continue
                if va == vb:
                    return True, (
                        f"name similarity {similarity:.2f} corroborated by {field}"
                    )

    return False, None


def find_duplicates(records):
    """Group records into clusters of the same business.

    Returns a list of clusters (each a list of records); clusters of one
    are omitted.  Intended for the retroactive pass over existing rows.
    """
    clusters = []
    for record in records:
        for cluster in clusters:
            if any(same_business(record, other)[0] for other in cluster):
                cluster.append(record)
                break
        else:
            clusters.append([record])
    return [c for c in clusters if len(c) > 1]
