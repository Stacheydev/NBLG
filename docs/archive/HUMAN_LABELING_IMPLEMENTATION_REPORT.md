# NorthBound Lead Finder — Human-Labeling / Calibration Layer Implementation Report

**Date:** 2026-09-01
**Phase:** measurement infrastructure only — no model change
**Preceding documents:** `CALIBRATION_DIAGNOSTIC_REPORT.md`, `CANDIDATE_LEDGER_IMPLEMENTATION_REPORT.md`

---

## 0. Precondition check: was the stale-signal refresh run?

**Yes — verified before any work started.**

`requalify.py --refetch --apply --report refresh.csv` ran at **2026-09-01 19:49–19:53 UTC**,
after the ledger validation run (19:25–19:28 UTC).

Evidence, not inference — comparing the pre-ledger backup against the current database, live
follower counts drifted on **16 rows**, which only a genuine network refetch can produce:

```
#  5 sawakart.com                 9233 -> 9253      # 22 fattal-online         49000 -> 50000
#  6 swarovski.com.lb             6098 -> 6112      # 27 outgeeked.net          5944 ->  5945
# 10 qatfalebanon.com              575 ->  574      # 41 beytmod.com            5230 ->  5229
# 11 welcomehomelb.com            7094 -> 7098      # 42 lebanonstore.net       3568 ->  3555
# 13 alorabrands.com              5900 -> 5899      # 43 scentsofpalestine.com  5873 ->  5874
# 18 lightwavelb.com               235 ->  238      # 48 fattalonline.com      49000 -> 50000
# 19 houseofappliances.co         4381 -> 4383      # 51 wavytalkshoplebanon    1021 ->  1009
                                                     # 55 mjboardgames.com       8839 ->  8844
                                                     # 62 gmbji5-mp.myshopify    7918 ->  7920
```

`signals_json` changed on 20 of 48 rows; all 50 leads carry a `qualified_at` in that window;
`refresh.csv` was rewritten with 50 rows and **zero** `unchanged (…)` error rows.

**No decision changed as a result of the refresh** — it confirmed the model's stability on fresh
inputs rather than moving anything. Proceeded as instructed.

### One finding worth recording (not acted on)

`requalify.evaluate_row()` overrides `analysis["business_name"]` with the stored name *after*
`analyze_site()` has already computed `lebanon_signals` from the name it extracted itself. So for
`welcomehomelb.com`, the refetch stored the correct name `"Welcome Home Lebanon"` but the Lebanon
signals were still computed against `"Cookware"`, and the `lebanon_in_business_name` claim never
fired. The row is still `REJECTED` on an empty gate.

**Not fixed** — it would change Lebanon-gate outcomes, which this phase forbids. It is now
visible in the labeling surface, which is where it belongs.

---

## 1. What was implemented

A calibration layer over the candidate ledger, in two files plus tests:

| File | Role |
|---|---|
| `calibration.py` (new) | Data layer: schema, label read/write, the queue, all reporting. Scores nothing. |
| `label.py` (new) | Reviewer CLI: `queue`, `show`, `next`, `set`, `export`, `import`, `open`, `report`. |
| `tests/test_calibration.py` (new) | 49 tests. |
| `northbound.db.bak-precalibration-20260901T201327Z` | Pre-migration backup. |

**Nothing else was modified.** `qualification.py`, `identity.py`, `exclusions.py`,
`exclusions.json`, `scraper.py`, `main.py`, `database.py`, `ledger.py`, `requalify.py` and every
pre-existing test are untouched.

The label answers the real-world question, deliberately **not** the model's proxy:

> Is this a Lebanese SME / independent local business that would actually be a valuable
> NorthBound lead?

A candidate the Shopify gate dropped, the Lebanon gate hard-rejected, or discovery skipped as
already-known can still be `YES`. That asymmetry is the whole point — it is what makes a
false-negative rate measurable.

---

## 2. Why this architecture

**A separate table, not columns on `candidate_ledger`.** A human opinion is not evidence about
what the pipeline did. Mixing them would corrupt the record this layer exists to audit, and would
mean mutating ledger rows that the previous phase's tests guarantee are immutable. `leads` is
untouched for the same reason, and because the ledger — not `leads` — is the population that
contains rejections, non-Shopify sites and Lebanon-gate failures.

