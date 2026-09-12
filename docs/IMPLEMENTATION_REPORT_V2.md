# NorthBound Lead Finder — Implementation Report V2

**Date:** 2026-08-31 · **Tests:** 179 passing (was 136) · **Scope:** two targeted fixes only

---

## 1. Executive Summary

Two improvements identified in the V1 report were implemented. **The qualification model was not touched.**

**1. Instagram handle extraction is now context-ranked instead of first-match.** The old code returned the first `instagram.com/...` string in the raw HTML, which on a page with an influencer grid is whichever third-party account appears first. It now scores every candidate handle on where it appears (footer/social block, `rel="me"`, structured metadata) and how closely it matches the business name, picks the highest-scoring candidate deterministically, and returns `None` when it cannot decide.

**2. Re-qualification now re-scores from stored signals with no network access.** `signals_json` was already written for every lead; `requalify.py` now reads it by default. A full pass over all 46 rows takes **0.86 seconds** instead of several minutes, and is deterministic. `--refetch` explicitly opts into live fetching.

**Why it mattered:** verified on real data, database row 15 ("Mazen Online") stored the handle `a.d.a.ybeauty` — an influencer featured on the page — and was scored with **that account's 9,366 followers**, landing at `REVIEW` (score 0). With the correct handle `mazenonline` and its real **65,000 followers**, the same business scores **−6 → REJECTED**. The old bug was feeding a stranger's follower count directly into qualification.

**Model untouched:** all weights, bands, thresholds, hard-rejection rules, the Lebanon gate, exclusion logic, identity resolution, and catalogue-size/website-quality neutrality are byte-for-byte unchanged (verified in §6).

---

## 2. Files Changed

**Modified**

| File | Change |
|---|---|
| `scraper.py` | Replaced `extract_instagram()` with a ranked implementation; added `instagram_candidates()`, `_handle_matches_business()`, `_clean_handle()`, `_social_context_score()`, `_normalize_handle_text()`, `_walk_same_as()`; added `NON_BUSINESS_INSTAGRAM_HANDLES` and `MIN_INSTAGRAM_CONFIDENCE`; extended `IGNORED_INSTAGRAM_PATHS`; re-added `json` and `SequenceMatcher` imports; `analyze_site()` now passes soup, business name and domain into extraction. |
| `qualification.py` | Extracted the verdict logic into `decide()` (single source of truth, shared by both paths); added `qualify_from_signals()`, `normalize_signals()`, `signals_are_usable()`, `SIGNAL_DEFAULTS`, `REQUIRED_SIGNAL_FIELDS`. **No weight, band or rule was altered.** |
| `requalify.py` | Offline is now the default: `evaluate_row()` reads `signals_json` unless `--refetch` is passed; added `stored_signals()`; added the `--refetch` flag; run header reports which source is in use. |

**Created**

| File | Purpose |
|---|---|
| `tests/test_instagram.py` | 23 tests for handle extraction and ranking. |
| `tests/test_requalify.py` | 20 tests for offline re-scoring, `--refetch`, legacy signals, DB preservation. |
| `IMPLEMENTATION_REPORT_V2.md` | This report. |

**Deleted:** none. No existing test file was modified. No database schema change.

---

## 3. Instagram Extraction

### How the old implementation worked

```
for handle in INSTAGRAM_PATTERN.findall(html):   # raw HTML, in document order
    skip post/reel/explore paths
    return handle                                 # first survivor wins
```

### What was wrong

It had no concept of *whose* account it found. On a page featuring influencers, affiliates, a theme author or an app vendor, the first match is usually not the merchant. Row 15 of the live database is the proof: business "Mazen Online", stored handle `a.d.a.ybeauty`. Because follower count is the highest-weighted signal in the model (up to ±8), a wrong handle silently corrupts the most influential input to qualification.

### How the new implementation works

`instagram_candidates(soup, html, business_name, domain)` collects candidates from four sources and scores each:

| Evidence | Points |
|---|---|
| Link inside `<footer>`/`<header>`/`<nav>` or an element whose class/id matches `social\|footer\|contact\|follow` | **+4** |
| Link carries `rel="me"`, or class/id/aria-label/title matching `instagram\|social\|follow` | **+3** |
| Handle appears in structured metadata — JSON-LD `sameAs`, `og:see_also`, `<link rel="me">` | **+3** |
| Handle matches the business name or first domain label exactly or by containment | **+3** |
| Handle merely resembles the name (`SequenceMatcher` ≥ 0.80) | **+1** |
| Handle only appears loose in the raw HTML | **0** |

**Context scores are taken as a maximum, never summed.** This was a bug found during implementation: with summation, `a.d.a.ybeauty` — repeated five times in an influencer grid — scored **70** and beat the correct `mazenonline` at 10. Repetition is a popularity artifact, not evidence of ownership. With max-per-source scoring, `mazenonline` scores 10 and `a.d.a.ybeauty` scores 7.

