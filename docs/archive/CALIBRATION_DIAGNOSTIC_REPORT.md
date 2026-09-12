# NorthBound Lead Finder — Calibration / Diagnostic Report

**Date:** 2026-09-01
**Scope:** diagnostic only. No production code changed, no database rows written or altered,
no qualification weights, thresholds, Lebanon rules, exclusion rules, discovery queries or
identity logic modified.

**What was actually done**
- Read `main.py`, `scraper.py`, `qualification.py`, `identity.py`, `database.py`, `exclusions.json`.
- Read `northbound.db` **read-only** (`file:northbound.db?mode=ro`), all 48 rows including full `signals_json`.
- Re-ran the **discovery step only** (`scraper.KEYWORDS` via DDGS) to capture what the queries return, per query.
- Re-ran `analyze_site()` + `qualify()` over all 50 returned URLs **in memory**, writing nothing.
- Fingerprinted the platform of every non-Shopify candidate.
- Ran offline sensitivity analysis over stored signals (which verdicts hinge on which single signal).
- Ran the test suite: **179 passed**.

Working artefacts (scratchpad, not in the repo):
`search_probe.json`, `analysis_probe.json`, `platform_probe.json`.

---

## 0. Executive summary

The Lebanon gate is **not** the bottleneck, and neither are the weights or thresholds.

Three findings, in order of importance:

1. **The pipeline destroys its own evidence.** `main.py` never persists a rejected or
   non-Shopify candidate. Of the 47 candidates in the last run, **only 2 rows exist anywhere**.
   The other 45 — including all 14 rejections and all 15 non-Shopify sites — are gone. I had to
   re-run discovery and 51 live fetches to reconstruct a single run. **This makes the planned
   calibration set structurally impossible to build**, and makes every future change to the
   gate or the weights unmeasurable for false-negative impact.

2. **Discovery yields ~2 genuinely new Lebanese SME candidates per run**, and the query set is
   static, so it returns substantially the same 50 URLs every time. 40% of results are already
   known; the `site:myshopify.com Lebanon` query contributes 8 new candidates per run, **all
   eight foreign**, none useful.

3. **The Shopify-only filter is discarding the best leads.** 16 of 50 results were non-Shopify.
   Ten of those are WooCommerce/WordPress/OpenCart stores **with a live +961 phone number** —
   i.e. exactly the Lebanese SME profile the project is looking for. The Shopify path produced
   2 new Lebanese candidates in the same run; the discarded non-Shopify path produced ~9.

The Laptops King case is a **pipeline-ordering defect, not an identity-logic defect** (identity
logic gets it right; it is simply never consulted). Carpisa's `REVIEW` is the correct band, but
it is right for the wrong reason, and it exposes a genuine signal defect in "sells own brand".

---

## 1. Corrected picture of the last run

The console summary is accurate but hides where the rows went:

| Reported | Where it ended up |
|---|---|
| 47 websites found | not persisted |
| 0 qualified | — |
| 2 review | rows **63** (Carpisa Lebanon) and **64** (Laptops King, myshopify) — the only two rows the run created |
| 14 rejected | **discarded — no record exists** (`main.py:105-107` `continue`s before any write) |
| 16 already in database | matched on `domain` only |
| 15 not Shopify | **discarded — no record exists** (`main.py:87-90`) |
| 0 errors | — |

The 23 `rejected` rows visible in `northbound.db` are **not** from this run. They were written
by the earlier `requalify.py` pass (timestamps 11:45–11:49) over pre-existing rows.
`refresh.csv` is that pass's output, 46 rows. Live-run rejections are never stored.

**Database state:** 48 rows — 17 `qualified`, 7 `review`, 23 `rejected`, 1 `duplicate`.
Of these, **28 rows carry a real score**; the other 20 are hard rejects with `score = 0`,
which carry no calibration information about the weights at all.

A bookkeeping detail worth noting: the `instagram_followers` **column** is populated on only
**2** of 48 rows, while `signals_json.instagram_followers` is populated on **37**. `set_decision()`
does not backfill the column, so any SQL query or export that reads the column under-reports the
strongest signal in the system by 35 rows. Scoring is unaffected (it reads `signals_json`).

---

## 2. Observation 1 — the Lebanon gate

**Verdict: the gate's policy is sound. Do not weaken it. But it is being starved of evidence
upstream, and that is producing real false negatives.**

### 2.1 The gate is doing its job

Live-confirmed from the probe. These are foreign Shopify stores that mention Lebanon and were
correctly rejected:

