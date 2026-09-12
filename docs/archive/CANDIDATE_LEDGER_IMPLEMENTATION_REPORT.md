# NorthBound Lead Finder — Candidate Ledger Implementation Report

**Date:** 2026-09-01
**Phase:** observability / calibration infrastructure only
**Preceding document:** `CALIBRATION_DIAGNOSTIC_REPORT.md`

---

## 1. Executive summary

The diagnostic found that the pipeline destroyed its own evidence: of the 47 candidates in the
previous run, only 2 rows survived anywhere. The 14 rejections and 15 non-Shopify sites were
discarded by the early `continue` paths in `main.py` and left no trace, which made the
false-negative rate structurally unmeasurable.

That is now fixed. A run-scoped candidate ledger records **every** discovered URL *before* any
gate can discard it, and updates that record with the outcome at every exit.

**Result of the first real run under the ledger:**

| | |
|---|---|
| Discovery results (incl. repeats) | **50** |
| Unique candidates recorded | **49** |
| Candidates left unresolved (`discovered`) | **0** |
| Sum of terminal statuses | **49 — 100% coverage** |
| Reached qualification | 13 |
| Previously invisible candidates now recorded | **36** (18 already-known + 18 non-Shopify) plus all 11 rejections |

`laptopsking.com` — the candidate the diagnostic could only recover by re-scraping the live web —
is now a permanent ledger row carrying its verdict (`REJECTED`, score `-3`, rejection kind
`score`), its 76,000-follower count, its reasons and its full signals JSON.

**Qualification behaviour is unchanged.** `qualification.py`, `identity.py`, `exclusions.py`,
`exclusions.json` and `blacklist.json` were not opened for editing and retain their original
timestamps. Independent offline re-scoring of all 13 evaluated candidates reproduced the stored
verdict **13/13 exactly** — same outcome, same score, same reasons line for line.

**Test suite: 179 passing before → 212 passing after** (33 new tests, 0 failures, 0 skips).
**Database: `PRAGMA integrity_check` = ok**, all 48 pre-existing leads byte-identical to the
pre-migration backup.

---

## 2. Files created and modified

### Created

| File | Purpose |
|---|---|
| `ledger.py` (21 KB) | The ledger: schema, run lifecycle, candidate recording, read-back helpers. Contains no scoring logic of any kind. |
| `tests/test_ledger.py` (24 KB) | 33 tests covering every requirement in the brief. |
| `northbound.db.bak-preledger-20260901T191942Z` | Pre-migration backup (project convention). |
| `CANDIDATE_LEDGER_IMPLEMENTATION_REPORT.md` | This document. |

### Modified

| File | Change | Model impact |
|---|---|---|
| `main.py` | Ledger record created before the loop's first gate; every `continue` path now updates it; run opened/closed around the loop in a `try/finally`. | **None.** Gate order, conditions, counters and console output unchanged. |
| `scraper.py` | Two edits: (1) extracted the inline literal `10` into `MAX_RESULTS_PER_QUERY = 10`; (2) `search_websites()` now appends a repeat sighting to a `sources` list instead of dropping it. | **None.** Same queries, same cap, same candidate set, same order. |
| `database.py` | Added `lead_id_for_domain()`. | **None.** Purely additive; `lead_exists()` untouched and still the pipeline's duplicate gate. |

### Deliberately untouched

`qualification.py`, `identity.py`, `exclusions.py`, `exclusions.json`, `blacklist.json`,
`requalify.py`, and every existing test file. Verified by file timestamp:

```
2026-08-31 20:35  exclusions.py
2026-08-31 21:00  identity.py
2026-08-31 21:01  exclusions.json
2026-08-31 21:33  qualification.py
2026-08-11 19:15  blacklist.json
```

---

## 3. Database schema and migration

Three new tables in the existing `northbound.db`. The migration is `CREATE TABLE IF NOT EXISTS`
only — **no `ALTER TABLE`, no `DROP`, no `DELETE`, no write of any kind to `leads`**. A static
test (`test_ledger_schema_is_additive_only`) enforces this by regex over `ledger.py`.

