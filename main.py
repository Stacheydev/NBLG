"""main.py - the NorthBound lead finder.

    python main.py

Searches for Lebanese Shopify stores, keeps the ones worth approaching,
and saves them to northbound_leads.db.  No arguments, no CSVs, no manual steps.

A run keeps searching until it has saved TARGET_NEW_LEADS new businesses
or has exhausted its queries, whichever comes first.
"""

import time

import database
import qualification
import scraper
from exclusions import ExclusionsError, load_exclusions

# How many NEW leads one run tries to find before it stops.  Discovery is
# lazy, so a run that reaches this early simply stops querying.
TARGET_NEW_LEADS = 10


def main():
    print("NorthBound Lead Finder")
    print("======================")
    print()

    database.create_database()

    # The exclusion list is the large-brand filter.  A silently empty
    # brand filter is worse than no brand filter, so a broken list aborts
    # the run instead of quietly passing every brand through.
    try:
        exclusions = load_exclusions()
    except ExclusionsError as exc:
        print(f"Cannot run: {exc}")
        return 1

    print(f"Searching for up to {TARGET_NEW_LEADS} new leads...")
    print()

    discovered = 0
    analysed = 0
    counts = {
        qualification.NOT_SHOPIFY: 0,
        qualification.STORE_CLOSED: 0,
        qualification.OUTSIDE_LEBANON: 0,
        qualification.LARGE_BRAND: 0,
        qualification.PLACEHOLDER: 0,
    }
    already_known = 0
    unreachable = 0
    saved = 0

    for candidate in scraper.discover():
        discovered += 1

        url = candidate["website"]
        domain = scraper.clean_url(url)

        # Already a lead, or already analysed and rejected on an earlier
        # run.  Either way there is nothing to fetch.
        if database.lead_exists(domain) or database.was_checked(domain):
            already_known += 1
            continue

        try:
            analysis = scraper.analyze_site(url)
        except Exception:
            unreachable += 1
            continue

        if not analysis.get("fetch_ok"):
            unreachable += 1
            continue

        analysed += 1
        decision = qualification.qualify(url, analysis, exclusions)

        if not decision.qualified:
            counts[decision.reason] = counts.get(decision.reason, 0) + 1
            database.mark_checked(domain, decision.reason)
            continue

        signals = decision.signals

        # The same business reached through a different domain.
        record = {
            "business_name": signals["business_name"],
            "domain": domain,
            "instagram": signals.get("instagram_url"),
        }
        existing, _reason = database.find_existing_business(record)
        if existing:
            database.record_alias_domain(existing["id"], domain)
            already_known += 1
            continue

        if database.add_lead(
            signals["business_name"],
            signals.get("instagram_url"),
            signals["website"],
            domain,
        ):
            saved += 1
            print(f"  + {signals['business_name']}  ({signals['website']})")

            if saved >= TARGET_NEW_LEADS:
                break

        time.sleep(1)  # be polite between sites

    print()
    print(f"Checked:             {discovered}")
    print(f"Analyzed:            {analysed}")
    print(f"Not Shopify:         {counts[qualification.NOT_SHOPIFY]}")
    print(f"Closed stores:       {counts[qualification.STORE_CLOSED]}")
    print(f"Outside Lebanon:     {counts[qualification.OUTSIDE_LEBANON]}")
    print(f"Large brands:        {counts[qualification.LARGE_BRAND]}")
    print(f"Unconfigured stores: {counts[qualification.PLACEHOLDER]}")
    print(f"Already known:       {already_known}")
    if unreachable:
        print(f"Unreachable:         {unreachable}")
    print(f"New leads:           {saved}")
    print()

    if saved < TARGET_NEW_LEADS:
        print(f"Saved {saved} new leads (searched every query without "
              f"reaching {TARGET_NEW_LEADS}).")
    else:
        print(f"Saved {saved} new leads.")

    print(f"Database: {database.DATABASE_NAME}  "
          f"({database.lead_count()} leads total)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
