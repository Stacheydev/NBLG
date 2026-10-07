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
import scoring
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

        # Contact priority: a HEURISTIC ordering of leads that have ALREADY
        # qualified, so Hadi knows who to contact first.  It is not a reply
        # probability - see scoring.py.  It runs after the qualified/not
        # verdict is final and has no way to change it.
        priority = scoring.score(signals, analysis)

        # Both dicts are still in scope here, which is the only place these
        # signals exist: collect_signals() keeps dominant_share but discards
        # product_count, and analyze_site()'s contact fields are never
        # persisted today.  Capturing them now avoids re-fetching the site
        # later just to score or retune.
        metrics = {
            "contact_priority_score": priority.score,
            "contact_priority": priority.band,
            "contact_priority_reasons": priority.reasons,

            # Weighted by scoring.score().
            "instagram_followers": signals.get("instagram_followers"),
            "has_email": bool(analysis.get("email")),
            "has_phone": bool(analysis.get("phone")),
            "has_whatsapp": bool(analysis.get("whatsapp")),
            "dominant_share": signals.get("dominant_share"),
            "lebanon_signal_tier": scoring.lebanon_tier(
                signals.get("lebanon_signals")
            ),

            # Recorded at weight zero, for retuning once real outreach
            # outcomes exist.  Acting on them now would be overfitting.
            "industry": analysis.get("industry"),
            "city": analysis.get("city"),
            "hreflang_count": analysis.get("hreflang_count"),
            "has_sentry": bool(analysis.get("has_sentry")),
            "has_store_locator": bool(analysis.get("has_store_locator")),
        }

        if database.add_lead(
            signals["business_name"],
            signals.get("instagram_url"),
            signals["website"],
            domain,
            metrics,
        ):
            saved += 1
            print(f"  + {signals['business_name']}  ({signals['website']})"
                  f"  [priority {priority.score}/10 {priority.band}]")

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