### `discovery_runs`
```
run_id TEXT PRIMARY KEY   -- e.g. 20260901T192554Z-79291325
started_at, completed_at, status ('running' | 'completed')
queries_json              -- the exact KEYWORDS list used
config_json               -- {"max_results_per_query": 10, "exclusion_brands": 250}
summary_json              -- counts, computed from this run's own ledger rows
notes
```

### `candidate_ledger`
```
candidate_id INTEGER PK, run_id

-- discovery identity
discovered_url, normalized_url, domain,
first_seen_query, first_seen_position, result_title

-- where it stopped and what happened
stage, status

-- pre-fetch gates
already_known, already_known_lead_id,
excluded, exclusion_name, exclusion_reason

-- fetch / extraction
fetch_ok, is_shopify, is_ecommerce, business_name,
analysis_json, error_type, error_message

-- qualification, copied verbatim from the Decision object
qualification_ran, decision, score, rejection_kind,
lebanon_gate_passed, reasons, signals_json, instagram_followers

-- identity resolution
identity_checked, identity_key, identity_match_lead_id, identity_match_reason

-- storage
stored_lead_id

-- timing
discovered_at, processing_started_at, processing_completed_at
```

### `candidate_discovery_sources`
```
source_id PK, candidate_id, run_id, query, position, result_title, seen_at
```

### Indexes
```
UNIQUE (run_id, discovered_url)                -- one candidate per URL per run
UNIQUE (candidate_id, query, position)         -- one source row per query hit
INDEX  (domain)                                -- cross-run history
INDEX  (run_id, status)                        -- reporting
```

### Migration verification
```
leads before: 48   after: 48   rows identical: True
PRAGMA integrity_check   -> ok
PRAGMA foreign_key_check -> clean
tables: candidate_discovery_sources, candidate_ledger, discovery_runs, leads, sqlite_sequence
```

---

## 4. Exact lifecycle of a candidate

The ordering the brief required — **discover → record → process → update** — replacing
**discover → process → continue → vanish**.

```
search_websites()
  └─ returns [{business, website, sources:[{query, position, title}, ...]}, ...]

for each site:
    ┌──────────────────────────────────────────────────────────────┐
    │ ledger.record_candidate(run_id, url, normalized_url, domain, │  <-- BEFORE any gate
    │                         sources, title)   -> candidate_id    │      status = 'discovered'
    │ ledger.mark_processing(candidate_id)                         │      stage  = 'discovery'
    └──────────────────────────────────────────────────────────────┘

    lead_exists(domain)? ──yes──> status=already_known  stage=duplicate_check   -> continue
    is_excluded(domain)? ──yes──> status=excluded       stage=exclusion_check   -> continue
    analyze_site() raises? ─────> status=error          stage=fetch             -> continue
    not is_shopify?
        fetch_ok == False ─────> status=fetch_failed    stage=fetch             -> continue
        fetch_ok == True  ─────> status=not_shopify     stage=shopify_check     -> continue

    decision = qualify(...)                       <-- unchanged, single source of truth

    decision == REJECTED? ─────> status=rejected  stage=qualification           -> continue
                                 + rejection_kind, score, reasons, signals_json

    find_existing_business()? ─> status=duplicate stage=identity_check          -> continue
                                 + identity_match_lead_id, identity_match_reason

    add_lead(...)
                              ─> status=qualified|review  stage=storage
                                 + stored_lead_id, identity_key
```

A static test (`test_ledger_record_precedes_every_continue_in_main`) parses `main.py` and asserts
that **every** `continue` statement appears after the `ledger.record_candidate(` line, so a future
edit cannot reintroduce a silent exit.

The run itself is opened before discovery and closed in a `finally` block, so a crashed run is
still recorded and summarized rather than being left forever `running`.

---

## 5. Every ledger status, and what it means

`status` answers *what happened*; `stage` answers *how far it got*. They are separate columns
because "discovered but not yet processed" and "processed and rejected" must never collapse into
one value.