Candidates are dropped outright if they are post/reel/system paths (`p`, `reel`, `explore`, `accounts`, `stories`, …), fail the username format check, or appear in `NON_BUSINESS_INSTAGRAM_HANDLES` (`shopify`, `instagram`, `klaviyo`, `gorgias`, `judgeme`, `printful`, …).

Ranking is `(-score, first-appearance index)` — fully deterministic, verified by a test that repeats the call five times.

### Ambiguous cases

Two guards, both resolving to `None`:

1. **Insufficient evidence** — the top candidate scores below `MIN_INSTAGRAM_CONFIDENCE = 3`. A handle mentioned only in a script blob, unrelated to the business, is not claimed.
2. **Unbroken tie** — the runner-up ties the leader and the leader has no name match. Two equally-placed social links (an agency and a photographer, say) are genuinely undecidable.

`None` means "unknown", which the scorer already treats as neutral — so an unresolved handle costs the lead nothing.

### Verification against real sites

All 18 captured fixture stores were re-extracted from the live web: **every one produced the identical handle** — zero recall regression. On `mazenonline.com` the result changed from `a.d.a.ybeauty` to `mazenonline`. On `istahly.com` a third-party candidate (`abuilds.web`, the site's web agency) was correctly ranked below `istahly.lb` (4 vs 10).

### Remaining limitations

- If a merchant's own handle appears **only** as a bare mention with no social context and no name resemblance, it now returns `None` where the old code returned it by luck. This is intentional and safe, but it slightly reduces follower coverage.
- Name matching fails when a handle is unrelated to the business name (e.g. a shop called "Beirut Sweets" posting as `@sweettoothlb`); such a handle is still accepted if it sits in the footer, which is the correct outcome, but it cannot be *confirmed*.
- Extraction still runs on the homepage only. A merchant linking Instagram solely from a `/contact` page is not covered.
- The follower count itself is still an unauthenticated scrape of instagram.com (unchanged from V1).

---

## 4. Signal Caching / Re-qualification

### How stored signals are used

`collect_signals()` output is already persisted to `leads.signals_json` on insert. Re-scoring now calls `qualification.qualify_from_signals(signals, exclusions)`, which validates the record, fills defaults, and runs the **same** `hard_rejections()` → `score_lead()` → banding path via the shared `decide()` helper. Live discovery and offline re-scoring cannot diverge, because they execute the same function.

### Exact CLI behaviour

| Command | Source | Network | Writes |
|---|---|---|---|
| `python requalify.py` | stored `signals_json` | **none** | no (dry run) |
| `python requalify.py --apply` | stored `signals_json` | **none** | yes |
| `python requalify.py --refetch` | live sites | yes | no (dry run) |
| `python requalify.py --apply --refetch` | live sites | yes | yes |
| `--report out.csv` | — | — | writes a before/after CSV |
| `--limit N` | — | — | processes the first N rows |

The run header states which source is active: `[DRY RUN] re-qualifying 46 leads from stored signals (offline)`.

**Measured:** offline full pass over 46 rows = **0.86 s**. `--refetch` over 4 rows ≈ 30 s (~3 HTTP requests per row). Both paths produced **identical decisions** on the same rows.

### Missing / legacy signals

- No `signals_json`, or unparseable JSON → row is **left untouched**, reported as `no stored signals (run with --refetch)`. It is never rejected on absent evidence.
- Record missing `lebanon_signals` or `business_name` → `qualify_from_signals()` raises `ValueError`, handled the same way.
- Fields added after a record was written are filled from `SIGNAL_DEFAULTS`, and **every default is the neutral value**, so an old record degrades to "unknown" rather than to a penalty.
- `lebanon_signals` written before the CLAIM tier existed is normalised to include an empty `claim` list.

---

## 5. Tests

**Added — `tests/test_instagram.py` (23):** business handle beats repeated influencer handles; repetition does not accumulate score; first-raw-match is not chosen; footer/social link identified; name-matching handle preferred; country-suffix handle (`mikesportleb` ↔ `Mike Sport`); JSON-LD `sameAs`; `rel="me"`; dotted handle vs undotted name; no Instagram → `None`; invalid URL → `None`; 5 parametrized post/system paths → `None`; platform accounts ignored; unrelated no-context handle → `None`; tied third-party links → `None`; tie broken by name match; deterministic ranking; evidence reporting; fixture-handle guard.

**Added — `tests/test_requalify.py` (20):** offline run raises if any network call is attempted; stored signals reproduce outcome, score **and reasons** exactly; 6 parametrized fixtures replay identically through a JSON round-trip; `--refetch` calls `analyze_site`; default path does **not** call it; row without signals left alone; corrupt JSON left alone; incomplete signals raise; missing optional fields default to neutral; `normalize_signals` fills every default; legacy `lebanon_signals` without `claim`; re-qualification never deletes rows; `set_decision` preserves existing signals; JSON round-trip fidelity; structural check that no `DELETE FROM`/`DROP TABLE` exists in `requalify.py`, `database.py` or `main.py`.

**Modified:** none. No existing test required changes, which is itself evidence the model was untouched.

| File | Tests |
|---|---|
| `tests/test_qualification.py` | 45 |
| `tests/test_exclusions.py` | 28 |
| `tests/test_identity.py` | 25 |
| `tests/test_instagram.py` | **23 (new)** |
| `tests/test_requalify.py` | **20 (new)** |
| `tests/test_lebanon.py` | 19 |
| `tests/test_pipeline.py` | 19 |

```
Command:  python -m pytest -q
Collected: 179
Passed:    179
Failed:    0
Skipped:   0
Errors:    0
Warnings:  0
Runtime:   ~11 seconds
```

---

## 6. Verification

| Check | Result |
|---|---|
| **Scoring weights changed?** | **No.** −8/−5/−2/0/+1/+2 follower bands, −3 corporate terms, −3 legal entity, −3 store locator, −3 hreflang, −1 Sentry, +2 own brand, +2 free email, +2 no-custom-domain, +1 WhatsApp, +1 no tooling, +1 city — all identical to V1. |
| **Thresholds changed?** | **No.** `QUALIFY_AT = 2`, `REJECT_BELOW = -2`, `FOLLOWER_HARD_REJECT = 250_000`, `MONO_BRAND_SHARE = 0.80`. |
| **Hard-rejection rules changed?** | **No.** All five (H1–H5) unchanged; `PLACEHOLDER_NAMES` still 9 entries. |
| **Lebanon gate / exclusions / identity changed?** | **No.** Untouched. |
| **Database schema changed?** | **No.** 22 columns, unchanged. `PRAGMA integrity_check: ok`. 46 rows, all with `reasons`, all 46 with `signals_json`. |
| **Normal re-qualification made network requests?** | **No.** A test monkeypatches `requests.get`, `analyze_site`, `fetch_products_json` and `fetch_instagram_followers` to raise on call; the offline path passes. Full 46-row run completes in 0.86 s. |
| **`--refetch` works?** | **Yes.** Verified live on 4 rows; produced the same decisions as the offline path. |
| **Instagram regression tests pass?** | **Yes.** 23/23. All 18 live fixture stores still resolve to the same handle. |
| **End-to-end pipeline** | Ran real `main()` into a temp DB: Qatfa qualified (+5), Marie France rejected, Mazen Online rejected (−6) with the corrected handle. |

---

## 7. Remaining Risks / Limitations

**Stale stored signals — the most actionable item.** All **46** rows in `northbound.db` have `signals_json` captured with the **old** Instagram extractor. Offline re-qualification faithfully replays those signals, including any wrong handle. Row 15 is a live example: it still stores `a.d.a.ybeauty` / 9,366 followers and sits at `REVIEW`, when the corrected handle gives 65,000 followers and `REJECTED`. **A one-time `python requalify.py --refetch --apply` is needed to refresh them.** I did not run it, as it changes stored decisions and you asked for no further changes.

**Instagram-specific:**
- Follower counts are an unauthenticated scrape; if Instagram requires auth or changes markup, the strongest signal degrades to neutral and more leads land in REVIEW.
- A merchant whose handle bears no resemblance to its name and sits outside any social block will now resolve to `None` — a lost signal, not a wrong one.
- Homepage-only extraction; a handle linked only from `/contact` is missed.
- Follower counts drift over time, so a stored signal is a snapshot, not a current fact.

**False positives still possible** (unchanged from V1): Fattal Online scores −1 (REVIEW, not REJECTED); Babyjem qualifies despite a distributor email; a large brand with a modest following, its own vendor name and a single locale would score positively.

**False negatives still possible** (unchanged from V1): a fast-growing SME above ~60K followers takes −5; an SME that looks like a mono-brand outlet is hard-rejected by H1 with no appeal; the Lebanon gate requires explicit evidence.

**Not addressed by this work:** the weights remain fitted on the same 18-store sample that validates them. Nothing here improves generalisation — it improves the *quality of one input* and the *cost of re-scoring*.

---

## 8. Recommended Next Step

**Run `python requalify.py --refetch --apply --report refresh.csv` once, then begin calibration.**

The refetch is now a prerequisite rather than an optional cleanup: every stored signal record predates the Instagram fix, so offline re-scoring — and any threshold experiment you run against it — would be replaying handles that may belong to the wrong accounts. Refreshing once puts correct data behind all 46 rows, and the CSV shows exactly which decisions move (row 15 will at minimum go `review → rejected`).

After that, calibration becomes cheap in a way it was not before: with clean stored signals, `requalify.py` re-scores the whole table in under a second with no network traffic, so you can sweep thresholds and inspect the effect immediately. That is the natural moment to gather the ~30 fresh, hand-labelled candidates recommended in V1 — the model's weights are still fitted to the sample that validates them, and that remains the largest open question about the system.