| Domain | What it actually is | Why rejected |
|---|---|---|
| `mt-lebanon-book-cellar.myshopify.com` | Mt Lebanon, **Pennsylvania** bookshop | had 2 CLAIM signals (`lb_in_email_local`, `lebanon_in_business_name`) — **`us_conflict` veto correctly overrode them** |
| `pontello-motorsports.myshopify.com` | Lebanon Valley Kart Track, **NY** | no signal + `us_conflict` |
| `terraboost-media.myshopify.com` | CVS ad panels, Lebanon **PA** | only `lebanon_shipping_context` + `us_conflict` |
| `frisco-sports-center.myshopify.com` | Frisco, **Texas** ("Lebanon Trail" high school tee) | no signal + `us_conflict` |
| `simplec10.myshopify.com` | US truck show, 223K followers | no signal + `us_conflict` |
| `open-books-a-poem-emporium.myshopify.com` | US bookshop selling a book *about* Lebanon | no signal |
| `fai-outfitter-and-supply.myshopify.com` | US mission outfitter | no signal |
| `ayntec.com` | AYN Technologies, **Hong Kong** (`info@ayn.hk`) | no signal |
| `stores.pandora.net` | Pandora store in Lebanon, **Kentucky** | excluded brand domain |

The `us_conflict` veto on `mt-lebanon-book-cellar` is the strongest evidence the design is
correct: two self-identification CLAIM signals were present and were still overridden. That is
exactly the behaviour that keeps a US business out of a Lebanese lead list.

### 2.2 But the gate is producing false negatives, from *evidence supply*, not from policy

Three rows are hard-rejected as "failed Lebanon gate" that are, on inspection, genuine Lebanese
businesses:

**`welcomehomelb.com` — "Welcome Home Lebanon"** (cookware retailer)
- Instagram `welcome_homelb`, 7,094 followers; 213 products; own-brand vendor share 0.97.
- Gate saw: `strong=[] claim=[] medium=[]` — **zero evidence.**
- Cause chain:
  - `extract_business_name()` returned **`"Cookware"`**, not "Welcome Home Lebanon", so the
    `lebanon_in_business_name` CLAIM never fired.
  - `lebanon_in_domain` (`scraper.py:717`) matches only `"lebanon"`/`"lebanese"` in the host.
    The domain is `welcomehome**lb**.com` — the `lb` suffix convention is not recognised.
  - The Instagram medium regex (`scraper.py:737`) is
    `(^|[._])(lb|leb|lebanon|lebanese)([._]|$)`. In `welcome_home**lb**` the `lb` is preceded by
    `e`, not `^` or `[._]`, so it does not match.
  - No `+961` on the homepage.

**`karoutonlinelb.com` — "Karout Online Shopping in Lebanon"**
- Page title says "in Lebanon" twice. Gate saw **zero signals**; `products.json` also came back
  empty (`vendor_count=None`), and name extracted as `"Karout"`.
- This is an *extraction/fetch* failure presenting as a country failure.

**`houseofappliances.co` — Lebanese appliance retailer**
- Instagram `house.of.applianceslb` (4,381 followers), 19 vendors incl. Le Creuset.
- Gate saw one MEDIUM (`lebanon_shipping_context`); needs two. The Instagram handle again ends
  `...applianceslb` and misses the delimiter-anchored regex.

**Pattern:** Lebanese SMEs overwhelmingly signal Lebanon with a bare `lb` suffix
(`adalinelb`, `babyjemlb`, `lightwavelb`, `wafstorelb`, `welcomehomelb`, `karoutonlinelb`,
`tecno-lb`, `sihoo-leb`, `house.of.applianceslb`). The gate recognises this convention **only**
when it is delimiter-separated. It is not a threshold problem and it is not a policy problem —
lowering the gate would let the Pennsylvania bookshop back in. It is a recogniser-coverage problem.

**No change made. Flagged for the calibration stage.**

---

## 3. Observation 2 — discovery quality

### 3.1 Per-query yield (live probe, 50 results)

| Query | Total | Already known | Ignored host | Excluded brand | Not Shopify | Reached qualification | **New Shopify candidates** | Of those, useful |
|---|---|---|---|---|---|---|---|---|
| `"Lebanon" "shop now"` | 10 | 2 | 1 | 2 | 5 | 2 | **0** | 0 |
| `"Lebanon" "online store"` | 10 | 4 | 0 | 2 | 5 | 3 | **1** | 1 (`la2taa.com`) |
| `"Lebanon" "powered by Shopify"` | 10 | 8 | 0 | 0 | 0 | 10 | **2** | 1 (`nur-lebanon`) |
| `"Lebanon" "buy online"` | 10 | 4 | 0 | 0 | 6 | 4 | **0** | 0 |
| `site:myshopify.com Lebanon` | 10 | 2 | 0 | 0 | 0 | 10 | **8** | **0** |
| **Total** | **50** | **20 (40%)** | 1 | 4 | 16 (32%) | 29 | **11** | **2** |

