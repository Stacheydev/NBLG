"""exclusions.py - the brand exclusion list, demoted to a SAFETY NET.

This is deliberately NOT the intelligence of the lead finder.  Scoring in
qualification.py does the real work; this list only catches the handful of
brands the signals miss.  It should shrink over time, not grow.

Fixes the four bugs the audit found in the old blacklist:

  * relative path      -> resolved against __file__, so the cwd cannot
                          silently disable brand filtering
  * missing file       -> raises ExclusionsError instead of quietly
                          behaving as if no brands were excluded
  * substring matching -> "Apple" matched "Pineapple", "Boss" matched
                          "Bossa Nova".  Matching is now on whole TOKENS
  * short brands dead  -> "ABC"/"TSC" could never match because entries
                          under 4 characters were compared for whole-string
                          equality only.  Token matching makes them work
"""

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

# Resolved against this file, never the current working directory.
EXCLUSIONS_FILE = Path(__file__).resolve().parent / "exclusions.json"

DEFAULT_TOKEN_SIMILARITY = 0.90
DEFAULT_MIN_FUZZY_LEN = 5


class ExclusionsError(RuntimeError):
    """The exclusion list is missing or unusable.

    Raised rather than returning an empty list: a silently disabled brand
    filter is worse than no brand filter, because nobody notices.
    """


