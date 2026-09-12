"""Capture real signal data from live stores into tests/fixtures/.

Run manually, NOT part of the test suite:

    python tests/capture_fixtures.py

The tests themselves never touch the network - they replay these files.
Re-run this only when you want to refresh the measurements.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scraper  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# label -> url.  Groups match the investigation's measured sample.
SITES = {
    # --- large brands / chains / wrong country: must NOT qualify ---
    "lacoste": "https://lacoste.com.lb",
    "swarovski": "https://swarovski.com.lb",
    "istyle": "https://istyle.com.lb",
    "marie_france": "https://mariefrancelingerie.com",
    "mike_sport": "https://mikesport.com",
    "fattal": "https://fattalonline.com",
    "maureen_abood_us": "https://shop.maureenabood.com",
    # --- legitimate Lebanese SMEs: must NOT be rejected ---
    "livgood": "https://livgood.com",
    "curly_square": "https://curlysquare.myshopify.com",
    "istahly": "https://istahly.com",
    "outgeeked": "https://outgeeked.net",
    "qatfa": "https://qatfalebanon.com",
    "lightwave": "https://lightwavelb.com",
    "mj_boardgames": "https://mjboardgames.com",
    "adaline": "https://adalinelb.com",
    "moromart": "https://moromart.com",
    "petriotics": "https://petrioticsstore.myshopify.com",
    "klaptap": "https://klaptap.com",
}


def main():
    FIXTURES.mkdir(parents=True, exist_ok=True)

    for label, url in SITES.items():
        print(f"--- {label} ({url})")

        analysis = scraper.analyze_site(url)
        if not analysis.get("fetch_ok"):
            print("    FETCH FAILED - skipping")
            continue

        products = scraper.fetch_products_json(url)
        followers = scraper.fetch_instagram_followers(analysis.get("instagram"))

        payload = {
            "label": label,
            "url": url,
            "analysis": analysis,
            "products": products,
            "instagram_followers": followers,
        }

        path = FIXTURES / f"{label}.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)

        print(f"    name={analysis.get('business_name')!r} "
              f"ig={analysis.get('instagram')!r} followers={followers} "
              f"vendor={products.get('dominant_vendor') if products else None!r} "
              f"lebanon={analysis.get('lebanon_signals')}")

        time.sleep(2)  # polite, and keeps Instagram from rate-limiting

    print(f"\nWrote fixtures to {FIXTURES}")


if __name__ == "__main__":
    main()