| Status | Meaning | Reached qualification? |
|---|---|---|
| `discovered` | Row created, not yet resolved. **A completed run should contain none.** | no |
| `already_known` | Domain already in `leads`. Exited at the duplicate gate; never fetched. | no |
| `excluded` | Pre-fetch exclusion-list hit on the domain. Never fetched. | no |
| `fetch_failed` | `analyze_site()` returned `fetch_ok=False` — the page never loaded. | no |
| `error` | An exception escaped `analyze_site()`. `error_type` + `error_message` recorded. | no |
| `not_shopify` | Fetched successfully, no Shopify signature. | no |
| `rejected` | Qualification said no. See `rejection_kind`. | **yes** |
| `review` | Qualification said maybe; stored in `leads`. | **yes** |
| `qualified` | Qualification said yes; stored in `leads`. | **yes** |
| `duplicate` | Identity resolution matched a stored lead; alias recorded instead of a new row. | **yes** |

### `rejection_kind` — three very different reasons to say no

| Value | Meaning |
|---|---|
| `lebanon_gate` | Hard rejection whose reason contains "failed Lebanon gate". |
| `hard_reject` | Any other hard rejection (exclusion list, placeholder name, mono-brand outlet, brand-scale followers). |
| `score` | Not hard-rejected — the weighted score fell below `REJECT_BELOW`. |

This is a **classification of a verdict that already exists**, computed by reading
`decision.reasons`. `ledger.classify_rejection()` performs no scoring; a dedicated test
(`test_classify_rejection_only_reads_an_existing_decision`) pins that down.

### `qualification_ran` — "not evaluated" vs "evaluated and rejected"

The brief's most important distinction, and it gets its own boolean. A candidate rejected *before*
qualification (`already_known`, `excluded`, `not_shopify`, `fetch_failed`, `error`) carries **zero**
information about the scoring model. One rejected *by* qualification carries all of it. Calibration
work should filter on `qualification_ran = 1`.

### Two useful separations the ledger introduces

- **`fetch_failed` vs `not_shopify`.** `analyze_site()` swallows network failures internally and
  returns an empty result, so the pipeline sees both as "Not a Shopify store". The pipeline still
  treats them identically (unchanged); the ledger keeps them apart, so a dead site is never
  miscounted as a live non-Shopify one.
- **`excluded` vs `rejected`.** `main.py` increments the same `rejected` console counter for both
  (unchanged). The ledger separates them, because a pre-fetch brand-domain skip and a
  post-qualification rejection are not the same evidence.

---

## 6. Repeat discovery within one run

`search_websites()` used to drop a URL the instant a second query returned it, so *which query
found a candidate* was already being lost inside discovery itself. It now appends the extra
sighting to a `sources` list. The returned candidate list is unchanged: same URLs, same order,
same count, ignored hosts still dropped.

`ledger.record_candidate()` is idempotent per `(run_id, discovered_url)`, enforced by a unique
index. Calling it again returns the same `candidate_id` and adds only genuinely new source rows
(`INSERT OR IGNORE` on `(candidate_id, query, position)`).

**Live evidence from the validation run — 50 discovery results, 49 unique candidates:**

```
karoutonlinelb.com -> 2 sources
      q='"Lebanon" "shop now"'   pos=1
      q='"Lebanon" "buy online"' pos=2
```

Under the old code the second sighting was silently discarded. It is now attributable.

`first_seen_query` / `first_seen_position` on the candidate row record which query found it first;
the sources table records all of them.

---

## 7. Association with discovery queries

A dedicated `candidate_discovery_sources` table rather than a JSON blob, because it makes the
question the diagnostic could not answer cheaply — *which query produces which kind of candidate* —
a plain `GROUP BY` instead of 51 live re-fetches:

```sql
SELECT s.query, cl.status, COUNT(*)
FROM candidate_discovery_sources s
JOIN candidate_ledger cl USING (candidate_id)
WHERE s.run_id = ?
GROUP BY s.query, cl.status;
```

The run's `queries_json` and `config_json` record the exact `KEYWORDS` list and
`MAX_RESULTS_PER_QUERY` in force, so a later change to discovery stays attributable to the run it
happened in.

---

## 8. How qualification output is preserved

`ledger.record_outcome()` accepts the `qualification.Decision` object and copies it verbatim:

```python
"decision":       decision.outcome
"score":          decision.score
"reasons":        "\n".join(decision.reasons)
"signals_json":   json.dumps(decision.signals)
"rejection_kind": classify_rejection(decision)   # reads reasons; scores nothing
```

There remains exactly one source of truth. Enforced three ways:

1. **Static guard** — `test_ledger_module_does_no_scoring()` asserts `ledger.py` contains none of
   `score_lead`, `hard_rejections`, `FOLLOWER_BANDS`, `QUALIFY_AT`, `REJECT_BELOW`,
   `passes_lebanon_gate`, `import qualification`, `import scraper`. `ledger.py` imports only
   `json`, `sqlite3`, `uuid`, `datetime`, and `DATABASE_NAME` from `database`.
2. **Replay test** — stored `signals_json` re-scored through `qualification.qualify_from_signals()`
   must reproduce the stored outcome, score and reasons.
3. **Live verification** — run against the real database:
   ```
   offline re-score of ledger signals: 13/13 reproduce the stored verdict exactly
   ```

`lebanon_gate_passed` is copied from `analysis["is_lebanon"]`, which `analyze_site()` already
computes as `passes_lebanon_gate(signals)`. The ledger does **not** re-derive it — doing so would
be a second implementation of the gate.

---

## 9. Tests

**Before implementation: 179 passed. After: 212 passed** (33 added, 0 failures, 0 skips, ~85s).
No network access; all fixtures local.

| # | Requirement | Test(s) |
|---|---|---|
| 1 | every early `continue` creates/updates a record | `test_every_candidate_in_a_mixed_run_is_recorded`, `test_ledger_record_precedes_every_continue_in_main` |
| 2 | not-Shopify recorded | `test_not_shopify_candidate_is_recorded` |
| 3 | Lebanon-gate failures recorded | `test_lebanon_gate_rejection_is_recorded` |
| 4 | hard rejections recorded | `test_other_hard_rejection_is_recorded_separately` |
| 5 | score rejections recorded | `test_score_rejection_is_recorded_separately` |
| 6 | REVIEW recorded | `test_review_candidate_is_recorded_and_stored` |
| 7 | QUALIFIED recorded | `test_qualified_candidate_is_recorded_and_stored` |
| 8 | already-known recorded | `test_already_known_candidate_is_recorded` |
| 9 | excluded recorded | `test_excluded_domain_is_recorded` |
| 10 | errors recorded | `test_fetch_error_is_recorded_with_diagnostics`, `test_silent_fetch_failure_is_distinguished_from_not_shopify` |
| 11 | no duplicate rows within a run | `test_repeat_discovery_within_a_run_makes_one_candidate_many_sources`, `test_record_candidate_is_idempotent_within_a_run` |
| 12 | same candidate attributable across runs | `test_same_candidate_across_two_runs_stays_attributable_to_both` |
| 13 | full signals/reasons preserved | `test_full_signals_and_reasons_are_preserved` |
| 14 | qualification output identical | `test_ledger_verdict_equals_an_independent_requalification`, `test_ledger_module_does_no_scoring`, `test_classify_rejection_only_reads_an_existing_decision` |
| 15 | existing leads untouched | `test_creating_the_ledger_does_not_touch_existing_leads`, `test_a_full_run_leaves_existing_leads_intact`, `test_ledger_schema_is_additive_only` |
| 16 | failed ledger write fails loudly | `test_a_failed_ledger_write_raises`, `test_the_pipeline_aborts_when_the_ledger_cannot_record`, `test_unknown_status_or_stage_is_rejected`, `test_unknown_column_is_rejected`, `test_record_candidate_requires_a_run_and_url`, `test_finishing_an_unknown_run_raises` |
| extra | identity duplicate recorded | `test_identity_duplicate_is_recorded` |
| extra | run config + summary recorded | `test_runs_record_their_own_configuration_and_summary` |
| extra | discovery still drops ignored hosts | `test_search_websites_still_drops_ignored_hosts` |
| extra | discovery sources preserved | `test_search_websites_preserves_every_discovery_source` |
| extra | integrity after migration | `test_database_integrity_after_ledger_creation` |

