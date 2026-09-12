# NorthBound Lead Finder

Finds **Lebanese Shopify stores** that NorthBound could help scale, and saves them to a
database. Small and mid-sized merchants — not brands, not their outlets, not foreign
businesses that merely mention Lebanon.

```bash
pip install -r requirements.txt
python main.py
```

That is the whole interface. No arguments, no CSVs, no manual labelling, no SQL.

A run keeps searching until it has saved **10 new leads** or has exhausted its 28 search
queries, whichever comes first. Discovery is lazy, so a run that hits the target early simply
stops querying.

---

## What a run does

```
DISCOVER -> SHOPIFY? -> OPEN? -> LEBANON? -> NOT A BIG BRAND? -> ALREADY KNOWN? -> SAVE
```

```text
NorthBound Lead Finder
======================

Searching for up to 10 new leads...

  + Beirut Junction  (https://beirutjunction.myshopify.com)
  + Built in Beirut  (https://builtinbeirut.com)
  + Lemon Beirut  (https://lemonbeirut.com)
  ...

Checked:             66
Analyzed:            24
Not Shopify:         7
Closed stores:       2
Outside Lebanon:     4
Large brands:        1
Unconfigured stores: 0
Already known:       42
New leads:           10

Saved 10 new leads.
Database: northbound.db  (37 leads total)
```

Run it again whenever you want more leads. It reuses the same database, skips everything it
has already seen, and appends only what is new.

---

## The four rules

A candidate is a lead only if it passes all four. They are binary — there is no score and no
"maybe" band.

| | Rule | How it is decided |
|---|---|---|
| 1 | **Shopify** | Shopify's own markers in the page (`cdn.shopify.com`, `shopify-section`, …) |
| – | **Storefront open** | A suspended store answers HTTP 402 (Shopify's own signal); closed/`Opening Soon`/password-protected pages are also rejected. A closed shop carries every other qualifying signal, so without this it lands in the lead list. |
| 2 | **In Lebanon** | A `+961` phone, a `.lb` domain, a Lebanese city in the contact block, LBP pricing, Lebanon shipping context. An explicit US "Lebanon, PA" reference vetoes the weaker evidence — that town is why a keyword match is not enough. |
| 3 | **Not a huge brand** | A 252-entry brand list (international brands + major Lebanese groups); a catalogue that is ≥80% one known international brand, which makes the shop that brand's outlet; or **more than** 100K Instagram followers. |
| 4 | **Not already known** | Same domain, or the same business reached through a different domain — matched on Instagram, email, phone or a corroborated name. |

**Why 100K followers.** Every legitimate SME in the measured sample sat at or below 45K
followers; the large brands started at 112K. 100K sits in that gap. The test is strict (`> 100000`),
and a follower count that cannot be retrieved never disqualifies — an unknown is not evidence,
and a fabricated count would be worse.

Nothing else is used. Catalogue size, website quality, having a branch, and running a
`.myshopify.com` address are all deliberately *not* signals — they were measured and found to
be either uninformative or actively inverted.

---

## What gets stored

`northbound.db` (SQLite, created on first run, reused forever after).

**`leads`** — the product:

| Column | |
|---|---|
| `business_name` | the actual shop, not a product or page title |
| `instagram_url` | `https://www.instagram.com/example/`, or NULL |
| `website` | the **root** site, never the page it was discovered on |

plus `id`, `domain`, `identity_key` and `aka_domains`, which exist only so deduplication works.

**`rejected_sites`** — a cache of domains already analysed and rejected, so repeat runs do not
re-fetch the same non-Shopify and foreign sites. It holds no lead data; deleting it only makes
the next run slower.

```sql
SELECT business_name, instagram_url, website FROM leads;
```

### Three things that are easy to get wrong

- **The website is the root.** Discovery returns `/products/…` and `/collections/…` far more
  often than a homepage. `https://shop.com/products/vase` is stored as `https://shop.com`.
- **The business name is the business.** Site-level identity — JSON-LD `Organization`,
  `og:site_name`, the header logo — is read *before* any page title, because on a product page
  the title is the product. A real example from the live run: `hightechlebanon.com`'s title is
  "Ultrasonic Cleaner | High Tech Lebanon", and the stored name is **High Tech Lebanon**. When
  nothing identifies the site, the name falls back to the domain rather than to whatever text
  happened to be on the page.
- **Instagram is never fabricated.** An earlier version built `instagram.com/<business name>`,
  which produced profiles that do not exist. A handle is stored only when it is actually found
  on the page and confidently belongs to that business; otherwise the column is NULL.

---

## Modules

```
scraper, exclusions        (leaves - no project imports)
    └── identity
          └── database
   qualification           (scraper + exclusions)
   main                    (entry point)
```

| File | Responsibility |
|---|---|
| `main.py` | The pipeline, and the only entry point. |
| `scraper.py` | Discovery queries, page fetch, Shopify detection, the Lebanon signal extractor and gate, business-name and contact extraction, URL normalisation. |
| `qualification.py` | Rules 1–3, as a binary decision. Nothing else may decide a lead's fate. |
| `exclusions.py` / `exclusions.json` | The 252-brand list behind Rule 3. Aborts the run rather than load silently broken — a quietly empty brand filter is worse than none. |
| `identity.py` | When two records are the same business. One strong identifier (domain, email, Instagram, phone) is enough; a similar name alone is not. |
| `database.py` | The schema, deduplication lookups, the rejection cache, and migration from the previous schema. |

---

## Testing

```bash
pytest                          # 243 tests, no network access
pytest tests/test_qualification.py
```

No test touches the network — store data is replayed from 18 real captured fixtures in
`tests/fixtures/`, captured by `tests/capture_fixtures.py` (the only script that makes live
requests; run it by hand to refresh them).

| File | Covers |
|---|---|
| `test_qualification.py` | the four rules, and the SMEs that must **not** be rejected |
| `test_pipeline.py` | end-to-end runs, persistence across runs, the schema, structural guards |
| `test_business_name.py` | product titles, nav labels, interstitials, JSON-LD |
| `test_urls.py` | root-URL normalisation |
| `test_instagram.py` | handle extraction and URL normalisation |
| `test_lebanon.py` | the Lebanon gate, including US towns named Lebanon |
| `test_identity.py`, `test_exclusions.py` | deduplication and the brand list |

Several tests are structural guards rather than behaviour tests, and are there on purpose:
qualification cannot be bypassed; the decision stays binary and cannot regrow a score; the
calibration workflow cannot come back; `main.py` takes no arguments; nothing deletes rows.

---

## Migration from the previous version

The first run on an old database migrates it automatically and once:

1. the old file is copied to `backups/` untouched;
2. the **current** rules are re-applied to the evidence each old row already stored — the old
   scorer's verdict is not trusted;
3. rows that still qualify become leads; the rest go to `rejected_sites` so they are not
   rediscovered;
4. a stored name that no longer passes validation is replaced by the domain, so known-bad names
   like `"Shop By Lifestyle"` are not carried forward.

On the live database this carried over **28 of 52** rows. The 24 that did not are listed with
their reason at migration time — mostly businesses outside Lebanon, plus brands the previous
scoring model had let through.

`docs/archive/` holds the reports for the previous calibration-based architecture. They describe
a system that no longer exists and are kept only as history.