**A CLI, not a web UI.** The repo is stdlib-only tooling: `argparse` (`requalify.py`), `csv`
(`refresh.csv`, `export_leads_csv`), no web framework, no frontend, no templates, no JS anywhere.
The dependency list is `requests`, `bs4`, `ddgs`. A local web UI would mean adding a server and a
dependency to a project whose entire toolchain is `python x.py`. The CLI plus a CSV round-trip
covers both jobs: one-at-a-time review, and bulk labelling in Excel.

**Two label sources, kept apart.** A candidate that exited at the duplicate gate carries no
evidence of its own — the pipeline skipped it precisely because the business was already known.
Its evidence lives on the `leads` row. Without that, a reviewer would be asked to judge a bare
domain, and the three suspected false negatives (`welcomehomelb.com`, `karoutonlinelb.com`,
`houseofappliances.co`) would be the *least* labelable rows in the set. So the layer reads the
linked lead for **display and verdict**, records `verdict_source` as `ledger` or `lead`, and never
overwrites the ledger's own fields (`decision`, `qualification_ran` stay exactly as recorded).
This is a read; nothing in `leads` is ever written.

**No sampling methodology invented.** The brief asked for simple, deterministic and explainable.
The queue is a fixed tier assignment plus a round-robin. No randomness, no weighting, no
statistics the data cannot yet support.

---

## 3. Exact schema changes

One new table. `CREATE TABLE IF NOT EXISTS` only — **no `ALTER`, no `DROP`, no `DELETE`, no write
to `leads` or `candidate_ledger`**, enforced by a static regex test.

```sql
CREATE TABLE IF NOT EXISTS candidate_labels (
    label_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id  INTEGER NOT NULL UNIQUE,   -- one current opinion per candidate
    domain        TEXT,                      -- denormalized: business-level views
    human_label   TEXT NOT NULL,             -- YES | NO | UNCERTAIN | UNLABELED
    human_reason  TEXT,                      -- controlled vocabulary
    human_notes   TEXT,                      -- free text
    reviewer      TEXT,
    labeled_at    TEXT NOT NULL,             -- first review
    updated_at    TEXT NOT NULL,             -- last revision
    FOREIGN KEY (candidate_id) REFERENCES candidate_ledger (candidate_id)
);
CREATE INDEX IF NOT EXISTS idx_label_value  ON candidate_labels (human_label);
CREATE INDEX IF NOT EXISTS idx_label_domain ON candidate_labels (domain);
```

`set_label()` upserts on `candidate_id`: revising an opinion overwrites it and refreshes
`updated_at`, but preserves the original `labeled_at`.

---

## 4. Label states and semantics

| Label | Meaning | Counts toward binary accuracy? |
|---|---|---|
| `YES` | Clearly a target Lebanese SME / independent local business | **yes** |
| `NO` | Clearly not a target | **yes** |
| `UNCERTAIN` | Genuinely ambiguous or insufficient evidence | **no** — excluded, never folded into `NO` |
| `UNLABELED` | Not yet reviewed (also the absence of a row) | **no** — excluded |

`UNCERTAIN` is a first-class answer. A reviewer forced to guess produces calibration data that is
worse than none, so the layer never coerces it into a boolean; it is reported in its own column
and excluded from the matrices.

### Reason codes (controlled vocabulary, so "why" is countable)

```
lebanese_sme               Independent Lebanese SME - a real NorthBound target
large_brand                Large brand, chain or enterprise retailer
franchise_or_distributor   Country franchise / exclusive distributor of a foreign brand
foreign_business           Not a Lebanese business
marketplace_or_directory   Marketplace, aggregator or directory
duplicate_or_alias         Same business as another candidate/lead
not_a_business             Placeholder, demo, parked or non-commercial
insufficient_evidence      Not enough evidence to decide either way
other                      Something else - see notes
```

Free text goes in `human_notes`, which survives newlines, quotes and non-ASCII intact (tested).
An unknown label or reason code is rejected rather than stored.

---

## 5. Calibration queue methodology

Deterministic and fully reproducible: **the same database always produces the same queue**, and
every position carries a `tier` and a plain-English `queue_reason`.

