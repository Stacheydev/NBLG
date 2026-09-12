# NorthBound Lead Finder — Qualification System Implementation Report

**Date:** 2026-08-31 · **Tests:** 136 passing · **Status:** implemented, tested, applied to the live database

---

## 1. Executive Summary

The lead finder previously asked three questions: *is it Shopify, does the page mention Lebanon, and is the name on a blacklist?* A large brand answers all three **better** than a small one, so Lacoste, Marie France and Mike Sport scored as well as legitimate SMEs. There was no company-size qualification of any kind — the only barrier was a hand-typed list of brand names, which could also be silently disabled by running the script from the wrong directory.

This implementation replaces that with a four-stage qualification system — **Lebanon gate → hard rejections → weighted score → banding** — producing `QUALIFIED` / `REVIEW` / `REJECTED` with a recorded score and written reasons for every lead.

Problems solved:

- **No size qualification** → weighted scoring built on measured signals, chiefly Instagram follower count and `products.json` vendor identity.
- **Blacklist as primary filter** → demoted to a safety net (~5% of decisions); scoring does the work.
- **Silent blacklist failure** → absolute path, fails loudly, aborts the run.
- **`Apple` rejecting `Pineapple`** → whole-token matching; generic-word brands never match a business name.
- **Dead short names (`ABC`, `TSC`)** → work again via exact-token matching.
- **Bare "Lebanon" accepted anywhere in HTML** → three-tier ranked gate; a country dropdown is no longer evidence.
- **No business identity** → multi-identifier resolution; the duplicate "Fattal Online" rows are merged.
- **No retroactive cleanup** → `requalify.py` re-scores stored rows without deleting anything.
- **Zero tests** → 136 tests, no network access.

Two signals from the design were **removed after measurement disproved them** (§13).

---

## 2. Files Changed

**Created**

| File | Purpose |
|---|---|
| `qualification.py` | Scoring engine: `collect_signals()`, `hard_rejections()`, `score_lead()`, `qualify()`, `Decision` dataclass. The single gate every lead passes through. |
| `exclusions.py` | Safety-net brand list: token-boundary matching, absolute-path loading, `ExclusionsError` on failure. |
| `exclusions.json` | 250 brands migrated from `blacklist.json` into a structured schema (`aliases`, `domains`, `scope`, `match`). |
| `identity.py` | Business identity: identifier normalization, `same_business()`, `find_duplicates()`, `identity_key()`. |
| `requalify.py` | Retroactive re-qualification CLI. Dry-run by default; `--apply` writes; `--report` emits CSV. |
| `pytest.ini` | Restricts collection to `tests/`. |
| `tests/` | 136 tests, 18 real captured fixtures, `capture_fixtures.py`. |

**Modified**

| File | What changed |
|---|---|
| `scraper.py` | Added `fetch_products_json()`, `fetch_instagram_followers()`, `lebanon_signals()`, `passes_lebanon_gate()`, `get_contact_text()`, and qualification-signal patterns. Rewrote Lebanon detection. Removed `load_blacklist()`/`is_blacklisted()`. Fixed `extract_email()` to be subdomain-aware. `analyze_site()` now returns 6 extra signal fields. |
| `main.py` | Rewritten. Seven inline `if/continue` filters replaced by one `qualify()` call plus identity-aware duplicate handling. Loads exclusions up-front and aborts if unavailable. |
| `database.py` | 9 qualification columns via `ALTER TABLE`; `all_leads()`, `find_existing_business()`, `record_alias_domain()`, `set_decision()`; absolute DB path; `IntegrityError` guard in `cleanup_existing_urls()`; CSV export includes decision fields. |

**Deleted:** none. `blacklist.json` and `test_shopify.py` are now unused but were left in place. `northbound.db` was backed up before any change.

---

## 3. Qualification System

### Lebanon gate (must pass)

Three tiers. **A bare "Lebanon" anywhere in the HTML is not a signal at any tier.**

| Tier | Sufficient? |
|---|---|
| **STRONG** — registry/telecom-backed | Any **one**. Cannot be vetoed. |
| **CLAIM** — merchant self-identification | Any **one**, unless a US-Lebanon conflict is present. |
| **MEDIUM** — suggestive | **Two**, unless a US-Lebanon conflict is present. |