Outcomes of the 29 that reached qualification: 11 qualified, 4 review, 14 rejected.

### 3.2 What this says

- **Hard ceiling of 50 candidates per run.** `search_websites()` runs 5 static queries at
  `max_results=10` (`scraper.py:528-557`). There is no pagination, no query rotation, no seed
  variation. DDG returns a near-identical set each time, so **novelty decays to zero** as the
  database grows. 40% already-known today; that fraction only rises.
- **The overall useful-yield rate is 2/50 = 4%.**
- **`site:myshopify.com Lebanon` is a net-negative query.** It is the only query with 100%
  Shopify precision, and it contributes **8 new candidates per run, all 8 foreign**
  (Pennsylvania, New York, Texas, Hong Kong, dropship). It is precise on *platform* and
  near-zero on *country*, because `site:myshopify.com` forces the match onto the word "Lebanon"
  appearing anywhere in a product title, a book title, or a US place name. It also has a
  second-order effect — see §4.2, the `+2 no custom domain` bonus.
- **`"Lebanon" "powered by Shopify"` is the only high-precision query and it is saturated.**
  10/10 Shopify, but 8/10 already in the database. It built most of the current DB and is now
  nearly exhausted.

### 3.3 Bias audit (item 8) — answered against evidence

| Suspected bias | Verdict | Evidence |
|---|---|---|
| Toward **large brands** | **Yes, moderate** | Results included Selfridges, Pandora, Swarovski, Avon, Marie France, iSTYLE, Ubuy, Ishtari, Sea Sweet, Sleep Comfort. The exclusion list caught 4 pre-fetch; the rest consumed budget. Cause: "shop now" / "buy online" / "online store" are commercial-SEO phrases that large retailers optimise for and small ones do not. |
| Toward **foreign businesses mentioning Lebanon** | **Yes, severe — the dominant failure mode** | 13 of 50 (26%) were foreign. All 8 new candidates from `site:myshopify.com Lebanon` were foreign. |
| Toward **marketplaces / directories** | **Yes, mild** | `ubuy.com.lb`, `ishtari.com`, `la2taa.com`, `isn-store.com`. Only ~4/50, and all are non-Shopify so they self-filter. Not currently a priority. |
| Toward **non-Shopify stores** | **Yes, 32%** — but see §3.4, this is a *strategy* problem, not a query problem | 16/50. |
| Toward **already-known businesses** | **Yes, severe and worsening** | 20/50 (40%). Static queries + growing DB ⇒ monotonic decay. |
| Toward **random Shopify stores whose content happens to mention Lebanon** | **Yes** | `open-books-a-poem-emporium` (sells a book about Lebanon), `frisco-sports-center` ("Lebanon Trail Wall Tee"), `fai-outfitter-and-supply` ("LEBANON" product). |

### 3.4 The single largest discarded population: non-Shopify Lebanese SMEs

Live platform fingerprint of all 16 non-Shopify results:

| Domain | Platform detected | Cart | `+961` on page | Read |
|---|---|---|---|---|
| `ovape-lebanon.com` | WooCommerce/WordPress | yes | **yes** | Lebanese SME |
| `compuonelb.com` | WooCommerce/WordPress | yes | **yes** | Lebanese SME |
| `vastlebanon.com` | WooCommerce/WordPress | yes | **yes** | Lebanese SME |
| `flowerzoneboutique.com` | WooCommerce/WordPress | yes | **yes** | Lebanese SME |
| `hajjelectronics.com` | WooCommerce/WordPress | yes | **yes** | Lebanese SME |
| `tecno-lb.com` | WooCommerce/WordPress | yes | **yes** | Lebanese, mid-size |
| `takkoushflowers.com` | OpenCart | yes | **yes** | Lebanese SME |
| `hmzshop.com` | Magento/Next | yes | **yes** | Lebanese SME |
| `sleepcomfort.com` | WooCommerce/WordPress | yes | **yes** | Lebanese, chain (store locator) |
| `numispoint.com` | WooCommerce/WordPress | yes | yes* | Indian coins — foreign |
| `seasweet.com` | Magento | yes | no | Lebanese, large chain |
| `isn-store.com` | Magento | yes | no | Lebanese SME |
| `ishtari.com` | Magento/Next | yes | no | Lebanese, large marketplace |
| `ubuy.com.lb` | Magento | no | no | international marketplace |
| `bestofwines.com` | Magento | no | no | Dutch — foreign |
| `hexaflexagon-…squarespace.com` | Squarespace | no | no | agency demo page |

