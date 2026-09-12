# NorthBound Lead Finder — Cleanup & Reorganization Report

**Date:** 2026-09-02
**Type:** maintenance only — no functional change
**Backup taken before any DB change:** `backups/northbound.db.bak-precleanup-20260901T211338Z`

---

## 1. Summary

The repository was reorganized, four unused imports removed, and two read-only SQL views added to
separate operational leads from historical evidence.

**No row in any table was inserted, updated or deleted.** The `leads` and `candidate_ledger`
tables are byte-identical to the pre-cleanup backup, verified by SHA-256 fingerprint:

```
leads   ad325db157578fbddc2bfdb2ecb772a8  ->  ad325db157578fbddc2bfdb2ecb772a8
ledger  271175b21c7c702e1e2f3750a869916b  ->  271175b21c7c702e1e2f3750a869916b
```

Tests: **261 before → 261 after**, 0 failures. Behavioural regression check: **PASS**.

The codebase turned out to be in far better shape than a cleanup pass usually finds — one dead
function, four unused imports, no dead code, no commented-out blocks, no import cycles. Most of
the value here is in organization and documentation, not deletion.

---

## 2. Original structure

80 files, 9 directories, everything at the repository root.

```
Lead gen/
├── main.py  label.py  requalify.py  scraper.py  qualification.py
├── identity.py  exclusions.py  database.py  ledger.py  calibration.py
├── exclusions.json
├── blacklist.json                     ← dead since Aug 31
├── test_shopify.py                    ← legacy scratch script
├── shopify_leads.csv                  ← orphan from the pre-rewrite pipeline
├── refresh.csv                        ← generated artifact
├── northbound.db
├── northbound.db.bak-preledger-…      ← backups loose in the root
├── northbound.db.bak-precalibration-…
├── pytest.ini
├── IMPLEMENTATION_REPORT.md  IMPLEMENTATION_REPORT_V2.md
├── CALIBRATION_DIAGNOSTIC_REPORT.md
├── CANDIDATE_LEDGER_IMPLEMENTATION_REPORT.md
├── HUMAN_LABELING_IMPLEMENTATION_REPORT.md
├── __pycache__/  .pytest_cache/  tests/__pycache__/
└── tests/
```

No `README`, no `requirements.txt`, no `.gitignore`.

---

## 3. Final structure

```
Lead gen/
├── README.md                    NEW
├── requirements.txt             NEW
├── .gitignore                   NEW
├── pytest.ini
├── views.sql                    NEW
│
├── main.py  label.py  requalify.py          entry points
├── scraper.py  qualification.py  identity.py  exclusions.py
├── database.py  ledger.py  calibration.py   libraries
├── exclusions.json
├── northbound.db
│
├── tests/                       unchanged - 9 test files, conftest,
│   ├── fixtures/                capture_fixtures.py, 18 JSON fixtures
│   └── capture_fixtures.py
│
├── docs/
│   ├── IMPLEMENTATION_REPORT.md
│   ├── IMPLEMENTATION_REPORT_V2.md
│   ├── CALIBRATION_DIAGNOSTIC_REPORT.md
│   ├── CANDIDATE_LEDGER_IMPLEMENTATION_REPORT.md
│   ├── HUMAN_LABELING_IMPLEMENTATION_REPORT.md
│   ├── CLEANUP_REPORT.md        NEW (this file)
│   └── archive/
│       ├── blacklist.json
│       ├── test_shopify.py
│       └── shopify_leads.csv
│
├── data/
│   └── refresh.csv
│
└── backups/
    ├── northbound.db.bak-preledger-20260901T191942Z
    ├── northbound.db.bak-precalibration-20260901T201327Z
    └── northbound.db.bak-precleanup-20260901T211338Z
```

The ten Python modules stay flat at the root, as approved. They import each other by bare name and
three are invoked as `python x.py`; a package move would mean rewriting every import, every
invocation and the `sys.path` handling in `conftest.py` for a ten-file project.