**Step 1 — one tier per candidate, first rule wins.** Tiering uses the *effective* verdict, from
the ledger when qualification ran in that run, otherwise from the linked lead. A candidate skipped
as already-known still has a Lebanon-gate rejection standing against it; burying it in
"never evaluated" would hide exactly the errors the queue exists to surface.

| Tier | Calibration question it answers |
|---|---|
| `near_boundary` | one signal away from a different verdict — where thresholds bite |
| `lebanon_gate_reject` | the largest suspected false-negative source |
| `model_qualified` | tests for false positives |
| `not_shopify` | dropped for its platform before qualification ever ran |
| `model_review` | is REVIEW the right answer for this shape? |
| `score_reject` | rejected by the weighted score, not a hard rule |
| `conflicting_signals` | evidence that points in opposite directions |
| `hard_reject` | hard-rejected for a reason other than the Lebanon gate |
| `never_evaluated` | no model opinion from either source |

`near_boundary` uses `QUALIFY_AT` and `REJECT_BELOW` **imported from `qualification.py`**, never
copied, so it cannot drift out of sync with the real bands. Hard rejections are excluded from it:
their score of `0` is a placeholder meaning "never scored", not "two points from qualifying".

**Step 2 — rotate queries inside a tier**, so one query cannot monopolise it.

**Step 3 — round-robin across tiers** in a fixed order that deliberately alternates "model said
yes" with "model said no". The head of the queue can never be all model survivors.

Live proof from the real database (positions 1–7):

```
 1  near_boundary        MYHOLDAL                 review  -2
 2  lebanon_gate_reject  House of Appliances      rejected +0     <- suspected false negative
 3  model_qualified      Adaline Lebanon          qualified +5
 4  not_shopify          ubuy.com.lb              (never evaluated)
 5  model_review         Carpisa Lebanon          review  +0
 6  score_reject         SuperDokan               rejected -4
 7  hard_reject          AVON Lebanon             rejected +0
```

Seven different tiers in the first seven positions.

**Scope control.** Labelled candidates are excluded by default; so are candidates whose *domain*
already carries a decided label in any run, so a business is not re-presented run after run.
`--include-labeled` and `skip_labeled_domains=False` re-open the whole population.

---

## 6. Reporting capabilities

`python label.py report` (add `--json` for machine-readable, `--run` to scope to one run).
Every numbered question in the brief maps to a section:

| # | Question | Section |
|---|---|---|
| 1 | Counts by label | 1. Label coverage |
| 2–3 | Human YES/NO × model qualify/review/reject | 2–3. Cross-tab + two confusion matrices |
| 4 | Human YES but model REJECTED | 4 (named list) |
| 5 | Human NO but model QUALIFIED | 5 (named list) |
| 6 | Decisions closest to a band boundary | 6 |
| 7 | Reasons associated with FN / FP | 7 |
| 8 | Discovery queries producing most human YES | 8 |
| 9 | Where candidates are lost | 9 (`not_shopify`, `rejected:lebanon_gate`, `rejected:score`, `excluded`, `already_known`, …) |
| 10 | Unlabelled / unusable count | 10. Calibration readiness |

**Two confusion matrices, both reported.** Strict treats only `QUALIFIED` as a model positive;
lenient counts `QUALIFIED or REVIEW`. Which is right depends on how REVIEW is worked in practice,
so the layer refuses to decide for you.

**Three exclusion buckets, reported explicitly rather than silently dropped:** `unlabeled`,
`uncertain`, `never_evaluated`. A candidate with no model verdict from either source is excluded
from the matrix and surfaced in its own list — counting a non-Shopify site as a "rejection" would
blame the scorer for a decision discovery made.

Section 7 is association, not causation: a reason line appearing beside many false negatives is a
lead to investigate, not proof it caused them.

---

## 7. Tests

**212 passing before this phase → 261 after** (49 added, 0 failures, 0 skips, ~130s).
No network; a static test asserts the CLI makes no `requests` call.