\* `numispoint` almost certainly from a country dropdown.

**Nine of the sixteen are Lebanese SME e-commerce sites with a live Lebanese phone number.**
They are discarded at `main.py:87-90` before qualification ever sees them, and — because
nothing is persisted — they are discarded **without leaving a trace**.

This is not a recommendation to drop the Shopify requirement; that is a strategy decision that
depends on what NorthBound actually sells, and the V1 report deliberately deferred it. It *is*
the observation that **the Shopify filter, not the Lebanon gate, is the largest single consumer
of good candidates**, and that today you have no data with which to make that decision, because
the filter throws the evidence away.

---

## 4. Observation 3 — the Laptops King case

**Answer: same business, confirmed. Identity logic is correct. The pipeline never calls it.**

### 4.1 They are the same business

Both URLs were re-fetched live. The extracted records are **byte-identical on every identifier**:

| Field | `laptopsking.com` | `laptopskinglb-961.myshopify.com` |
|---|---|---|
| business_name | `Laptops King` | `Laptops King` |
| instagram | `laptopsking` | `laptopsking` |
| followers | 76,000 | 76,000 |
| phone | `+961 71 330 103` | `+961 71 330 103` |
| whatsapp | `+96171330103` | `+96171330103` |
| city | `Beirut` | `Beirut` |
| industry | `electronics` | `electronics` |
| vendors / dominant | 17 / `Lenovo` 34.4% | 17 / `Lenovo` 34.4% |

Direct call to the identity module:

```
identity.same_business(a, b) -> (True, 'same instagram: laptopsking')
```

Phone and WhatsApp also match after normalisation (`96171330103` both sides). **Three
independent strong identifiers agree.** `identity.py` would merge these without hesitation.

### 4.2 Why they were not collapsed — two independent causes

**Cause A — ordering. Identity resolution runs *after* qualification, and rejected candidates
exit before it.**

`main.py` order of operations:

```
main.py:63    lead_exists(domain)          <- exact-string domain match only
main.py:72    is_excluded(...)
main.py:81    analyze_site(...)
main.py:87    if not is_shopify: continue
main.py:95    decision = qualify(...)
main.py:105   if decision.outcome == REJECTED: rejected += 1; continue   <-- EXITS HERE
main.py:121   existing, reason = find_existing_business(record)          <-- never reached
```

`laptopsking.com` scored `-3` → `REJECTED` → `continue` at line 107. `find_existing_business()`
at line 121 was never called for it. The only duplicate check it ever received was
`lead_exists(domain)` — an **exact string comparison on the domain**, which by construction can
never detect an alias.

Note the ordering also means this could not have been caught in the other direction: even if
`laptopskinglb-961.myshopify.com` had been processed first, `laptopsking.com` would still have
been rejected and dropped, because a rejected candidate is never stored and therefore never
becomes something a later record can match against.

**Cause B — the two URLs score differently, and the entire difference is a technical artefact.**

```
laptopsking.com                  -5 followers  +1 whatsapp  +1 no-enterprise-tooling            = -3  REJECTED
laptopskinglb-961.myshopify.com  -5 followers  +1 whatsapp  +1 no-enterprise-tooling  +2 PLATFORM = -1  REVIEW
```

The sole delta is **`+2 no custom domain (.myshopify.com)`** (`qualification.py:281-282`).

The signal's intent is sound — "a shop that never bought a domain is probably small". But
**every Shopify store has a `*.myshopify.com` URL**; it is the platform's permanent fallback
address, not evidence of anything. When `site:myshopify.com Lebanon` surfaces a store by its
fallback URL rather than its real domain, the same business gets **+2 for free**. Offline
sensitivity analysis over stored signals:

```
verdicts that change if the +2 platform bonus is removed:
  056713-cc.myshopify.com          review (-2) -> rejected (-4)
  laptopskinglb-961.myshopify.com  review (-1) -> rejected (-3)
  (6 platform-domain rows scored, 2 verdict changes)
```

So the bonus is currently the sole reason **both** of the run's `REVIEW` outcomes on
`.myshopify.com` hosts are not rejections. This makes the verdict depend on *which URL discovery
happened to surface*, which is not a property of the business.

**This is a real defect and it is not in `identity.py`.** No change made.