### On requirement 16 — failing loudly

Every ledger write raises `LedgerError` on failure; nothing is swallowed. `main.py` does **not**
wrap ledger calls in `try/except`, so a ledger failure aborts the run. `record_outcome()` also
rejects an unknown status, an unknown stage or an unknown column name rather than silently
dropping the field. A run that cannot be observed must not quietly proceed — that is the entire
premise of this phase.

### Two test-fixture corrections made during the work

Both were faults in my new tests, not in production code:

- `conftest.make_analysis()` defaults `is_lebanon=True` regardless of the `lebanon_signals`
  override. Real `analyze_site()` always keeps the two consistent. The tests now set both.
- Two assertions were over-broad (a substring check against JSON-escaped text, and a `"DROP"`
  substring search that matched the word "dropped" in a comment). Both were tightened.

---

## 10. Database integrity verification

Backup taken before any schema change, per project convention:
`northbound.db.bak-preledger-20260901T191942Z` (77,824 bytes, 48 leads).

```
PRAGMA integrity_check    -> ok
PRAGMA quick_check        -> ok
PRAGMA foreign_key_check  -> clean

leads                        50 rows
discovery_runs                1 row
candidate_ledger             49 rows
candidate_discovery_sources  50 rows
```

**Row-by-row comparison against the backup:**

```
backup rows: 48   current rows: 50
pre-existing rows modified: 0   []
new ids added: [65, 66]
```

All 48 pre-existing leads are byte-identical. Ids 65 (`maison123-lb.com`, REVIEW) and 66
(`la2taa.com`, QUALIFIED) were added by normal pipeline operation during the validation run —
ordinary lead insertion, not a ledger effect.

---

## 11. Results of the first real discovery run

**Run `20260901T192554Z-79291325`** — 2026-09-01 19:25:54 → 19:28:10 UTC, status `completed`.

### Requested counts

| Metric | Value |
|---|---|
| Total discovered URLs (discovery results, incl. repeats) | **50** |
| Unique candidates | **49** |
| Already-known | **18** |
| Excluded | **0** (see note below) |
| Non-Shopify | **18** |
| Errors | **0** |
| Lebanon-gate rejected | **9** |
| Hard rejected (non-Lebanon) | **0** |
| Score rejected | **2** |
| REVIEW | **1** |
| QUALIFIED | **1** |
| Duplicates (identity) | **0** |
| Left unresolved (`discovered`) | **0** |

Statuses sum to 49 = unique candidates. **Coverage is 100%.**

### Candidates by discovery query

```
 10  "Lebanon" "buy online"
 10  "Lebanon" "online store"
 10  "Lebanon" "powered by Shopify"
 10  "Lebanon" "shop now"
 10  site:myshopify.com Lebanon
```

50 source rows across 49 candidates — `karoutonlinelb.com` was returned by two queries.

### Note on the zero `excluded` count

No candidate reached the exclusion gate this run. The two excluded-brand domains in the result set
(`swarovski.com.lb`, `sihoo-leb.com`) are already stored leads, so they exited one gate earlier at
`already_known` — the duplicate check precedes the exclusion check in `main.py`, which is unchanged
behaviour. **This is itself an example of what the ledger now makes visible**: gate ordering
determines which reason gets recorded, and that was previously unobservable.

The `excluded`, `error`, `fetch_failed` and `duplicate` paths were exercised against the real
`main.main()` on a scratch database (production untouched):

```
nike.com               -> EXCLUDED      stage=exclusion_check
                          excluded=1 name='Nike' reason='pre-fetch domain match on nike.com'
                          fetch_ok=None                    <- never fetched, correctly
broken.example         -> ERROR         stage=fetch
                          error_type='ConnectionError' message='getaddrinfo failed'
dead.example           -> FETCH_FAILED  stage=fetch
                          fetch_ok=0 error_type='fetch_failed'
twin-lb.myshopify.com  -> DUPLICATE     stage=identity_check
                          identity_checked=1 match_lead=1
                          reason='same instagram: twinstore'
```

---

## 12. Evidence that all discovered candidates are now observable