| Brief requirement | Test(s) |
|---|---|
| labels can be created | `test_labels_can_be_created` |
| labels can be updated | `test_labels_can_be_updated_without_duplicating` |
| tied to the correct candidate | `test_labels_are_tied_to_the_correct_candidate`, `test_a_label_must_attach_to_a_real_candidate` |
| candidate data not mutated | `test_labeling_does_not_mutate_the_candidate` |
| existing leads not mutated | `test_labeling_does_not_mutate_existing_leads`, `test_enrichment_never_writes_to_leads` |
| all four label states | `test_all_four_label_states_round_trip` |
| notes survive round trip | `test_notes_survive_a_database_round_trip` |
| deterministic queue ordering | `test_queue_is_deterministic`, `test_queue_carries_an_explanation_for_every_position` |
| confusion computed correctly | `test_confusion_matrix_counts_model_vs_human_correctly`, `test_lenient_matrix_counts_review_as_a_positive` |
| unlabeled excluded from accuracy | `test_unlabeled_candidates_are_excluded_from_accuracy` |
| UNCERTAIN excluded from binary accuracy | `test_uncertain_is_excluded_from_binary_accuracy` |
| no destructive operations | `test_calibration_schema_is_additive_only`, `test_report_is_read_only` |
| existing tests still pass | full suite: 261 passed |

Additional coverage beyond the brief: queue is not only model survivors; tiers interleave rather
than group; queue rotates between discovery queries; already-labelled domains are not
re-presented; hard rejections are not treated as near-boundary; conflicting signals are flagged;
every candidate lands in exactly one tier; lead-sourced evidence and verdicts; `legacy_unreviewed`
and `duplicate` are not treated as model verdicts; CSV round-trip; blank CSV rows are skipped, not
recorded as reviewed; bad CSV rows are reported without aborting; the module does no scoring; the
CLI makes no network calls.

**A test-performance note:** the population fixture was doing ~20 separate sqlite commits per
test (~5s each on this filesystem). It now builds the population once into a template database and
copies the file per test — identical coverage, 210s → 22s for the calibration module.

---

## 8. Database integrity verification

Backup taken before the schema change: `northbound.db.bak-precalibration-20260901T201327Z`.

```
PRAGMA integrity_check    -> ok
PRAGMA quick_check        -> ok
PRAGMA foreign_key_check  -> clean

tables: candidate_discovery_sources, candidate_labels, candidate_ledger,
        discovery_runs, leads, sqlite_sequence
```

Row-by-row comparison of every pre-existing table against the backup:

```
leads                        backup= 50  current= 50  identical=True
candidate_ledger             backup= 49  current= 49  identical=True
candidate_discovery_sources  backup= 50  current= 50  identical=True
discovery_runs               backup=  1  current=  1  identical=True
candidate_labels rows in production: 0
```

Schema creation was run twice to confirm idempotence. **Production carries zero labels** — the
demonstration in §10 was performed on a throwaway copy, so your own review is not pre-empted.

---

## 9. Confirmation that qualification behaviour was untouched

**No model change of any kind.** By name from the brief: weights, thresholds, hard-rejection
rules, the Lebanon gate, exclusion logic, identity resolution, discovery queries and scope, and
the Shopify gate are all **unchanged**. No lead decision was modified. No candidate was removed
from the ledger.

Evidence:

1. `qualification.py`, `identity.py`, `exclusions.py`, `exclusions.json`, `scraper.py`, `main.py`,
   `database.py`, `ledger.py` and `requalify.py` were **not opened for editing** in this phase.
2. `calibration.py` contains none of `score_lead`, `hard_rejections`, `FOLLOWER_BANDS`,
   `passes_lebanon_gate`, `collect_signals`, `def decide`, or `import scraper` — asserted by
   `test_calibration_module_does_no_scoring`. Its only model import is
   `from qualification import QUALIFY_AT, REJECT_BELOW`, used to locate band edges, never to
   compute a verdict.
3. A static regex test forbids `DROP TABLE`, `DROP INDEX`, `ALTER TABLE`, `DELETE FROM`,
   `UPDATE leads`, `INSERT INTO leads`, `UPDATE candidate_ledger` and `INSERT INTO candidate_ledger`
   anywhere in `calibration.py`.
4. All 212 pre-existing tests pass unmodified.
5. Every pre-existing table is byte-identical to the pre-migration backup (§8).

---

## 10. Real-data calibration snapshot

### 10.1 Production, as it stands (zero labels)