### Hard rejections (definitional only)

| # | Rule |
|---|---|
| H1 | ≥**80%** of the `products.json` catalogue carries one vendor matching an **international**-scope exclusion entry (mono-brand outlet) |
| H2 | Exclusion-list token match on business name or domain |
| H3 | Instagram followers ≥ **250,000** |
| H4 | Fails the Lebanon gate |
| H5 | Placeholder store name (`My Store`, `My Shop`, `Shopify Store`, …) |

Hard rejection returns immediately with `score = 0` and does not run scoring.

### Thresholds

| Band | Outcome |
|---|---|
| score **≥ +2** | **QUALIFIED** |
| score **−2 … +1** | **REVIEW** (stored, flagged, excluded from outreach) |
| score **< −2** | **REJECTED** |

Constants: `QUALIFY_AT = 2`, `REJECT_BELOW = -2`, `FOLLOWER_HARD_REJECT = 250_000`, `MONO_BRAND_SHARE = 0.80`.

### Special rules

- **Catalogue size is never scored.** `product_count` is stored for diagnostics only; a parametrized test asserts it cannot change an outcome.
- **Website quality is never scored.**
- Followers `0` and `None` are distinct: `0` is a measurement, `None` means unknown.
- One cheap pre-fetch check remains in `main.py`: a domain matching the exclusion list is skipped before spending an HTTP request. It is the only discovery-side rejection.

---

## 4. Signals

### Negative

| Signal | Measures | Weight | If unavailable |
|---|---|---|---|
| `brand_scale_following` | IG followers 120K–250K | **−8** | Neutral (0) |
| `very_large_following` | IG followers 60K–120K | **−5** | Neutral |
| `large_following` | IG followers 30K–60K | **−2** | Neutral |
| Corporate terms in name/vendor | `international`, `group`, `holding(s)`, `s.a.l.`, `s.a.r.l.`, `franchise`, `corporation` | **−3** | Not applied |
| Legal entity in footer | `s.a.l.`, `s.a.r.l.`, `holdings`, `franchisee`, `group of companies` — **footer/contact block only** | **−3** | Not applied |
| Store locator present | `/pages/stores`, "our branches", "find a store" | **−3** | Not applied (poor recall) |
| Multi-locale operation | ≥10 distinct `hreflang` values | **−3** | Not applied |
| Sentry present | Error-monitoring tooling | **−1** | Not applied |

### Positive

| Signal | Measures | Weight | If unavailable |
|---|---|---|---|
| `very_small_following` | IG followers < 2,000 | **+2** | Neutral |
| `small_following` | IG followers 2K–10K | **+1** | Neutral |
| `moderate_following` | IG followers 10K–30K | **0** | Neutral |
| Sells own brand | Dominant `products.json` vendor is a token-subset of (or superset of) the business name | **+2** | Not applied |
| Free email contact | `gmail.com`, `hotmail.com`, … | **+2** | Not applied |
| No custom domain | Host ends `.myshopify.com` | **+2** | Deterministic |
| WhatsApp channel | WhatsApp contact present | **+1** | Not applied |
| No enterprise tooling | No Sentry detected | **+1** | Applied by default |
| Lebanese city in contact block | City name inside footer/contact markup | **+1** | Not applied |

**Deliberately excluded after measurement:** product count, collection count, vendor count, page weight, link count, country-selector count, Klaviyo/GTM, `Shopify.country`, `tel:` links, JSON-LD `addressCountry`.

**Failure principle:** every external lookup returns `None` on any failure, and `None` is always neutral. **A missing signal can never reject a lead.**

---

## 5. Blacklist (now: exclusion list)

**Role:** safety net only. Scoring is the primary mechanism; the list catches brands the signals miss and should shrink over time.

**Matching** operates on normalized whole tokens, not substrings:

- Name: alias tokens must appear as a **consecutive run** of whole tokens. `["mike","sport"]` matches `["mike","sport","abc"]`; `["apple"]` does **not** match `["pineapple","boutique"]`.
- Domain: split on separators into labels. `lacoste.com.lb` → `["lacoste","com","lb"]` matches; `pineapple.com` → `["pineapple","com"]` does not. Glued aliases are also compared (`mikesport` as a single label).
- Explicit `domains` entries match the host or any parent.
- Fuzzy matching only for single-word aliases ≥5 characters at ratio ≥0.90.

**Relative-path fix:** `EXCLUSIONS_FILE = Path(__file__).resolve().parent / "exclusions.json"`. Loading no longer depends on the working directory; a test verifies this by `chdir`-ing to a temp directory.

**Partial-match false positives:** 28 entries whose name is an ordinary English word (`Apple`, `Target`, `Boss`, `Coach`, `Mango`, `Vans`, `Browns`, `Creed`, …) are marked `"match": "generic_word"`. These **never match a business name**. They still match on domain, and on a `products.json` vendor when the caller passes `allow_generic_word=True` — a catalogue whose vendor is literally `Apple` is an Apple reseller, not a coincidence. This is what catches iSTYLE while leaving `Apple Orchard Farm` alone.

**Short names (`ABC`, `TSC`, `Gap`, `MAC`, `Lee`, `Zep`, `H&M`):** marked `"match": "exact_token"`. Under the old rules anything under 4 characters could only match a whole string, so they never fired. They now match as whole tokens — `ABC Verdun` and `abc.com.lb` are caught — while `abcdef` is not, and fuzzy matching is disabled for them.

**Missing file:** `load_exclusions()` raises `ExclusionsError` on a missing file, invalid JSON, unreadable file, or wrong structure. `main.py` catches it, prints `FATAL:` and **aborts the run**. The old behaviour — returning an empty list so everything passed — is gone.

---

## 6. Lebanon Detection

**STRONG** (registry/telecom-backed; any one passes, cannot be vetoed):
`lb_domain` (host ends `.lb`) · `lebanese_phone` (`+961`/`00961` in HTML) · `lebanese_whatsapp` (WhatsApp digits start `961`) · `lb_email_domain` (email domain ends `.lb`)

**CLAIM** (deliberate self-identification; any one passes unless US-Lebanon conflict):
`lebanon_in_domain` (excluding `mt-lebanon`) · `lb_in_email_local` (`lb`/`lebanon` token in the mailbox, e.g. `curlysquare.lb@gmail.com`) · `lebanon_in_business_name`

**MEDIUM** (two required, unless US-Lebanon conflict):
`lebanese_instagram_handle` · `city_in_contact_block` (Lebanese city **inside footer/contact markup only**) · `lbp_currency` · `lebanon_shipping_context`

**Rejected as a signal:** a bare `Lebanon` anywhere in the HTML. This was the old rule and it accepted any foreign store whose checkout lists Lebanon as a shipping country.

The **CLAIM tier was added during implementation.** The two-tier design from the plan rejected four real SMEs (Qatfa, Curly Square, Adaline, MOROMART) whose only evidence was self-identification.

City detection is scoped to the contact block via `get_contact_text()` (footer/address tags plus elements whose class or id mentions contact/address/location/footer), so a blog post mentioning Beirut is not treated as an address.

---

## 7. Identity Resolution

**Identifiers** (normalized): registrable domain · phone · WhatsApp · email · Instagram handle · business name.

Normalization details:
- Phone: digits only, `00` prefix stripped, local Lebanese numbers get `961` prepended — `+961 3 655 267`, `009613655267`, `+9613655267` all unify.
- Instagram: lowercased, dots removed — `Curly.Square` → `curlysquare`.
- Registrable domain: handles two-part TLDs (`istyle.com.lb` stays whole). **`*.myshopify.com` is treated as a platform**, so the full host is used — otherwise every Shopify store would collapse into one business.
- Business name: country/store noise removed (`Adaline Lebanon` → `adaline`). Returns `None` for placeholder names and anything under 4 identifying characters.

**Matching logic** — confidence hierarchy, not unanimity:

1. **Any one** of domain / email / Instagram matching → same business.
2. Phone and WhatsApp are compared crosswise as one identifier space → same business.
3. Name similarity ≥0.90 **requires corroboration** from a matching city or industry. `general`/`unknown`/`other` cannot corroborate — they mean "no information".

**The real case:** rows 22 and 48 ("Fattal Online", `fattal-online.myshopify.com` and `fattalonline.com`) share name, email, phone and Instagram. They merge on `same email: info@fattalonline.com`. In `main.py`, a matching business causes the alias domain to be recorded via `record_alias_domain()` instead of inserting a second lead.

**Safeguards:** identical names alone never merge (two shops can share a name); 0.89 similarity stays separate; different `.myshopify.com` stores never merge.

---

## 8. Retroactive Re-qualification

`requalify.py` re-fetches each stored lead, re-runs `qualify()`, and records the verdict.

- **Dry run is the default.** `--apply` is required to write. `--report CSV` emits a full before/after table. `--limit N` restricts the pass.
- **Nothing is ever deleted.** There is no `DELETE` statement in the codebase. Failing rows are marked `decision='rejected'` with their reasons in the `reasons` column and full signals in `signals_json`.
- Rows that predate qualification default to `legacy_unreviewed` — not assumed good.
- A site that can no longer be fetched is **left unchanged** rather than rejected on missing evidence.
- Duplicate clusters are detected across the whole table; the lowest id is canonical, others are marked `decision='duplicate'` with a pointer to the canonical row.

**Applied to the live database (46 rows):** 17 qualified · 22 rejected · 6 review · 1 duplicate. All 46 rows retained, all carry written reasons, `PRAGMA integrity_check: ok`. Swarovski (row 6) — which sat in the leads table despite being blacklisted — is now `rejected` with its reason recorded.

---

## 9. Tests

136 tests, **no network access** — all store data is replayed from real captured fixtures in `tests/fixtures/` (18 stores, captured by `tests/capture_fixtures.py`).

| File | Tests | Covers |
|---|---|---|
| `tests/test_qualification.py` | 45 | Must-reject large brands; must-qualify SMEs; hard-rejection mechanisms; edge cases; fail-safe behaviour; score-ordering calibration |
| `tests/test_exclusions.py` | 28 | Substring false positives; generic-word brands; short names; real matches; fail-loud loading; cwd independence; normalization |
| `tests/test_identity.py` | 25 | Fattal duplicate; single-identifier merges; phone/Instagram normalization; wrong-merge protection; placeholder-name regressions |
| `tests/test_lebanon.py` | 19 | Gate tiers; country dropdowns; US-Lebanon towns; contact-block scoping; foreign phone numbers |
| `tests/test_pipeline.py` | 19 | Qualification-bypass invariants; catalogue-size neutrality; extraction bug fixes; schema; legacy-row handling |

Behaviour categories covered: **must-reject** (Lacoste, Swarovski, iSTYLE, Marie France, Mike Sport, non-Lebanese, placeholders); **must-not-reject** (10 SMEs, plus 1000+ product catalogues, many collections, 32K followers, polished websites, multiple branches); **exclusion false positives** (`Pineapple Boutique`, `Bossa Nova Beirut`, `Coachella Style LB`, `Caravans Lebanon`, `Apple Orchard Farm`, `Target Fitness Lebanon`); **failure modes** (missing Instagram, invalid handle, missing `products.json`, missing exclusion file); **duplicates**; **pipeline invariants**.

`test_score_ordering_bad_below_good` asserts every large brand scores below every SME, so weights can be retuned without rewriting the suite.

---

## 10. Real-World Validation

Actual output of the implemented system against captured fixture data (identical to the live-network run).

