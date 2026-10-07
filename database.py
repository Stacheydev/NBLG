"""database.py - the single persistent lead store.

One SQLite file, created on first run and reused on every run after it.
There is exactly one canonical database and it is never per-run.

Two tables:

  leads           the product.  business_name / instagram_url / website,
                  plus the minimum identity metadata deduplication needs.

  rejected_sites  domains already analysed and REJECTED.  Not the lead
                  list: purely a cache, so repeat runs do not re-fetch
                  the same non-Shopify and foreign sites, which is most
                  of a run's work.  It holds no lead data and can be
                  deleted at any time with no effect beyond making the
                  next run slower.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import identity

# Resolved against this file so the database cannot silently be created
# somewhere else depending on the working directory.
DATABASE_NAME = str(Path(__file__).resolve().parent / "northbound_leads.db")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect():
    connection = sqlite3.connect(DATABASE_NAME)
    connection.row_factory = sqlite3.Row
    return connection


# ==========================
# SCHEMA
# ==========================

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    business_name  TEXT NOT NULL,
    instagram_url  TEXT,
    website        TEXT NOT NULL,
    domain         TEXT NOT NULL UNIQUE,
    identity_key   TEXT,
    aka_domains    TEXT,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rejected_sites (
    domain      TEXT PRIMARY KEY,
    result      TEXT NOT NULL,
    checked_at  TEXT NOT NULL
);
"""

# Columns added after the first release, so they cannot go in SCHEMA above:
# an existing database already has a `leads` table and CREATE TABLE IF NOT
# EXISTS will not alter it.  SQLite has no ADD COLUMN IF NOT EXISTS, so
# _migrate() checks PRAGMA table_info first.
#
# Every one is nullable with no default.  The leads generated before this
# feature existed stay NULL and read as "not scored" - they are deliberately
# NOT backfilled, which would mean re-fetching every site.
#
# The first three are the heuristic contact priority (see scoring.py).  The
# next six are the signals that CARRY WEIGHT in it.  The last five are
# recorded at weight zero so the score can be retuned against real outreach
# outcomes later without re-fetching anything.
LEAD_COLUMNS_ADDED = (
    ("contact_priority_score", "INTEGER"),
    ("contact_priority", "TEXT"),
    ("contact_priority_reasons", "TEXT"),

    ("instagram_followers", "INTEGER"),
    ("has_email", "INTEGER"),
    ("has_phone", "INTEGER"),
    ("has_whatsapp", "INTEGER"),
    ("dominant_share", "REAL"),
    ("lebanon_signal_tier", "TEXT"),

    ("industry", "TEXT"),
    ("city", "TEXT"),
    ("hreflang_count", "INTEGER"),
    ("has_sentry", "INTEGER"),
    ("has_store_locator", "INTEGER"),
)


def _migrate(connection):
    """Add any LEAD_COLUMNS_ADDED the `leads` table does not have yet.

    Additive only: no column is dropped, renamed or retyped, and no existing
    row is rewritten.  ADD COLUMN on SQLite is a metadata-only change, so
    this stays instant however many leads are stored.
    """
    existing = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(leads)")
    }

    for name, column_type in LEAD_COLUMNS_ADDED:
        if name not in existing:
            connection.execute(f"ALTER TABLE leads ADD COLUMN {name} {column_type}")


def create_database():
    """Create the database and its tables if they do not exist yet.

    Safe to call on every run: the schema uses CREATE TABLE IF NOT
    EXISTS, so an existing database is opened and reused untouched, and
    _migrate() only ever adds columns that are missing.
    """
    connection = _connect()
    connection.executescript(SCHEMA)
    _migrate(connection)
    connection.commit()
    connection.close()


# ==========================
# DEDUPLICATION (RULE 4)
# ==========================

def lead_exists(domain):
    """True when this exact domain is already stored."""
    if not domain:
        return False

    connection = _connect()
    row = connection.execute(
        "SELECT 1 FROM leads WHERE domain = ?", (domain,)
    ).fetchone()
    connection.close()

    return row is not None


def all_leads():
    connection = _connect()
    rows = [dict(r) for r in connection.execute(
        "SELECT * FROM leads ORDER BY id"
    )]
    connection.close()
    return rows


def find_existing_business(record, rows=None):
    """The stored lead representing the same business, or (None, None).

    This is what catches the same shop reached through a different
    domain - a custom domain and its .myshopify.com twin, for instance.
    """
    for row in (all_leads() if rows is None else rows):
        candidate = {
            "business_name": row.get("business_name"),
            "domain": row.get("domain"),
            "instagram": row.get("instagram_url"),
        }
        same, reason = identity.same_business(record, candidate)
        if same:
            return row, reason

    return None, None


def record_alias_domain(lead_id, domain):
    """Remember that a business is also reachable at another domain."""
    if not domain:
        return

    connection = _connect()
    row = connection.execute(
        "SELECT aka_domains, domain FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()

    if row is None:
        connection.close()
        return

    known = {d for d in (row["aka_domains"] or "").split(",") if d}
    if domain != row["domain"]:
        known.add(domain)

    connection.execute(
        "UPDATE leads SET aka_domains = ? WHERE id = ?",
        (",".join(sorted(known)), lead_id),
    )
    connection.commit()
    connection.close()


# ==========================
# THE CHECKED-SITES CACHE
# ==========================

def was_checked(domain):
    """True when this domain was already analysed and rejected."""
    if not domain:
        return False

    connection = _connect()
    row = connection.execute(
        "SELECT 1 FROM rejected_sites WHERE domain = ?", (domain,)
    ).fetchone()
    connection.close()

    return row is not None


def mark_checked(domain, result):
    if not domain:
        return

    connection = _connect()
    connection.execute(
        "INSERT OR REPLACE INTO rejected_sites VALUES (?, ?, ?)",
        (domain, result, _now()),
    )
    connection.commit()
    connection.close()


# ==========================
# WRITING A LEAD
# ==========================

def add_lead(business_name, instagram_url, website, domain, metrics=None):
    """Insert one qualified lead.  Returns True when a row was written.

    `metrics` is the optional contact-priority and signal record built by
    main.py from scoring.score() plus the signals already in hand.  Only keys
    named in LEAD_COLUMNS_ADDED are stored; anything else is ignored, so a
    caller cannot widen the row by accident.  Omitting it entirely writes the
    lead with those columns NULL, which is exactly what the pre-scoring
    callers and the existing tests do.
    """
    record = {
        "business_name": business_name,
        "domain": domain,
        "instagram": instagram_url,
    }

    allowed = [name for name, _ in LEAD_COLUMNS_ADDED]
    extra = {
        name: (metrics or {}).get(name)
        for name in allowed
        if (metrics or {}).get(name) is not None
    }

    columns = ["business_name", "instagram_url", "website", "domain",
               "identity_key", "aka_domains", "created_at", *extra]
    values = [business_name, instagram_url, website, domain,
              identity.identity_key(record), None, _now(), *extra.values()]
    placeholders = ", ".join("?" for _ in columns)

    connection = _connect()
    try:
        connection.execute(
            f"INSERT INTO leads ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values),
        )
        connection.commit()
        written = True
    except sqlite3.IntegrityError:
        written = False
    finally:
        connection.close()

    return written


def lead_count():
    connection = _connect()
    count = connection.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
    connection.close()
    return count