### 12.1 The headline case — `laptopsking.com`

The diagnostic could only recover this candidate by re-scraping the live web. It is now a
permanent row:

```
laptopsking.com  ->  REJECTED  (stage qualification)
  candidate_id=30
  url=https://laptopsking.com/collections/laptops/Laptops
  query='"Lebanon" "powered by Shopify"' pos=10
  name='Laptops King'  shopify=1  fetch_ok=1  leb_gate=1
  qual_ran=1  decision=rejected  score=-3  rejection_kind=score
  followers=76000
  reasons: -5 very_large_following (76,000 followers)
         | +1 WhatsApp contact channel
         | +1 no enterprise tooling detected
  identity_checked=0                        <- the ordering defect, now VISIBLE
```

Its twin `laptopskinglb-961.myshopify.com` is recorded in the same run as `already_known`
(`already_known_lead_id=64`). Both halves of the case are queryable — the twin's stored lead is
row 64, and the rejected half now carries its own signals. The identity-ordering defect is
unchanged, as required, but it is no longer invisible: `identity_checked=0` on a rejected
candidate is now a fact you can `SELECT`.

### 12.2 Candidates from the diagnostic's manual-inspection list

| Candidate | Ledger status | What the record explains |
|---|---|---|
| `welcomehomelb.com` | `already_known` → lead 11 | Skipped at the duplicate gate; `qualification_ran=0`. Its hard-reject verdict is the stale one on lead 11 — **it was not re-evaluated this run**. |
| `karoutonlinelb.com` | `already_known` → lead 2 | Same. Also the run's only two-query candidate. |
| `houseofappliances.co` | `already_known` → lead 19 | Same; discovered at `"Lebanon" "buy online"` position 7. |
| `laptopsking.com` | `rejected` / `score` / −3 | Full record above. |
| `laptopskinglb-961.myshopify.com` | `already_known` → lead 64 | Its twin, one gate earlier. |
| `carpisalebanon.com` | `already_known` → lead 63 | Stored at REVIEW from the previous run; not re-scored. |
| `mt-lebanon-book-cellar.myshopify.com` (foreign) | `rejected` / `lebanon_gate` | `leb_gate=0`, reason `HARD REJECT: failed Lebanon gate`. The `us_conflict` veto working, now on record. |
| `tecno-lb.com` (non-Shopify Lebanese) | `not_shopify` | `fetch_ok=1`, `is_shopify=0`, `business_name='Tecnoservice'`, `leb_gate=1` — **a Lebanese store that passes the Lebanon gate and is discarded solely for its platform. Previously invisible.** |
| `nike.com` (excluded brand) | `excluded` | Demonstrated on the scratch run above. |
| `la2taa.com` | `qualified` / +3 | `stored_lead_id=66`. |
| `maison123-lb.com` | `review` / 0 | `stored_lead_id=65`. New candidate: 29K followers, store locator, own brand — the Carpisa shape again. |

### 12.3 The population that used to vanish

**36 of this run's 49 candidates (73%) would have left no trace under the old code:**
18 `already_known` + 18 `not_shopify`. Add the 11 rejections and the figure is 47 of 49 (96%).
Only the 2 stored leads would have survived — exactly matching the diagnostic's finding that a
47-candidate run produced 2 rows.

**Of particular calibration value, now recorded for the first time:** 18 non-Shopify candidates
including `tecno-lb.com`, `vastlebanon.com`, `flowerzoneboutique.com`, `isn-store.com`,
`seasweet.com`, `ishtari.com`, `kelchi.com`, `yelleb.com`, `lebanon-apparel.com`,
`kouranionline.com` — the population the diagnostic identified as the largest discarded group of
genuine Lebanese SMEs. They now have `fetch_ok`, `business_name` and `lebanon_gate_passed`
recorded, so the "should we go beyond Shopify" question can be answered from a table.

### 12.4 On "all 47 candidates from the latest comparable run"

Stated precisely: **the ledger captured 100% of the candidates in the run it observed** — 49 of 49,
zero left in `discovered`, statuses summing exactly to the total.