| Business | Score | Status | Main Reasons |
| -------- | ----: | ------ | ------------ |
| Lacoste | 0 | **REJECTED** | HARD: mono-brand outlet, 100% vendor `LACOSTE`; exclusion list; 9,000,000 followers |
| Swarovski | 0 | **REJECTED** | HARD: mono-brand outlet, 100% vendor `SWAROVSKI`; exclusion list |
| iSTYLE | 0 | **REJECTED** | HARD: mono-brand outlet, 100% of catalogue is `Apple` |
| Marie France | **−7** | **REJECTED** | −8 brand-scale following (179,000); −1 Sentry; +2 sells own brand |
| Mike Sport | **−4** | **REJECTED** | −5 very large following (117,000); −3 corporate term `international`; +2 own brand; +1 WhatsApp; +1 no tooling |
| Fattal Online | **−1** | **REVIEW** | −2 large following (49,000); +1 no enterprise tooling |
| LivGood | **−1** | **REVIEW** | −2 large following (45,000); +1 no enterprise tooling |
| Curly Square | **+5** | **QUALIFIED** | −2 large following (32,000); +2 own brand; +2 free email; +2 no custom domain; +1 no tooling |
| Istahly | **+3** | **QUALIFIED** | +0 moderate following (24,000); +2 sells own brand; +1 no tooling |
| OutGeeked | **+3** | **QUALIFIED** | +1 small following (5,944); +1 WhatsApp; +1 no tooling |
| Qatfa Lebanon | **+5** | **QUALIFIED** | +2 very small following (575); +2 sells own brand; +1 no tooling |
| Lightwave | **+5** | **QUALIFIED** | +2 very small following (235); +2 sells own brand; +1 no tooling |

Also rejected: **Maureen Abood** (Michigan, USA) and **NBPower** (Chinese, `+86` WhatsApp) via the Lebanon gate; **4× "My Store"** via H5.

Lacoste and Swarovski are additionally caught **pre-fetch** in `main.py` by the excluded-domain check, before any HTTP request. iSTYLE is caught **only** by the `products.json` vendor rule — name and domain matching miss it entirely.

**Summary: 6/6 large brands rejected · 11/11 SMEs not rejected (10 qualified, 1 review).**

---

## 11. Test Results

```
Command:  python -m pytest -q          (from the project root)
Collected: 136 tests
Passed:    136
Failed:    0
Skipped:   0
Errors:    0
Warnings:  0
Runtime:   ~7 seconds
```

`pytest` was not previously installed and was added (`pytest 9.1.1`). The project root contains `test_shopify.py`, a legacy scratch script with no assertions that makes a live network call; `pytest.ini` sets `testpaths = tests` so it is not collected.

---

## 12. Pipeline Integration

```
search_websites()                       scraper.py  (unchanged — discovery stays broad)
  └ for each candidate in main.py:
      1. lead_exists(domain)                    → skip known domain
      2. is_excluded(None, domain, exclusions)  → skip excluded brand domain (pre-fetch)
      3. analyze_site(url)                      → ONE request, all page signals
      4. if not is_shopify: skip
      5. qualify(url, analysis, exclusions)     ← THE GATE (qualification.py)
           ├ collect_signals()  → +1 products.json, +1 Instagram request
           ├ hard_rejections()
           └ score_lead() → banding
      6. if REJECTED: continue                  → never inserted
      7. find_existing_business()               → alias recorded instead of duplicate row
      8. add_lead(..., decision, score, reasons, signals)
```

**Every discovery path passes through qualification.** There is one discovery source (`search_websites()`) and one loop, and `add_lead()` is reachable only after `qualify()` returns a non-rejected decision.

Verified two ways:
- **Static:** `test_main_calls_qualify_before_adding_a_lead()` parses `main.py`'s AST and asserts `qualify` is called before `add_lead`; further tests assert the old blacklist API is absent from `main.py` and from `scraper`.
- **Runtime:** an end-to-end run through the real `main()` against a temp database with 8 candidates produced **0 rejected rows stored** and **0 rows without a decision**, correctly qualified Qatfa and Lightwave, rejected Lacoste/Marie France/iSTYLE/Maureen Abood, sent Fattal to REVIEW, and merged the second Fattal domain as an alias.

Network cost is now ~3 requests per candidate (page, `products.json`, Instagram), up from 1.

---

## 13. Known Limitations

**Weights are fitted on the sample that validates them.** 17/18 correct demonstrates consistency, not generalisation. The thresholds should be treated as provisional until ~30 fresh candidates are hand-labelled.