### 4.3 A related, smaller observation

`identity_key` is stored domain-first (`identity.py:159`), so the two twins would carry
`domain:laptopsking.com` and `domain:laptopskinglb-961.myshopify.com` — two different keys for
one business. This is not itself a bug (`find_existing_business()` does pairwise matching, not
key lookup), but the stored key is not a stable business identity and should not be treated as one.

---

## 5. Observation 4 — Carpisa Lebanon

**Answer: `REVIEW` is the correct human-labelled outcome — but the model reached it by
cancelling a wrong `+2` against a correct `-3`, and the `+2` is a genuine signal defect.**

Stored signals (row 63):

```
name            Carpisa Lebanon        followers        21,000
domain          carpisalebanon.com     store locator    TRUE
vendors         1  ("Carpisa Lebanon", 100% of catalogue)
products        250                    sentry           FALSE
email/phone/whatsapp/city   all NULL
lebanon         claim=[lebanon_in_domain, lebanon_in_business_name]

score:  +0 moderate_following  -3 store locator  +2 sells own brand  +1 no enterprise tooling  =  0  -> REVIEW
```

**My human label: *large / brand* — specifically a country franchise of Carpisa, the Italian
leather-goods brand.** Not a NorthBound SME.

**Is `REVIEW` the right *system* outcome for this type?** Yes, and it should stay `REVIEW`, for a
reason that matters: the evidence on the page genuinely does not settle it. A single-vendor
catalogue named after the shop, a store locator, 21K followers and no published contact details
describe a foreign-brand franchise *and* they equally describe a successful independent
Lebanese label with three shops. Forcing a verdict here is exactly the kind of change that
converts a false-positive fix into a false-negative problem. **A country franchise of an
international brand is the archetypal REVIEW case.**

**But the `+2 sells own brand` is firing incorrectly, and it is a systemic problem.**

`_own_brand_vendor()` (`qualification.py:138-151`) checks token-subset between business name and
dominant vendor. A franchise's Shopify catalogue is tagged with the franchise's own trading name
("Carpisa Lebanon"), which is **structurally indistinguishable** from an indie brand tagging its
own products. Audit of every row where the signal fired:

```
+2 sells-own-brand fired on 16 of 48 rows:
  genuine indie SME : klaptap, qatfalebanon, curlysquare, adalinelb, lightwavelb,
                      lebanonstore, moromart, istahly, fromlebanon, mjboardgames, lebanonshop-2
  foreign brand's LB storefront : babyjemlb (Turkish), wavytalkshoplebanon (Chinese)
  brand / franchise / chain     : mariefrancelingerie, carpisalebanon, exotica
```

The signal cannot separate "independent brand" from "single-brand franchise or exclusive
distributor". Sensitivity analysis confirms how much rests on it:

```
verdicts that change if +2 own-brand is removed:
  fromlebanon.co       qualified(+3) -> review(+1)
  klaptap.com          qualified(+2) -> review(+0)
  istahly.com          qualified(+3) -> review(+1)
  babyjemlb.com        qualified(+3) -> review(+1)
  056713-cc.myshopify  review(-2)    -> rejected(-4)
```

For Carpisa specifically, removing the bad `+2` gives `-2`, which is still `REVIEW`
(`REJECT_BELOW = -2` rejects only *below* -2). So the label is stable — but for 4 other rows it
is not. **No change made.**

---

## 6. Observation 5 — hand labels for every candidate in this run

Labels are my own judgement from the fetched evidence (platform, vendor list, contact details,
Instagram, page content). **They were assigned without reference to the model's verdict**, then
compared in §7.

### A — Definitely good NorthBound SME lead

*Reached qualification (Shopify):*
| Candidate | Evidence |
|---|---|
| `la2taa.com` | Lebanese online store, `+9613363111`, own brand, 23K followers. Mid-small; good lead. **New.** |

*Blocked by the Shopify filter — never assessed (all new):*
`ovape-lebanon.com`, `compuonelb.com`, `takkoushflowers.com`, `vastlebanon.com`,
`flowerzoneboutique.com`, `hajjelectronics.com`, `hmzshop.com`, `isn-store.com` — 8 Lebanese SME
storefronts on WooCommerce/OpenCart/Magento, 7 of 8 with a live `+961` number.

*Already in DB, correctly held:*
`adalinelb`, `qatfalebanon`, `curlysquare`, `klaptap`, `outgeeked`, `petrioticsstore`,
`lightwavelb`, `alorabrands`, `istahly`, `mjboardgames`, `moromart`, `lebanonstore`,
`sawakart`, `fromlebanon`, `lebanonshop-2`.

