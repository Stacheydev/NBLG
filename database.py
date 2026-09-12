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


def create_database():
    """Create the database and its tables if they do not exist yet.

    Safe to call on every run: the schema uses CREATE TABLE IF NOT
    EXISTS, so an existing database is opened and reused untouched.
    """
    connection = _connect()
    connection.executescript(SCHEMA)
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

def add_lead(business_name, instagram_url, website, domain):
    """Insert one qualified lead.  Returns True when a row was written."""
    record = {
        "business_name": business_name,
        "domain": domain,
        "instagram": instagram_url,
    }

    connection = _connect()
    try:
        connection.execute(
            "INSERT INTO leads (business_name, instagram_url, website, "
            "domain, identity_key, aka_domains, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (business_name, instagram_url, website, domain,
             identity.identity_key(record), None, _now()),
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
