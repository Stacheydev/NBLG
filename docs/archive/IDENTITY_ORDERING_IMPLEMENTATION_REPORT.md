# Identity-Ordering Fix — Implementation Report

**Stage:** the Laptops King defect, cause A (pipeline ordering).
**Scope:** one statement reorder in `main.py`, six new behavioural tests, one structural guard.
**Model risk:** none. No weight, threshold, gate rule, exclusion, query or identity rule changed.

---

## 1. What was wrong

`CALIBRATION_DIAGNOSTIC_REPORT.md` §4.2 identified two independent causes for the same business
being stored twice under two verdicts. This report closes **cause A only**. Cause B (the `+2 no
custom domain` bonus) is a weight change and stays deferred to post-label calibration.

Identity resolution ran *after* the rejection exit, so a rejected candidate never reached it:

```
decision = qualify(...)
if decision.outcome == REJECTED:   continue     <-- EXITS HERE
existing, reason = find_existing_business(...)  <-- never reached
```

The only duplicate check a rejected candidate ever received was `lead_exists(domain)` — an exact
string comparison that by construction cannot detect an alias.

The live consequence, as measured on the real sites:

| URL | Score | Verdict | Stored? |
|---|---|---|---|
| `laptopsking.com` | `-3` | REJECTED | no — vanished (ledger candidate 30) |
| `laptopskinglb-961.myshopify.com` | `-1` | REVIEW | yes — lead 64 |

The new tests reproduce the same asymmetry at `-4` / `-2`: the synthetic fixture omits the
WhatsApp number worth `+1` on both sides, which shifts both scores equally and leaves the `+2`
gap — and therefore the REJECTED/REVIEW split — intact.

Same business. Identical Instagram (`laptopsking`), phone, WhatsApp and follower count. Three
independent strong identifiers agree, and `identity.same_business()` returns
`(True, 'same instagram: laptopsking')` on the pair — the pipeline simply never asked it.

The verdict therefore depended on **which URL discovery happened to surface**, which is not a
property of the business.

---

## 2. The change

`main.py` — the identity block now sits before the rejection exit. Both blocks are otherwise
byte-identical to what they were; this is a reorder, not a rewrite.

```
decision = qualify(...)          # unchanged - qualification still runs first

record = {...}                   # MOVED UP
existing, reason = find_existing_business(record)
if existing:                     # -> ledger DUPLICATE + record_alias_domain()
    continue

if decision.outcome == REJECTED: # MOVED DOWN
    continue                     # -> ledger REJECTED, now with identity_checked=1

add_lead(...)
```

Two details worth stating explicitly:

- **The model's verdict is still recorded verbatim.** The duplicate branch already passed
  `decision=decision` to `ledger.record_outcome()`, which sets `qualification_ran=1` plus the
  decision, score, `rejection_kind`, reasons and full `signals_json`. A rejected candidate that is
  now booked as a duplicate loses none of its evidence, and `calibration.classify_tier()` tiers on
  `_model_verdict()` (which reads `qualification_ran`), not on `status` — so it still lands in
  `score_reject` / `lebanon_gate_reject` exactly as before.
- **The rejection branch now also records `identity_checked=1` and `identity_key`.** The check
  genuinely did run for it, and `identity_checked` is what distinguishes "checked, no match" from
  "never checked".

### What this does not do

`main.py` still stores no rejection, so if the *rejected* twin is discovered first there is
nothing in `leads` for the second twin to match against, and the second is stored on its own
merits. That is correct and is asserted in both directions by
`test_the_laptops_king_pair_never_becomes_two_leads`. The invariant the fix guarantees is **one
business, one lead** — not "the ledger always shows a DUPLICATE row".

---

## 3. Tests

Seven new cases in `tests/test_ledger.py`, all offline, reusing the existing `run_pipeline` harness.

Attribution was verified empirically, by temporarily restoring the old ordering and re-running
them: **4 fail against the old ordering and pass against the new one**; the other **3 pass either
way** and exist to prove the reorder did not over-correct.

| Test | Pins |
|---|---|
| `test_a_rejected_candidate_is_still_identity_checked` | a rejected alias is booked `DUPLICATE` at `identity_check`, verdict still recorded |
| `test_a_rejected_alias_is_recorded_on_the_stored_lead` | the alias reaches `aka_domains`; the stored lead's own verdict is untouched |
| `test_the_laptops_king_pair_never_becomes_two_leads` (×2) | one business → one lead in **either** discovery order |
| `test_a_rejected_candidate_that_is_new_is_still_rejected` | genuine rejections are not swallowed into duplicates |
| `test_identity_check_still_precedes_storage_for_good_leads` | the pre-existing guarantee survives the reorder |
| `test_identity_resolution_precedes_the_rejection_exit_in_main` | structural guard on the ordering itself (mutation-tested) |

```
before: 261 passed
after:  268 passed
```

---

## 4. Database

**No database change.** Verified by a field-by-field snapshot diff across every row of every
table: 0 cells changed, schema identical, all counts identical (52 leads, 96 ledger, 97 sources,
0 labels, 2 runs, 28 `active_leads`, 23 `rejected_leads`), both views byte-identical.

The fix changes how *future* runs behave. It deliberately does not retro-merge lead 64 with
`laptopsking.com`: that would be a manual edit to a stored verdict, which this project does
through evidence and the pipeline, not by hand.

---

## 5. Recommended next step — unchanged, and still blocked on one human input

> ## Label the queue. `data/calibration_round1.csv` is written and waiting: 40 candidates, every tier represented, all `human_label` cells blank.

This report does not advance the calibration roadmap, because that roadmap has been blocked on the
same input since `HUMAN_LABELING_IMPLEMENTATION_REPORT.md` §12: **human labels, which only the
reviewer can supply.** The identity-ordering fix was the one item on the post-ledger list that is
independent of any weight, which is why it could be done first.

Everything downstream still needs the labels:

- Should the Lebanon gate recognise the `lb` suffix? → 7 `lebanon_gate_reject` rows in round one.
- Should the `+2 .myshopify.com` bonus go? → this is **cause B** of the very defect above, and the
  sensitivity analysis already shows it is the sole reason two `review` rows are not rejections.
- Is `REVIEW` right for the Carpisa shape? → 3 `model_review` rows.
- Should discovery go beyond Shopify? → 6 `not_shopify` rows.

Round one, ready to fill:

```bash
python label.py queue --limit 40          # read the queue
python label.py show 11                   # full evidence for one candidate
# fill the human_label column (YES / NO / UNCERTAIN) in data/calibration_round1.csv
python label.py import --csv data/calibration_round1.csv --reviewer hadi
python label.py report                    # model vs human, once labels exist
```

Label the **business**, not the model's verdict, and use `UNCERTAIN` freely — it is excluded from
accuracy rather than folded into `NO`.