*Already in DB, **wrongly hard-rejected** (see §2.2):*
`welcomehomelb.com`, `houseofappliances.co`, and probably `karoutonlinelb.com` (Karout is a
Lebanese retail chain — arguably too large, but rejected on *absent evidence*, not on size).

### B — Definitely large / enterprise / brand
`mariefrancelingerie.com`, `swarovski.com.lb`, `avonlebanonstore.com`, `istyle.com.lb`,
**`carpisalebanon.com`** (Italian brand franchise), `seasweet.com` (large Lebanese pastry chain),
`ishtari.com` (large marketplace), `ubuy.com.lb` (international marketplace),
`tecno-lb.com` (mid-large), `sleepcomfort.com` (chain), `selfridges.com`, `stores.pandora.net`,
plus already-stored `superdokan`, `mazenonline`, `fattal-online`.

### C — Foreign / non-Lebanese
`ayntec.com` (Hong Kong), `simplec10.myshopify.com` (US), `frisco-sports-center.myshopify.com`
(Texas), `mt-lebanon-book-cellar.myshopify.com` (Pennsylvania),
`pontello-motorsports.myshopify.com` (New York), `terraboost-media.myshopify.com` (US),
`open-books-a-poem-emporium.myshopify.com` (US), `fai-outfitter-and-supply.myshopify.com` (US),
`myvoltifystore-store.myshopify.com` (dropship, `Dropshipman` vendor), `numispoint.com` (India),
`bestofwines.com` (Netherlands), `sihoo-leb.com` (Chinese brand's LB outlet),
`hexaflexagon-…squarespace.com` (agency demo).

### D — Duplicate / alias
`laptopsking.com` ≡ `laptopskinglb-961.myshopify.com` — **one business, two rows' worth of
processing, two different verdicts.**
(Historical precedent already in DB: `fattalonline.com` ≡ `fattal-online.myshopify.com`, which
*was* caught, because neither was rejected.)

### E — Ambiguous
| Candidate | Why ambiguous |
|---|---|
| `nur-lebanon.myshopify.com` | Lebanese by domain+name, but **no phone, no email, no Instagram, no products.json**. Genuinely undecidable on current evidence. **New.** |
| `babyjemlb.com` | Turkish baby-products brand's Lebanon store. Local operator or brand subsidiary? Currently `QUALIFIED +3`. |
| `wavytalkshoplebanon.com` | Chinese hair-tool brand's Lebanon store. Same question. Currently `QUALIFIED +5`. |
| `laptopsking.com` | Genuinely Lebanese electronics retailer, but 76K followers and 17 vendors — SME or regional chain? |
| `livgood.com`, `wafstorelb.com`, `myholdal.com`, `056713-cc` (Exotica) | already in DB at `REVIEW`; correctly parked |

---

## 7. Observation 6 — human labels vs. system outcomes

### False positives (system says QUALIFIED, human says not an SME lead) — 2, both soft
- `babyjemlb.com` — `QUALIFIED +3`. Foreign brand's Lebanon storefront. Should be `REVIEW`.
- `wavytalkshoplebanon.com` — `QUALIFIED +5`. Same. Should be `REVIEW`.

Both are driven by the `+2 sells own brand` defect from §5. **Neither is a large brand** — this
is a mild over-qualification, not the Lacoste/Mike Sport class of failure the V1 rewrite was
built to stop. **That class of failure is now absent from the results.**

### False negatives (system REJECTED, human says lead) — 3, all from evidence starvation
- `welcomehomelb.com` — hard-rejected on Lebanon gate; name extraction returned "Cookware".
- `houseofappliances.co` — one MEDIUM short; `lb`-suffix Instagram handle unrecognised.
- `karoutonlinelb.com` — zero signals extracted from a page whose title says "in Lebanon" twice;
  `products.json` also empty. Probable partial-fetch failure.

**All three are extraction failures, not scoring failures.** Not one of them would be fixed by
changing a weight or a threshold.

### Appropriate REVIEW cases — the band is working
`carpisalebanon.com` (franchise), `laptopskinglb-961` (scale genuinely unclear), `myholdal`,
`wafstorelb`, `livgood`, Exotica. Every one is a business a human would also want to look at.
**The REVIEW band is the healthiest part of the system.**

### Cases where the system simply lacks evidence — the largest group
- `nur-lebanon.myshopify.com`: qualified `+3` on **nothing but** "no custom domain" + "no Sentry".
  Both are absence-of-evidence signals. This row's `QUALIFIED` is not a judgement, it is a default.
- **11 of 48 DB rows have no follower count at all** (23%), so the strongest measured signal is
  neutral for nearly a quarter of the population.
- Missing-evidence rates across the DB: email 54%, phone 60%, WhatsApp 81%, city 75%.
- **Fragility:** of the 28 rows that carry a real score, **14 sit within 1 point of a band edge**
  (5 exactly on an edge, 9 one point away). Half the scored population is decided by a single
  signal firing or not firing.

The dominant error mode of this system is **not** wrong weights. It is **thin and unreliable
evidence**, and verdicts that consequently hinge on one signal.

---

## 8. Observation 7 — is there enough for the ~30-candidate calibration set?

**No. And the gap is not "a few more runs".**

### What this run actually produced
- 47 candidates seen → **2 rows written**.
- Genuinely new candidates that reached qualification: **11**, of which **8 were foreign junk**
  from `site:myshopify.com Lebanon`, **1 was a duplicate** (`laptopsking.com`), leaving
  **2 genuinely new Lebanese candidates** (`la2taa.com`, `nur-lebanon.myshopify.com`).
- **45 of 47 candidates left no record.** The 14 rejections and 15 non-Shopify sites in your
  summary cannot be hand-labelled, because they no longer exist anywhere. I could only
  reconstruct them by re-running discovery and re-fetching 51 sites live.

### Why more runs will not close the gap
The query set is static and un-paginated, capped at 50 results. It returns substantially the
same URLs each run — already 40% known. At ~2 new Lebanese candidates per run you would need
**~15 runs** to reach 30, from a source that is already saturated. It will not get there.

### What you *do* have
28 scored rows in `northbound.db` (the other 20 are hard rejects at score 0, which teach the
weights nothing). Adding this run's 2 gives **30 scored rows** — nominally the target number.

**But do not calibrate on them.** Those 30 rows are the survivors of a discovery process that
only surfaces Shopify stores that self-identify as Lebanese in a way the current recogniser
happens to catch. Every business the extraction layer failed on (`welcomehomelb`, `karoutonlinelb`,
`houseofappliances`) and every non-Shopify Lebanese SME is systematically absent. Fitting
thresholds to that sample would fit the model to its own discovery bias and lock the current
false negatives in permanently.

### What is missing, precisely
1. **Negative examples.** Zero of the last run's 14 rejections were retained. A calibration set
   without negatives cannot measure a false-negative rate — which is the *only* number that tells
   you whether the Lebanon gate is correctly tuned.
2. **Non-Shopify Lebanese SMEs.** ~9 per run, currently discarded unrecorded. They are the
   population you most need labelled before deciding whether to relax the Shopify constraint.
3. **Reliable evidence per candidate.** With followers missing on 23% and phone missing on 60%,
   many labels would rest on absent data. `nur-lebanon` is qualified on two absence-signals alone.
4. **Novel candidates.** ~2 per run against a saturated query set.

---

## 9. Observations 9 and 10 — objective and forbidden signals

**§9 (do not optimise blindly for volume):** honoured. Nothing in this report recommends
weakening the Lebanon gate, lowering `QUALIFY_AT`, or raising `REJECT_BELOW`. The two false
positives found argue for *more* `REVIEW`, not more `QUALIFIED`. The false negatives argue for
better extraction, not a lower bar.

**§10 (banned signals):** confirmed still absent from scoring. `product_count`, `vendor_count`
and `dominant_share` are collected in `collect_signals()` (`qualification.py:120-124`) but
`score_lead()` reads **none** of them — `dominant_share` is used only in the H1 mono-brand hard
rejection, which is a definitional test ("this catalogue IS one brand"), not a size proxy. No
website-quality, catalogue-size, product-count or collection-count signal is scored. The
documented V1 decision is intact. **I have used none of them in my labels above.**

---

## 10. Recommendation — the single highest-value next engineering step

> ## Persist every candidate. Add a run-scoped candidate ledger that records *all* discovered URLs — including rejections, non-Shopify sites, fetch errors, and duplicates — with the full signal record and verdict. Change no qualification behaviour.

Concretely: a new table (e.g. `candidates`) written for **every** URL in **every** run, capturing
at minimum: `run_id`, the originating query string, the raw URL and normalised domain, fetch
outcome, `is_shopify`, the full `signals_json` **whether or not the candidate was rejected**, the
verdict and reasons, the stage at which it exited the pipeline (`already_in_db` / `excluded_domain`
/ `not_shopify` / `fetch_error` / `hard_reject` / `scored` / `stored`), and a nullable
`human_label` column for hand-labelling. The existing `leads` table and every existing row stay
exactly as they are; `qualification.py` is not touched.

### Why this comes before everything else

**1. Every other finding in this report is currently unmeasurable without it.** The `lb`-suffix
recogniser gap, the `+2` platform-domain artefact, the `+2` own-brand ambiguity, the
identity-ordering defect, the Shopify-vs-WooCommerce question — each one is a change whose
effect is *how many candidates move between bands*. You cannot measure that against a pipeline
that deletes 96% of what it sees. Fix the weights first and you are tuning blind.

**2. It is the direct and only unblock for the ~30-candidate calibration set.** Today one run
yields 2 labelable rows. With the ledger, the *same* run yields **47** — including the 14
rejections and 15 non-Shopify sites you currently cannot see. One ledger-enabled run gets you
past 30. Two or three get you a set with real negatives in it.

**3. It supplies the negatives, which is where the actual risk lives.** The stated objective is
"find legitimate Lebanese SMEs while strongly avoiding large brands and false positives." The
false-positive side is already measurable — those rows are in the table. **The false-negative
side is completely invisible.** I found three probable false negatives only by re-fetching sites
by hand. Any future tightening of the gate will look like an improvement, because the leads it
wrongly kills leave no trace.

**4. It has zero model risk.** It is pure observability: no weight, no threshold, no gate rule,
no exclusion, no query, no identity rule changes. It cannot alter a single current verdict. That
makes it the one change that can safely go in *before* calibration rather than after — every
other item on the list needs the ledger's data to be justified, and several of them (the platform
bonus, the own-brand signal) would move real rows and should not be attempted on the current
evidence base.