**Two designed signals were removed after measurement disproved them.** This is worth knowing because the plan document still describes them:
- *Catalogue size:* Mike Sport has **5** products; OutGeeked, KlapTap and Petriotics (all legitimate SMEs) have **1000+**. Inverted, therefore unused.
- *Multi-brand international vendor penalty:* Fattal (large distributor) has **0%** of its vendors on the exclusion list; OutGeeked (SME) has **14%**. It would have penalised the good lead and spared the bad one.

**Could still false-positive a large company:**
- **Fattal Online scores −1 (REVIEW, not REJECTED).** The only signal separating it from LivGood is 49K vs 45K followers, and any threshold that rejects Fattal also rejects a legitimate lead. REVIEW is the honest outcome; the test asserts only that it is never QUALIFIED.
- **Babyjem** still qualifies despite a `@paravidagroup.com` distributor email — a group-domain contact is a signal the system captures but does not score.
- A large brand with a modest Instagram presence, its own vendor name, no store locator and a single locale would score positively. `lebanonshop-2.myshopify.com` and `Juthour Co` currently qualify on thin evidence.
- Store-locator detection has **poor recall** — absent on Mike Sport *and* Swarovski. Useful only when present.

**Could still false-negative a legitimate SME:**
- A fast-growing SME above ~60K followers takes −5 and will likely land in REVIEW.
- An SME resembling a mono-brand outlet (a single-brand authorised reseller of a listed brand) is hard-rejected by H1 with no appeal.
- The Lebanon gate requires explicit evidence; a Lebanese business with no `+961` number, no `.lb` anything, and no self-identification will be rejected.
- Generic-word exclusion entries still match on **domain** — a business at `mango.com.lb` would be excluded.

**Could fail due to external data:**
- **Instagram is a scraped surface**, unauthenticated. If it starts requiring auth, rate-limits, or changes markup, the strongest signal degrades to neutral and more leads land in REVIEW. Safe, but weaker. There is no caching, so `requalify.py` re-fetches everything.
- `products.json` was available on 17/17 stores tested but is not guaranteed; password-protected or non-Shopify stores return nothing.
- Instagram handle extraction still takes the **first regex match**, which can capture a third-party handle (row 15 stores `a.d.a.ybeauty`), producing a follower count for the wrong account.
- Single fetch, 10s timeout, no retry — a slow site is classified "not Shopify" and lost.

**Other:** `blacklist.json` and `test_shopify.py` are dead files left in place. `requalify.py` at ~3 requests/row is fine for 46 rows and slow at 500.

---

## 14. Recommended Next Steps

1. **Calibrate on fresh data.** Run ~30 newly discovered candidates, hand-label them, and compare against the model. This is the single highest-value next action — every threshold currently rests on an 18-store sample.
2. **Cache signals for offline re-qualification.** `signals_json` is already stored; making `requalify.py` re-score from it (with an explicit `--refetch` flag) would make re-tuning instant and remove repeated Instagram traffic.
3. **Measure REVIEW volume, then decide on the LLM tie-breaker.** The REVIEW state and the `Decision` structure are already in place. If REVIEW stays under ~15% of candidates, manual review is cheaper than adding an LLM.
4. **Validate the Instagram handle before trusting its follower count** — require the handle to appear in the footer/social block or resemble the business name. This closes the wrong-account risk noted above.
5. **Improve discovery yield.** Two of the five keyword queries return mostly non-Lebanese or US-"Lebanon" noise, wasting roughly 40% of each run's budget. Replacing them with `.com.lb`-oriented and Lebanese-city long-tail queries raises SME yield without touching correctness.

**On expanding discovery beyond Shopify:** worth doing eventually, but **not next.** The current pipeline hard-requires a working Shopify store, which means it finds "Lebanese SMEs already selling online well" — a coherent target, but not the same as "businesses that would benefit from better online presence." Businesses with no website, a broken site, or Instagram-only presence are arguably the best NorthBound prospects and **cannot be discovered at all today**. That is a discovery problem, not a qualification problem, and it needs a different source (business directories, Instagram) plus a rethink of the scoring signs — several current positive signals assume a Shopify storefront exists. Do it as a separate project once the qualification thresholds are calibrated and stable.