It is not a byte-identical repeat of the diagnostic's 47-candidate run: DuckDuckGo results drift.
Of the 51 domains in the diagnostic probe, **36 reappeared**; 12 were new
(`maison123-lb.com`, `decorahomelb.myshopify.com`, `allbrandsfactoryoutlet.com`, `kelchi.com`,
`yelleb.com`, `llbean.com`, `dhgate.com`, …) and 15 did not return this time
(`ovape-lebanon.com`, `compuonelb.com`, `takkoushflowers.com`, `nur-lebanon.myshopify.com`,
`mariefrancelingerie.com`, `selfridges.com`, …). Result drift of ~25% between runs is itself a
newly measurable fact, and one worth tracking now that runs are individually addressable.

---

## 13. Limitations and edge cases

1. **Candidate identity is the URL, not the domain.** Two different URLs on the same host produce
   two ledger rows. The run contains a live example: `terraboost-media.myshopify.com` appears as
   candidates 42 and 48 from two different product URLs. This is deliberate — it is what discovery
   actually returned, and collapsing by domain would hide a real query-quality signal. Calibration
   queries should `GROUP BY domain` where a per-business view is wanted.

2. **`already_known` candidates are not re-evaluated.** They exit at the duplicate gate, so
   `qualification_ran=0` and no fresh signals are captured. This is unchanged pipeline behaviour.
   The consequence for calibration: 18 of 49 candidates in this run contribute no new evidence, and
   stale verdicts on rows like `welcomehomelb.com` (a suspected false negative) are **not**
   refreshed by a normal run. `requalify.py --refetch` remains the tool for that.

3. **Gate ordering determines the recorded reason.** A candidate that is both already-known *and*
   an excluded brand records `already_known`, because that gate comes first. The ledger records
   what the pipeline did, not every label that could apply.

4. **`error` vs `fetch_failed`.** `analyze_site()` catches its own network failures, so genuine
   exceptions reaching `main.py` are rare; most fetch problems surface as `fetch_failed`. The run
   showed 0 of the former and 0 of the latter.

5. **No `human_label` column yet.** The brief did not ask for one and adding it now would prejudge
   the labelling workflow's shape. Hand labels should be attached in the next phase, keyed on
   `candidate_id`.

6. **Ledger growth is unbounded.** ~50 rows per run is negligible for a long time; no retention
   policy exists and none is needed yet.

7. **One connection per write.** Simple and matches `database.py`'s existing style; ~150 short-lived
   connections per run, immaterial next to 50 HTTP fetches.

---

## 14. Explicit confirmation: qualification behaviour was not changed

**Nothing in the qualification model was modified.** Specifically, and by name from the brief:

- weights — unchanged
- thresholds (`QUALIFY_AT = 2`, `REJECT_BELOW = -2`) — unchanged
- hard rejections (H1–H5) — unchanged
- Lebanon gate rules (`lebanon_signals()`, `passes_lebanon_gate()`) — unchanged
- exclusion rules and `exclusions.json` — unchanged
- identity resolution logic — unchanged (`identity.py` not edited; the Laptops King ordering defect
  is **still present**, as instructed)
- discovery queries (`KEYWORDS`) — unchanged
- Shopify detection (`SHOPIFY_SIGNALS`, the `is_shopify` test) — unchanged
- scoring signals — unchanged, including the `.myshopify.com` bonus and the own-brand signal
- follower bands, Instagram extraction — unchanged
- no LLM added

**Evidence:**

1. `qualification.py`, `identity.py`, `exclusions.py`, `exclusions.json`, `blacklist.json` retain
   their pre-existing file timestamps (Aug 11 / Aug 31); none was opened for editing.
2. `ledger.py` imports only `json`, `sqlite3`, `uuid`, `datetime` and `database.DATABASE_NAME` —
   enforced by a static test.
3. All 179 pre-existing tests still pass unmodified.
4. Live re-scoring: **13/13** evaluated candidates reproduce their stored verdict exactly.
5. Behavioural cross-check against the diagnostic's independent measurements —
   `laptopsking.com` scored **−3 REJECTED** in the diagnostic probe and **−3 REJECTED**
   under the ledger; `mt-lebanon-book-cellar` rejected on the Lebanon gate in both.