---

## 4. Files moved

| From | To | Why |
|---|---|---|
| 5 × `*_REPORT.md` | `docs/` | Design rationale, not root-level clutter. All five preserved — each explains why a rule exists. |
| `blacklist.json` | `docs/archive/` | Superseded by `exclusions.json` on Aug 31. Kept, not deleted. |
| `test_shopify.py` | `docs/archive/` | 38-line scratch script, live network call, no assertions. Already excluded by `pytest.ini`. |
| `shopify_leads.csv` | `docs/archive/` | Jul 31 output of the pre-rewrite pipeline. Referenced by nothing. |
| `refresh.csv` | `data/` | Generated artifact. |
| 2 × `northbound.db.bak-*` | `backups/` | Backups do not belong in the source root. |

## 5. Files deleted

Only generated caches — everything else was moved, never deleted:

- `__pycache__/` (10 `.pyc`)
- `tests/__pycache__/` (11 `.pyc`)
- `.pytest_cache/`

**Zero source files, zero documentation, zero data files deleted.**

## 6. Files created

| File | Purpose |
|---|---|
| `README.md` | Architecture, module-by-module responsibilities, the operational-vs-historical distinction, commands, testing, known issues |
| `requirements.txt` | Derived from actual imports and installed versions: `requests==2.34.2`, `beautifulsoup4==4.15.0`, `ddgs==9.14.4`, `pytest==9.1.1` |
| `.gitignore` | Caches, SQLite side files, `backups/`, editor noise. **`northbound.db` is deliberately not ignored** — it is the historical record. |
| `views.sql` | The two operational views |
| `docs/CLEANUP_REPORT.md` | This report |

## 7. Dead code removed

Four unused imports, each verified unreferenced before removal:

| File | Removed |
|---|---|
| `main.py:7` | `all_leads` from the `database` import list |
| `qualification.py:27` | `normalize_text` from the `exclusions` import |
| `tests/test_lebanon.py:9` | `import pytest` |
| `tests/test_requalify.py:9` | `import sqlite3` |

Two reference updates after the moves:

- `pytest.ini` — comment now points at `docs/archive/test_shopify.py`
- `label.py` docstring — `refresh.csv` → `data/refresh.csv`

**Kept deliberately, as instructed:** `database.export_leads_csv()`. It is called by no module and
no test, and is superseded by `label.py export`, but removing it is unnecessary risk for negligible
benefit. It is now documented as legacy/unused in the README.

**No speculative dead-code hunting was done.** The static audit found no other unreferenced
top-level function in any module, no commented-out code, no debug instrumentation, and no import
cycles.

---

## 8. Database changes

**Two views added. Nothing else.** No `INSERT`, no `UPDATE`, no `DELETE`, no `DROP TABLE`, no
schema change to any table.

### `active_leads` — 26 rows

`decision IN ('qualified', 'review')`. This is the operational list.

Contact fields use a read-time `COALESCE(column, json_extract(signals_json, …))` fallback, because
`set_decision()` refreshes the JSON but not the flat columns (see §12). Reading through the
fallback fixes the staleness at query time; mutating historical rows to match would be a rewrite of
the record, not a cleanup.

Ordering is transparent, deterministic, and uses only fields already in the database — no invented
relevance score:

| Rank | Field | Rationale |
|---|---|---|
| 1 | `decision_rank` | qualified before review |
| 2 | `score DESC` | the model's own confidence, unchanged |
| 3 | `contact_channels DESC` | count of email/phone/WhatsApp/Instagram present |
| 4 | `has_direct_contact DESC` | phone/WhatsApp/email beats Instagram-only |
| 5 | `has_followers DESC` | follower evidence present |
| 6 | `id ASC` | stable tie-breaker |

Verified output — the top of the list is exactly what you would want to call first:

```
 id decision   score  ch  direct  business                domain
 18 qualified     +5   2       1  Lightwave Lebanon       lightwavelb.com
 20 qualified     +5   2       1  CurlySquare             curlysquare.myshopify.com
 52 qualified     +5   1       1  MOROMART LEBANON        moromart.com
 10 qualified     +5   1       0  Qatfa Lebanon           qatfalebanon.com
 …
 23 qualified     +4   4       1  Petriotics              petrioticsstore.myshopify.com
 …
 22 review        +1   3       1  Fattal Online           fattal-online.myshopify.com
 …
  4 review        -2   3       1  MYHOLDAL                myholdal.com
```

18 qualified then 8 review; 25 of the 26 have at least one contact channel.

### `rejected_leads` — 23 rows

The calibration side, preserved and made easy to study. Adds a derived `rejection_kind`
(`lebanon_gate` 14, `hard_reject` 6, `score` 3) read from the existing `reasons` text.

### Rows in neither view, by design

`decision = 'duplicate'` (1 row: Fattal #48) is bookkeeping, and `legacy_unreviewed` (0 rows today)
was never judged. Documented in `views.sql` and the README; query `leads` directly to see
everything.

### Applying the views

The `sqlite3` CLI is **not** installed on this machine, so the README documents the Python form:

```bash
python -c "import sqlite3; sqlite3.connect('northbound.db').executescript(open('views.sql').read())"
```

`views.sql` is idempotent (`DROP VIEW IF EXISTS` then `CREATE VIEW`) and was applied twice against
a copy to confirm it before touching production.

---

## 9. Duplicate analysis

Re-run against the real database using the project's own `identity.find_duplicates()`:

| Check | Result |
|---|---|
| Identity clusters | **1** |
| Exact duplicate domains | 0 |
| Shared Instagram | 1 — `fattalonline` → ids 22, 48 |
| Shared phone | 1 — `9613655267` → ids 22, 48 |
| Shared WhatsApp | 0 |
| Shared email | 1 — `info@fattalonline.com` → ids 22, 48 |
| Bookkeeping `duplicate` rows | id 48 |
| Rows carrying `aka_domains` | id 22 → `fattalonline.com` |

The single cluster is the Fattal pair, and it is **already correctly resolved**: #22
(`fattal-online.myshopify.com`, `review`, canonical, carries the alias) and #48
(`fattalonline.com`, marked `duplicate`).

**No merge, no deletion, no change** — as instructed. Both historical rows remain. The operational
view excludes #48 automatically, which is the correct separation: *removing duplicates from the
operational view is not the same as deleting duplicate historical records.*

---

## 10. Rejected-lead handling

**No rejected lead was deleted.** All 23 remain in `leads` and are exposed through
`rejected_leads`.

The audit established three independent reasons deletion would have been destructive, and they are
recorded here so the question does not get re-opened casually:

1. **16 of the 23 rejected leads have no `candidate_ledger` record.** The ledger is one run deep;
   30 of 50 leads were never rediscovered by it. Deleting rejected rows would destroy the only
   surviving evidence for Marie France (−7), Mazen Online (−6), iSTYLE Lebanon, LIVE LOVE LEBANON,
   BEYTMOD, and the four "My Store" placeholder rows — the last being useful negative calibration
   examples.
2. **It would silently break calibration.** Seven ledger candidates take their model verdict from a
   rejected `leads` row (`karoutonlinelb`, `welcomehomelb`, `houseofappliances`, `superdokan`,
   `swarovski`, `sihoo-leb`, `avonlebanonstore`). Deleting those rows drops those candidates out of
   the confusion matrix — including three of the four suspected false negatives.
3. **`lead_exists()` is the pipeline's duplicate gate.** A deleted rejected domain gets
   re-discovered, re-fetched and re-added on every future run.

The project already encodes this intent: `tests/test_requalify.py::test_no_delete_statement_exists_in_the_codebase`
asserts no `DELETE FROM` or `DROP TABLE` appears in `requalify.py`, `database.py` or `main.py`,
docstringed *"Structural guarantee that history is preserved."*

The resulting separation:

```
leads (historical, 50 rows)          Views (operational)
  ├── qualified  18  ──────────────►  active_leads    26
  ├── review      8  ──────────────►
  ├── rejected   23  ──────────────►  rejected_leads  23
  └── duplicate   1                   (neither — bookkeeping)
```

---

## 11. URL-helper consolidation: **abandoned, with proof**

`database._root_host/_root_url` vs `scraper.clean_url/root_url` were tested for behavioural
equivalence across a 247-case corpus: **205 distinct real URL/domain values** drawn from
`leads.website`, `leads.domain`, `leads.aka_domains`, `candidate_ledger.discovered_url`,
`.normalized_url` and `.domain`, plus **44 adversarial edge cases**.

**Result: 23 mismatches — not equivalent.** All 205 real database values agreed; every divergence
was an edge case:

| Input | `database._root_host` | `scraper.clean_url` |
|---|---|---|
| `None` | `None` | `''` |
| `'   '` | `'   '` | `''` |
| `'/'` | `'https:///'` | `''` |
| `'http://'` | `'http://'` | `''` |
| `'example.com:8080'` | `'example.com'` | **`'8080'`** |
| `'mailto:a@b.com'` | `'mailto'` | `'a@b.com'` |
| `'//example.com/x'` | `'https:////example.com/x'` | `'example.com'` |

Twelve further mismatches in the `_root_url` / `root_url` pair follow from the same causes.

Per the approved rule — *any* mismatch stops the consolidation — **both implementations were left
untouched.** They are near-identical in the happy path and genuinely different at the boundaries;
`cleanup_existing_urls()` runs `_root_host` against the live `leads` table on every startup, so
swapping in `clean_url` would be a silent behavioural change to a data-migration path.

This also surfaced a latent bug in `scraper.clean_url` — see §12.

---

## 12. Known issues intentionally not changed

Every item here would alter behaviour. Each should be a separate, explicitly reviewed change.

**1. `set_decision()` does not backfill the flat contact columns.**
`requalify.py` writes `decision`, `score`, `reasons`, `qualified_at` and `signals_json`, but not
`email`/`phone`/`whatsapp`/`instagram`/`city`/`industry`/`instagram_followers`. The columns are
therefore staler than the JSON:

| Field | Rows where column ≠ signals_json | Signals-only |
|---|---|---|
| `industry` | 35 / 50 | 27 |
| `instagram` | 12 / 50 | 11 |
| `city` | 9 / 50 | 7 |
| `phone` | 7 / 50 | 6 |
| `email` / `whatsapp` | 6 / 50 each | 6 |
| `instagram_followers` | column populated on 4; JSON has 39 | — |

*Not fixed:* backfilling would mutate historical lead records. The views work around it at read
time via `COALESCE`.

**2. `requalify.evaluate_row()` overrides `business_name` too late.**
It sets `analysis["business_name"] = row["business_name"]` *after* `analyze_site()` has already
computed `lebanon_signals` from the name it extracted itself. So the Sep 1 `--refetch` stored the
correct name `"Welcome Home Lebanon"` for lead #11 while its Lebanon signals were still computed
against `"Cookware"` — the `lebanon_in_business_name` claim never fires and the row stays
`REJECTED` on an empty gate. A probable false negative, and a Lebanon-gate change.

**3. `scraper.clean_url()` mishandles a scheme-less host with a port.**
`clean_url("example.com:8080")` returns `"8080"`. With no scheme, `urlparse` puts the whole string
in `.path`; the code takes the first `/`-segment and then splits on `:` taking the *last* part. No
such value exists in the database today. `clean_url` feeds domain identity throughout the pipeline,
so this is not a safe drive-by fix.

**4. Two near-duplicate URL-helper pairs remain.** See §11 — measured, proven non-equivalent,
deliberately left.

**5. `database.export_leads_csv()` is unused.** Kept by instruction; documented as legacy.

**6. Not a Git repository.** Deletions and migrations are currently irreversible, which is why
`backups/` now holds three timestamped copies. **Initialising Git is strongly recommended before
the next round of changes.** Not done here — no automatic `git init` was performed.

**7. `scraper.py` is 1,403 lines**, roughly a third of the production codebase. It is the obvious
splitting candidate and also the densest concentration of measured behaviour in the project.
Splitting it is churn with real regression risk; deliberately not done.

---

## 13. Verification

### Tests

| | Before | After |
|---|---|---|
| Collected | 261 | **261** |
| Passed | 261 | **261** |
| Failed | 0 | **0** |

No test was removed, weakened or skipped. `pytest --collect-only` confirms the count after every
move; `pytest.ini` sets `testpaths = tests`, so relocating `test_shopify.py` could not affect
collection.

Also run: `python -m compileall` (exit 0) and an import smoke test of all ten modules (OK).

### Database integrity

| Check | Before | After |
|---|---|---|
| `PRAGMA integrity_check` | ok | **ok** |
| `PRAGMA quick_check` | ok | **ok** |
| `PRAGMA foreign_key_check` | clean | **clean** |

### Row counts

| Table | Before | After |
|---|---|---|
| `leads` | 50 | **50** |
| `discovery_runs` | 1 | **1** |
| `candidate_ledger` | 49 | **49** |
| `candidate_discovery_sources` | 50 | **50** |
| `candidate_labels` | 0 | **0** |
| Views | 0 | **2** (`active_leads`, `rejected_leads`) |

Lead decisions unchanged: 18 qualified, 8 review, 23 rejected, 1 duplicate.

### Historical evidence

**Zero historical evidence lost.** Every table compared row-by-row against the pre-cleanup backup
and found identical, including `candidate_labels` (still intact and still empty, so your own
labelling is not pre-empted).

### Behavioural regression check

Re-scored all 50 stored leads and every evaluated ledger candidate from stored signals and compared
against the pre-cleanup verdicts. **49/50 reproduce their stored verdict exactly**; the one
shortfall is the `duplicate` bookkeeping row, which has no score by design.

Confirmed unchanged by direct assertion: discovery queries (all 5), `MAX_RESULTS_PER_QUERY = 10`,
`QUALIFY_AT = 2`, `REJECT_BELOW = -2`, all six follower bands, `FOLLOWER_HARD_REJECT = 250_000`,
`MONO_BRAND_SHARE = 0.80`, 250 exclusion brands, all four Lebanon-gate branches including the
`us_conflict` veto, `SHOPIFY_SIGNALS`, identity clustering (still exactly the Fattal pair).

### CLI

`label.py queue|show|open|report`, `label.py --help`, and `requalify.py --limit 3` all exit 0 and
produce correct output. Database verified unchanged afterwards. `main.py` was deliberately **not**
executed — it performs live discovery and would write new rows; its integrity is covered by the
import smoke test and the static guards in `tests/test_pipeline.py`.

---

## 14. What could still be improved later

1. **Initialise Git.** The single highest-value maintenance action remaining.
2. **The three known bugs in §12** — each as a separate, reviewed change with a labelled
   calibration set to measure against.
3. **`scraper.py` could be split** once there is a labelled dataset to regression-test against.
4. **`candidate_ledger` has no index on `run_id` alone** (only `(run_id, status)` and
   `(run_id, discovered_url)`). Irrelevant at 49 rows.
5. **`northbound.db` will eventually want a retention policy** for `candidate_discovery_sources`.
   Not remotely a concern at 50 rows.
6. **The views are not auto-created.** If `northbound.db` is deleted and rebuilt, `views.sql` must
   be re-applied by hand. Wiring it into `database.create_database()` would be a startup-behaviour
   change and was not done.