```
1. LABEL COVERAGE
   YES 0   NO 0   UNCERTAIN 0   UNLABELED 49
   candidates                      49
   model-evaluated                 31        <- 13 from this run + 18 from linked leads
   usable for binary calibration    0

9. WHERE CANDIDATES ARE LOST
   stop point                    total
   already_known                    18
   not_shopify                      18
   rejected:lebanon_gate             9
   rejected:score                    2
   qualified                         1
   review                            1

   QUEUE TIERS across the population
       18  not_shopify           dropped for its platform before qualification ran
       12  lebanon_gate_reject   the largest suspected false-negative source
        8  near_boundary         one signal away from a different verdict
        5  model_qualified       tests for false positives
        3  hard_reject
        2  model_review
        1  score_reject
```

**Calibration readiness: 0 usable, 30 short of the target.** That is the honest state — the
surface is built, the labelling has not been done.

Note the effect of lead-sourced verdicts: model-evaluated rose from **13 to 31**, because 18
already-known candidates carry a verdict on the linked lead. Those 18 were the least labelable
rows in the population and are now the most informative.

### 10.2 Demonstration that the measurement machinery works

Performed on a **throwaway copy** (`scratchpad/demo.db`), reusing the hand-classification already
published in `CALIBRATION_DIAGNOSTIC_REPORT.md` §6. **These labels are not in production.**

```
24 labels applied:  YES 8   NO 13   UNCERTAIN 3   UNLABELED 25
usable for binary calibration: 19

                     qualified         review       rejected  not_evaluated
   YES                       3              1              4              0
   NO                        0              1             10              2
   UNCERTAIN                 1              2              0              0

   strict  (model positive = QUALIFIED)
     TP 3   FP 0   FN 5   TN 11   (n=19)
     precision 100%   recall 38%   accuracy 74%
     excluded: {'unlabeled': 25, 'uncertain': 3, 'never_evaluated': 2}

   lenient (model positive = QUALIFIED or REVIEW)
     TP 4   FP 1   FN 4   TN 10   (n=19)
     precision 80%   recall 50%   accuracy 74%

4. HUMAN YES but MODEL REJECTED  (false negatives)
   #  1 karoutonlinelb.com     already_known  +0   lebanese_sme
   # 21 welcomehomelb.com      already_known  +0   lebanese_sme
   # 30 laptopsking.com        rejected       -3   lebanese_sme
   # 36 houseofappliances.co   already_known  +0   lebanese_sme

5. HUMAN NO but MODEL QUALIFIED  (false positives)
   (none)

7. REASONS BESIDE FALSE NEGATIVES
     3x  HARD REJECT: failed Lebanon gate: no strong or corroborated signal
     1x  -5 very_large_following (76,000 followers)
```

The shape is exactly what the diagnostic predicted and could not previously quantify: **precision
is high and recall is low.** The model is not letting brands through; it is losing real leads, and
the Lebanon gate accounts for three of four false negatives.

**Treat these numbers as a smoke test of the instrument, not a result.** n=19 on labels I assigned
is not a calibration set — the labels must be yours.

### 10.3 A worked evidence card

```
#21  Welcome Home Lebanon
  domain        : welcomehomelb.com
  found by      : "Lebanon" "powered by Shopify" (position 1)
  PIPELINE      : ALREADY_KNOWN (stopped at duplicate_check)
  MODEL VERDICT : REJECTED (score +0)   <- stored lead #11
                    HARD REJECT: failed Lebanon gate: no strong or corroborated signal
                    (evidence below comes from the stored lead - this run skipped it as already-known)
  EVIDENCE
    instagram   : welcome_homelb
    industry    : home & furniture
    lebanon     : no signals
    catalogue   : dominant vendor 'Welcome Home Lebanon' at 97%, 3 vendors
  QUEUE TIER    : lebanon_gate_reject - hard-rejected by the Lebanon gate (stored lead #11)
  HUMAN LABEL   : UNLABELED
```

---

## 11. Limitations

1. **One label per candidate, not per business.** A business rediscovered in a later run gets a
   new `candidate_id`. The queue skips domains that already carry a decided label, so effort is
   not wasted, but there is no explicit "business" entity. Fine at this scale; revisit if a
   business's correct label ever changes over time.

2. **Single reviewer assumed.** `reviewer` is recorded, but the unique constraint is on
   `candidate_id`, so two reviewers overwrite each other rather than producing an inter-rater
   comparison. Adding that now would be complexity without a second reviewer to justify it.