**5. It makes runs reproducible and diffable.** With `run_id` and the originating query stored,
"what changed between runs" and "which query produced this candidate" become SQL queries instead
of a re-scrape. The per-query yield table in §3.1 cost 51 live fetches to produce; it should be
one `GROUP BY`.

### Explicitly *not* the next step, and why

- **Fixing the Lebanon `lb`-suffix recognition** — the most tempting fix, and it is a real gap.
  But it changes the gate, and the gate is the one component protecting you from the
  Pennsylvania/Texas/Hong Kong class of false positive. Changing it without a labelled negative
  set risks trading three false negatives for a dozen false positives, invisibly.
- **Removing the `+2 no-custom-domain` bonus** — correct in principle, but it moves 2 of 6
  platform rows today and you have no measurement of what else it moves at scale.
- **Moving identity resolution before qualification** — the right fix for Laptops King, and it
  should come second. It is a genuine ordering defect. But note the ledger *also* makes the
  problem visible for the first time (both twins recorded, merge candidacy inspectable), and the
  fix wants that data to verify against.
- **Expanding discovery beyond Shopify** — the largest upside in the report (~9 SMEs per run),
  but it is a strategy decision about what NorthBound sells, and it should be made *after* you
  can see the discarded population in a table and label it.

Ledger first. Then identity ordering. Then calibrate the gate and the weights against a set that
finally contains its own negatives.