The three edits outside `ledger.py` were audited for model impact and have none: `main.py` gained
ledger calls without touching a gate condition or its order; `scraper.py` gained a named constant
holding its existing value and a `sources` list that does not affect which candidates are returned
(two tests pin the returned set); `database.py` gained one new read-only helper.

---

## 15. Recommended next step

> ## Requalify with refetch, then hand-label the ledger. Specifically: (a) run `requalify.py --refetch --apply` so the 48 stale rows carry current signals, and (b) add a labelling surface over `candidate_ledger` — a `human_label` column plus a CSV export/import round-trip — and hand-label the accumulated candidates.

**Why this, before any model change.**

The ledger is now capturing evidence, but two gaps still block calibration, and neither is a model
change:

1. **The stored population is stale, and normal runs will not refresh it.** 18 of this run's 49
   candidates exited at `already_known` without re-evaluation. `welcomehomelb.com`,
   `karoutonlinelb.com` and `houseofappliances.co` — the three suspected false negatives — were
   skipped entirely and still carry verdicts computed from signals captured by the *old* Instagram
   extractor (flagged in `IMPLEMENTATION_REPORT_V2.md` §177 and never actioned). Any accuracy
   measurement taken today would be measuring stale inputs, not the current model.

2. **There is nowhere to put a human label.** The whole point of the ledger is to enable
   human-versus-model comparison, and that comparison needs the labels stored next to the
   candidates, versioned by run, not in a side spreadsheet that drifts.

Doing these two things converts the ledger from "we now retain the evidence" into "we can compute a
false-negative rate", which is the number every deferred decision depends on:

- Should the Lebanon gate recognise the `lb` suffix? → measurable once the 9 `lebanon_gate`
  rejections per run are labelled.
- Should the `.myshopify.com` bonus go? → measurable once `laptopsking.com`-shaped pairs accumulate.
- Should discovery go beyond Shopify? → measurable once the 18 `not_shopify` rows per run are
  labelled; the ledger already records that `tecno-lb.com` passes the Lebanon gate and is dropped
  purely for its platform.
- Should `site:myshopify.com Lebanon` be retired? → 8-of-10 foreign, and now provable by `GROUP BY`
  rather than by hand.

**Sequencing after that**, in the order the evidence supports — and none of it before the labels
exist:

1. Fix the identity-resolution ordering (the Laptops King defect). It is a genuine bug, it is now
   fully documented in the ledger, and fixing it does not change any weight.
2. Then, with a labelled set in hand, calibrate: the `lb`-suffix recogniser gap, the
   `.myshopify.com` bonus, the own-brand signal.
3. Then the discovery strategy decision (query set, and whether to go beyond Shopify).

**Do not skip to step 2.** Every item there moves real rows, and the reason the diagnostic
recommended the ledger first was that changes of that kind cannot currently be evaluated for
false-negative impact. That is still true until the labels exist.

---

## Appendix — useful queries

```sql
-- Everything that happened to one domain, across all runs
SELECT run_id, status, stage, decision, score, rejection_kind
FROM candidate_ledger WHERE domain = 'laptopsking.com' ORDER BY candidate_id;

-- Candidates that actually exercised the scoring model
SELECT domain, decision, score, rejection_kind, instagram_followers
FROM candidate_ledger WHERE qualification_ran = 1 ORDER BY score;

-- Query quality: which query yields which outcome
SELECT s.query, cl.status, COUNT(*) n
FROM candidate_discovery_sources s JOIN candidate_ledger cl USING (candidate_id)
GROUP BY s.query, cl.status ORDER BY s.query, n DESC;

-- The discarded non-Shopify population, with its Lebanon verdict
SELECT domain, business_name, lebanon_gate_passed
FROM candidate_ledger WHERE status = 'not_shopify';

-- Novelty decay across runs
SELECT run_id,
       SUM(status = 'already_known') known,
       COUNT(*) total,
       ROUND(100.0 * SUM(status = 'already_known') / COUNT(*), 1) pct_known
FROM candidate_ledger GROUP BY run_id;
```