3. **`already_known` candidates are judged on stored lead evidence,** which is as fresh as the last
   `requalify --refetch` (today). If a later run's ledger and the lead disagree, the card shows the
   lead's verdict with `verdict_source=lead`. It is labelled, not hidden.

4. **Evidence quality is inherited.** `ubuy.com.lb` has `business_name = "Just a moment"` — a
   Cloudflare interstitial captured as the business name. The labelling surface shows what was
   captured; it does not repair extraction. Reviewers will meet a handful of these.

5. **No live re-fetch during review, by design.** `label.py open` prints the URLs for you to look
   at yourself. Re-fetching would judge a different page than the model judged.

6. **Section 7 is association only.** With n small, a reason line's co-occurrence with false
   negatives is a hypothesis, not a finding.

7. **The population is still one run deep.** 49 candidates, of which 18 are non-Shopify and cannot
   enter a strict confusion matrix at all. Reaching ~30 *model-evaluated* labelled candidates is
   achievable today (31 are model-evaluated); reaching 30 across a *diverse* population needs more
   runs.

---

## 12. Recommended next engineering step

> ## Label the queue. Run `python label.py export --csv calibration_round1.csv --limit 40`, fill in the `human_label` column, and `python label.py import --csv calibration_round1.csv`. Nothing else should be built until that CSV comes back filled.

**Why this and not more code.**

The measurement system is complete and verified. Every remaining question on the roadmap is now
blocked on exactly one input that only you can supply — and each is a question the report will
answer mechanically once the labels exist:

- *Should the Lebanon gate recognise the `lb` suffix?* → §4 and §7 will show how many of the 12
  `lebanon_gate_reject` candidates are human `YES`, and whether that reason line dominates the
  false negatives.
- *Should the `.myshopify.com` bonus go?* → §6 lists `laptopskinglb-961` at `-1` and
  `laptopsking.com` at `-3`; label both and the bonus's effect becomes a measured number.
- *Is `REVIEW` right for the Carpisa shape?* → §2–3 will show where `UNCERTAIN` and `NO` land
  against `review`.
- *Should discovery go beyond Shopify?* → §9's `not_shopify` row has 18 candidates; the human
  `YES` count in it is the entire business case.
- *Should `site:myshopify.com Lebanon` be retired?* → §8 gives human YES per query directly.

**Target for round one: the top 40 of the queue.** That is enough to fill every tier, and the
queue is already built to guarantee the sample is not all model survivors — 18 non-Shopify, 12
Lebanon-gate rejections, 8 near-boundary, 5 qualified.

**Two cautions for the labelling itself.** Label the *business*, not the model's verdict — the
report is worthless if the labels were anchored on the score you can see on the card. And use
`UNCERTAIN` freely: it is excluded from accuracy rather than folded into `NO`, so an honest
"I don't know" costs nothing and a forced guess costs a lot.

**After the labels are in**, in this order — and only then:

1. Read the report and decide which single rule the evidence indicts most strongly.
2. Fix the identity-resolution ordering (the Laptops King defect) — a genuine bug, independent of
   any weight, and already fully documented.
3. Calibrate one rule at a time, re-running `label.py report` after each to confirm the change
   moved the numbers in the intended direction and did not trade false negatives for false
   positives.

**Do not skip to step 3.** The demonstration in §10.2 shows precision 100% / recall 38% on labels
I assigned — a shape that would tempt an immediate loosening of the Lebanon gate. That gate is
also the only thing keeping the Pennsylvania, Texas and Hong Kong stores out. The number that
justifies touching it has to come from your labels, not mine.

---

## Appendix — command reference

```bash
# See what to review, and why each candidate is there
python label.py queue --limit 40

# Full evidence for one candidate (add --signals for the raw record)
python label.py show 30

# Walk the queue one at a time
python label.py next --count 5

# Record a judgement
python label.py set 30 --label YES --reason lebanese_sme \
                       --notes "Beirut electronics retailer, not a chain" --reviewer hadi

# Bulk: export, fill the human_label column in Excel, import back
python label.py export --csv calibration_round1.csv --limit 40
python label.py import --csv calibration_round1.csv --reviewer hadi

# The URLs, to look at yourself (does not fetch)
python label.py open 30

# The calibration report (--json for machine-readable, --run to scope)
python label.py report
```

Blank rows in an imported CSV are **skipped, not recorded as reviewed**, so a half-finished pass
is safe to import.
