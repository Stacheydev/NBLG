"""sync_to_supabase.py - mirror generated leads into Supabase.

A read-only companion to the generator, never part of it.  This script
does not discover, qualify, or judge anything: it copies rows the
generator has already committed into the online `public.leads` table.

    python sync_to_supabase.py --since-id 0      # every lead
    python sync_to_supabase.py --since-id 42     # only ids above 42
    python sync_to_supabase.py --since-id 0 --dry-run

`--since-id` is a high-water mark taken from `leads.id` BEFORE a
generator run.  That column is INTEGER PRIMARY KEY AUTOINCREMENT and
nothing in the generator deletes rows, so ids are monotonic and are never
reused - which is what makes "id > baseline" an exact description of one
run's output rather than an approximation.

Three properties this file is built around:

  read-only      SQLite is opened with mode=ro, so a write is not merely
                 absent from the code, it is refused by the driver.

  idempotent     Rows are upserted on `domain`, the one identifier the
                 local schema enforces as UNIQUE.  Re-running syncs the
                 same rows to the same place instead of duplicating them.

  additive       There is no DELETE path.  Rows in Supabase that are not
                 in SQLite are left alone.

The service-role key is read from the environment and is never printed,
logged, or written to disk - including in error messages.
"""

import argparse
import json
import os
import sqlite3

import requests

# Imported ONLY for DATABASE_NAME, so this script and the generator can
# never disagree about which file is the database.  Importing `database`
# has no side effects: it defines the path and its functions, and does not
# call create_database() or open a connection.
import database

TABLE = "leads"

# Exactly the columns public.leads has.  Declared once so the payload
# shape is stated in a single place rather than implied by the builder.
SUPABASE_FIELDS = (
    "domain", "business_name", "instagram_url", "website_url", "created_at",
)

# `id` is selected because it is the high-water mark the caller filters
# on; it is deliberately NOT sent to Supabase (see build_payload).
SELECT_NEW_LEADS = """
    SELECT id, business_name, instagram_url, website, domain, created_at
    FROM leads
    WHERE id > ?
    ORDER BY id ASC
"""

REQUEST_TIMEOUT = 30


def load_leads(since_id, db_path=None):
    """Leads with id > since_id, through a READ-ONLY connection.

    mode=ro is enforcement, not documentation: any INSERT/UPDATE/DELETE
    on this handle raises instead of succeeding.  It also refuses to open
    a database that still has a hot rollback journal beside it, so an
    unclean generator exit fails loudly here rather than being mirrored
    online as half-written state.
    """
    path = database.DATABASE_NAME if db_path is None else db_path

    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(SELECT_NEW_LEADS, (since_id,))
        ]
    finally:
        connection.close()


def build_payload(rows):
    """SQLite rows as Supabase records: exactly SUPABASE_FIELDS, always.

    PostgREST builds its ON CONFLICT DO UPDATE SET list from the union of
    keys across the whole batch, so a single row carrying an extra or
    missing key would silently change what gets written for every other
    row.  Every record therefore has all five keys.

    Three mappings are worth stating outright:

      website -> website_url   the only column whose name differs.

      instagram_url            passed through as None (JSON null) when
                               the generator found no account it trusted.
                               Never "" - an empty string would claim the
                               lead has a profile at no address.

      created_at               the generator's real discovery time, kept
                               verbatim.  Letting Supabase default this
                               would collapse every backfilled lead onto
                               the instant of the backfill and lose the
                               true ordering permanently.

    `id`, `identity_key` and `aka_domains` are absent on purpose: they are
    local bookkeeping for deduplication, not lead data, and `id` is only
    meaningful inside this one database file.
    """
    return [
        {
            "domain": row["domain"],
            "business_name": row["business_name"],
            "instagram_url": row["instagram_url"],
            "website_url": row["website"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def credentials():
    """(url, key) from the environment, or abort with a clear message.

    Read straight from os.environ - this script never reads, writes, or
    looks for a .env file.
    """
    url = (os.environ.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()

    missing = [
        name for name, value in (
            ("SUPABASE_URL", url),
            ("SUPABASE_SERVICE_ROLE_KEY", key),
        ) if not value
    ]
    if missing:
        raise SystemExit(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + "\nSet them in the environment before running. The "
              "service-role key must come from a secret store, never "
              "from a file in the repository."
        )

    return url, key


def upsert(payload, url, key):
    """One domain-keyed upsert.  Raises SystemExit on any non-2xx reply.

    resolution=merge-duplicates is what makes the INSERT an upsert on the
    `domain` primary key, so running this twice writes the same rows
    rather than failing or duplicating.  return=minimal keeps Supabase
    from echoing the whole batch back.
    """
    endpoint = f"{url}/rest/v1/{TABLE}?on_conflict=domain"

    response = requests.post(
        endpoint,
        json=payload,
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        timeout=REQUEST_TIMEOUT,
    )

    if not 200 <= response.status_code < 300:
        # The endpoint, the status and Supabase's own error body are what
        # make a failure diagnosable.  Request headers are deliberately
        # NOT reported: they carry the service-role key.
        raise SystemExit(
            "Supabase upsert failed.\n"
            f"  POST    {endpoint}\n"
            f"  status  {response.status_code} {response.reason}\n"
            f"  rows    {len(payload)}\n"
            f"  body    {response.text[:1000]}"
        )

    return response.status_code


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Upsert generated leads from the local SQLite database into "
            "the Supabase leads table, keyed on domain."
        ),
    )
    parser.add_argument(
        "--since-id",
        type=int,
        required=True,
        metavar="N",
        help=(
            "only sync leads whose id is greater than N. Use the value "
            "read from the database BEFORE the generator ran; 0 syncs "
            "every lead."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "print the payload that would be sent and stop. Makes no "
            "network request and needs no credentials."
        ),
    )
    args = parser.parse_args(argv)

    if args.since_id < 0:
        parser.error("--since-id must be 0 or greater")

    # Credentials are checked before the database is read so a
    # misconfigured run fails immediately.  A dry run sends nothing, so it
    # deliberately does not require them.
    url = key = None
    if not args.dry_run:
        url, key = credentials()

    rows = load_leads(args.since_id)
    payload = build_payload(rows)

    if not payload:
        print(f"No leads with id > {args.since_id}; nothing to sync.")
        return 0

    if args.dry_run:
        print(
            f"Dry run: {len(payload)} lead(s) with id > {args.since_id}. "
            "No request sent."
        )
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    status = upsert(payload, url, key)
    print(
        f"Upserted {len(payload)} lead(s) into {TABLE} "
        f"(HTTP {status}, on_conflict=domain)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