---

## Appendix — files, line references, and verification state

| Claim | Where to verify |
|---|---|
| Rejections never persisted | `main.py:105-107` (`continue` precedes the insert at `main.py:129`) |
| Non-Shopify never persisted | `main.py:87-90` |
| Identity runs after qualification | `main.py:121` vs. `main.py:105` |
| Duplicate check is exact-domain only pre-qualification | `main.py:63` → `database.py:186-205` |
| `same_business()` merges the Laptops King pair | `identity.py:183-185`; verified live: `(True, 'same instagram: laptopsking')` |
| `+2` platform-domain bonus | `qualification.py:281-282` |
| `+2` own-brand signal | `qualification.py:138-151`, `qualification.py:274-276` |
| Lebanon gate policy | `scraper.py:761-774` |
| `lebanon_in_domain` misses `lb` suffix | `scraper.py:717` |
| Instagram `lb` regex requires a delimiter | `scraper.py:737` |
| Discovery capped at 5 queries × 10 results | `scraper.py:38-44`, `scraper.py:528-557` |
| Banned size signals still unscored | `qualification.py:224-297` (no read of `product_count` / `vendor_count`) |
| `instagram_followers` column not backfilled | `database.py:386-409` (`set_decision` omits it) |
| Test suite | `python -m pytest -q` → **179 passed** |

**Verification state of this report:** every quantitative claim above was produced by executing
code against the live web and the live database during this session. The database was opened
read-only throughout; no repository file was modified other than the creation of this report.
