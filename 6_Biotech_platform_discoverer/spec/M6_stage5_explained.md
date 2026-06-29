# M6 explained — Stage 5: rank, dedup, persist, export

*What milestone 6 does. Companion to `SPEC_acrivon_pattern_screener.md` §5.7 / §10 / §11 / §15 and
`decisions.md` D2 / D4.*

---

## In one sentence

M6 turns the persisted Claude `scores` into the **deliverable**: it ranks the universe, collapses any
leftover duplicate company-ids into one row per real company, routes borderline names back to the
review queue, records run metadata, and writes the analyst-house-format shortlist to
`Outputs/shortlist.md` — all with **no API calls and no deletions**.

## How to run

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_screen.py --stage 5
# writes Outputs/shortlist.md (override with --out). Re-render the HTML any time:
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_render.py
```

Stage 5 is **free and idempotent** — it only reads the latest `scores`, so re-run it as often as you
like. It ranks whatever has been scored so far (Stage 4); it doesn't trigger scoring.

## The pipeline inside Stage 5 (`stage5.run`)

```
latest_scores ─► rank (dedup + lifecycle weight) ─► refresh review_queue ─► record run_meta ─► export md
```

1. **`store.latest_scores()`** — the most recent score per company (MAX `run_id`, ISO-chronological),
   so re-scores supersede old ones.
2. **`rank()`** — one entry per company: `{composite, confidence, rubric, lifecycle_weight,
   rank_score, age_years, merged_ids}`, sorted by `rank_score` desc.
3. **review-queue refresh (§11)** — `substance_check ∈ {marketing, mixed}` or
   high-composite-but-low-confidence → back to the queue for a human look (thresholds in
   `config.yaml → stage5.review`).
4. **`run_meta`** — per-run metrics (shortlist size, duplicates collapsed, review adds, top score)
   persisted for the §15 run summary.
5. **export** — `Outputs/shortlist.md`: a ranked summary table + a dense prose memo per top-N
   (`stage5.shortlist_top`). `[V]`/`[INF]` labels and the Claude memo prose pass through verbatim.

## Dedup — the cardinal rule shapes it (D4a/D4b)

The store can hold two `company_id`s for one real company because `company_id = hash(name|listing)`
and the seed CSV ("Acrivon Therapeutics") and the SEC directory ("Acrivon Therapeutics, Inc.") give
different names → different ids. The cardinal rule (§0.2) forbids **deleting** a row for being a
duplicate, so Stage 5 collapses them **only in the shortlist**: it keeps the best-scored representative
and lists the rest as `merged_ids`; every row still exists in the store.

Matching is by **shared identity signals via union-find**, not a single per-row key. Each row emits a
*set* of signals — `isin`, `ticker+country`, or (only when tickerless) a legal-suffix-stripped name —
and two rows merge if they share **any** signal, transitively. This is load-bearing: the live ACRV
rows carry *asymmetric* identifiers (the seed row has an ISIN, the SEC row doesn't), so a
"strongest-key-per-row" scheme split them; the shared-signal union merges them on the common
`ticker+country`. Legal-entity suffixes (Inc/Corp/Ltd/AB/ASA/…) are stripped for the name fallback;
industry words (therapeutics/pharmaceuticals) are **not**, so "Foobar Therapeutics" and "Foobar
Pharmaceuticals" stay distinct. Live: 8 scored rows → **6 shortlist rows** (ACRV + RXRX collapsed).

## Lifecycle age-weighting — built, OFF by default (D2 / D4c)

A transparent ranking multiplier: `rank_score = composite × lifecycle_weight(age)`. It **never** alters
the auditable composite and never deletes — it only reorders.

| Age since IPO | weight |
|---|---|
| ≤ `old_threshold_years` (15) | **1.0** — the young are never penalized (rule 3a) |
| `old_threshold` … `hard_old_years` (15–20) | linear decay 1.0 → `floor` |
| ≥ `hard_old_years` (20) | **`floor`** (0.5) — the ship has sailed (rule 3b) |

Unknown age (no `ipo_date` yet) → **1.0** (recall-safe: never penalize for missing data). Enable with
`stage5.lifecycle.enabled: true`.

**The age signal (schema v3 prereq):** `companies.ipo_date` (ISO date) is populated on the next Stage-0b
`--enrich-yf` from yfinance `firstTradeDateEpochUtc`. NULL until then. SEC first-filing fallback and a
market-cap-appreciation discount are deferred (D2 phase 2).

## What it does NOT do

- **No scoring / no API** — that's Stage 4.
- **No deletions** — dedup is presentation-only; the guardrail still owns the only two delete reasons.
- **Appreciation-since-IPO** discount — deferred (needs price history).

## Tests

`tests/test_stage5.py` (15): lifecycle curve (disabled / unknown / young / old / decay band), the
`ipo_date` v3 round-trip + epoch conversion, ranking order, the three dedup tiers **plus the
asymmetric-identifier regression** (the live ACRV bug), the lifecycle reorder, review-queue routing,
the export + `run_meta` persistence, and the empty-store case. Whole suite: **114 pass**.