def normalize_text(text):
    """Lowercase, strip accents, keep letters/digits, collapse whitespace.

    "L'Oréal Paris" -> "loreal paris",  "Pull & Bear" -> "pull bear"
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def tokenize(text):
    """Normalized text -> list of word tokens.  "Mike Sport ABC" ->
    ['mike', 'sport', 'abc']"""
    normalized = normalize_text(text)
    return normalized.split() if normalized else []


def domain_labels(domain):
    """Domain -> the label tokens it is built from.

    'lacoste.com.lb'   -> ['lacoste', 'com', 'lb']
    'lacoste-lb.com'   -> ['lacoste', 'lb', 'com']

    Splitting on separators (rather than substring scanning) is what stops
    'pineapple.com' from matching the brand 'Apple'.
    """
    if not domain:
        return []
    return [part for part in re.split(r"[^a-z0-9]+", domain.lower()) if part]


def _contains_sequence(haystack, needle):
    """True when `needle` appears as a run of consecutive whole tokens.

    ['mike','sport','abc'] contains ['mike','sport']  -> True
    ['pineapple','boutique'] contains ['apple']       -> False
    """
    if not needle or len(needle) > len(haystack):
        return False
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start:start + len(needle)] == needle:
            return True
    return False


def _fuzzy_token_match(tokens, alias_token, threshold, min_len):
    """Near-miss spellings for a single-word brand ('Lacosta' -> 'Lacoste').

    Only applied to aliases of at least `min_len` characters, so short
    words can never fuzzy-match anything.
    """
    if len(alias_token) < min_len:
        return False
    for token in tokens:
        if len(token) < min_len:
            continue
        if SequenceMatcher(None, token, alias_token).ratio() >= threshold:
            return True
    return False


def load_exclusions(path=None):
    """Load and validate exclusions.json.

    Raises ExclusionsError when the file is missing, unreadable, invalid
    JSON, or structurally wrong.  Callers are expected to let this abort
    the run.
    """
    path = Path(path) if path else EXCLUSIONS_FILE

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ExclusionsError(
            f"Exclusion list not found at {path}. Refusing to run with brand "
            f"filtering silently disabled."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ExclusionsError(
            f"Exclusion list at {path} is not valid JSON: {exc}"
        ) from exc
    except OSError as exc:
        raise ExclusionsError(f"Cannot read exclusion list at {path}: {exc}") from exc

    if not isinstance(data, dict) or not isinstance(data.get("brands"), list):
        raise ExclusionsError(
            f"Exclusion list at {path} must be an object with a 'brands' list."
        )

    settings = data.get("settings") or {}
    brands = []

    for entry in data["brands"]:
        if not isinstance(entry, dict) or not entry.get("name"):
            raise ExclusionsError(f"Invalid exclusion entry: {entry!r}")

        aliases = entry.get("aliases") or [entry["name"]]
        alias_tokens = [tokenize(alias) for alias in aliases]
        alias_tokens = [tokens for tokens in alias_tokens if tokens]

        if not alias_tokens:
            raise ExclusionsError(f"Entry {entry['name']!r} has no usable aliases.")

        brands.append({
            "name": entry["name"],
            "scope": entry.get("scope", "international"),
            "match": entry.get("match", "token"),
            "alias_tokens": alias_tokens,
            # "mike sport" -> "mikesport", so a glued domain label matches.
            "alias_joined": {"".join(tokens) for tokens in alias_tokens},
            "domains": [d.lower().strip() for d in entry.get("domains", [])],
        })

    return {
        "brands": brands,
        "token_similarity": float(
            settings.get("token_similarity", DEFAULT_TOKEN_SIMILARITY)
        ),
        "min_fuzzy_len": int(settings.get("min_fuzzy_len", DEFAULT_MIN_FUZZY_LEN)),
    }


def is_excluded(name, domain, exclusions, scopes=None, allow_generic_word=False):
    """Return the excluded brand entry matching name/domain, else None.

    Matching is whole-token only:
      "Mike Sport ABC"     matches "Mike Sport"
      "Pineapple Boutique" does NOT match "Apple"
      "Bossa Nova Beirut"  does NOT match "Boss"
      "ABC Verdun"         DOES match "ABC" (short names work again)

    Brands whose name is an ordinary English word ("Apple", "Target",
    "Mango", "Boss") are marked `"match": "generic_word"`.  Those never
    match a business NAME, because "Apple Orchard Farm" and "Target
    Fitness Lebanon" are plausible Lebanese SMEs.  They still match on
    domain, and on a products.json vendor when the caller passes
    allow_generic_word=True - a catalogue whose vendor really is "Apple"
    is an Apple reseller, not a coincidence.

    `scopes` optionally restricts to certain entry scopes, e.g.
    ("international",) when testing a product vendor.
    """
    if not exclusions:
        return None

    name_tokens = tokenize(name)
    labels = domain_labels(domain)
    threshold = exclusions.get("token_similarity", DEFAULT_TOKEN_SIMILARITY)
    min_len = exclusions.get("min_fuzzy_len", DEFAULT_MIN_FUZZY_LEN)

    for brand in exclusions.get("brands", []):
        if scopes and brand["scope"] not in scopes:
            continue

        # 1. Explicit domain match (registrable domain or any parent).
        if domain:
            host = domain.lower()
            for listed in brand["domains"]:
                if host == listed or host.endswith("." + listed):
                    return brand

        # A brand that is an ordinary word only matches names when the
        # caller vouches for the context (i.e. it is a product vendor).
        generic = brand["match"] == "generic_word"
        name_matchable = allow_generic_word or not generic

        # 2. Whole-token match on the business name.
        if name_matchable:
            for alias in brand["alias_tokens"]:
                if _contains_sequence(name_tokens, alias):
                    return brand

        # 3. Whole-label match on the domain: 'lacoste' in
        #    ['lacoste','com','lb'], or glued 'mikesport' as one label.
        for alias in brand["alias_tokens"]:
            if _contains_sequence(labels, alias):
                return brand
        if labels and brand["alias_joined"].intersection(labels):
            return brand

        # 4. Fuzzy, single-word aliases only, and only when the entry did
        #    not ask for strict exact-token matching.
        if brand["match"] == "token" and name_matchable:
            for alias in brand["alias_tokens"]:
                if len(alias) != 1:
                    continue
                if _fuzzy_token_match(name_tokens, alias[0], threshold, min_len):
                    return brand

    return None
