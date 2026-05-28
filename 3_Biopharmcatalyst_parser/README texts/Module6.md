# Module 6 — Scoring & ranking

**Plain-English purpose:** Module 6 takes the ~570-row catalyst
universe coming out of Modules 0–5 and turns it into a **ranked
shortlist of investment candidates**, persisted to a new table
`catalyst_scores` and rendered to a self-contained HTML report at
`Outputs/catalyst_scores.html`.

Two things happen in one pass:

1. **Hard filtering** — drop catalysts that are structurally outside
   the investment scope (large-cap, post-approval, past-window, etc.).
   ~503 of 572 rows are filtered out on the reference snapshot.
2. **Soft scoring** — rank the surviving ~69 catalysts on three
   weighted signals (insider buying, price momentum, fund accumulation)
   to a composite 0–100 score. The user reviews the ranking and picks
   a slice to send to Module 7 (Claude API deep-dive).

The goal is to compress a 296-ticker universe into a **portfolio of
~20 names** without losing anything important and without paying
Anthropic to deep-dive on uninteresting candidates.

---

## The "too many catalysts" problem (why M6 has to exist)

Modules 0–5 give you everything you need to *describe* the universe:

- 572 catalyst rows in `catalyst_snapshots`
- 153/63/351/5/0 rows in the four resolver lanes from M5
- ~9,300 insider open-market trades in `v_executive_open_market_trades`
- ~2,200 13D/G institutional filings in `edgar_ownership_filings`

What they don't give you is a **ranked candidate list**. Eyeballing
572 rows weekly is impractical; throwing all of them at Claude is
prohibitive (~$50 per snapshot run at Opus 4.7 prices). You need a
two-stage funnel:

```
   572 catalysts in snapshot          ← M0..M5 output
        ↓
   hard filter H1..H6
        ↓
   ~20-70 surviving catalysts         ← M6 hard pass
        ↓
   sort by composite_score
        ↓
   user picks N (typically 10-30)
        ↓
   Claude API deep-dive               ← M7
        ↓
   ~20 portfolio names                ← user's final pick
```

M6 is the funnel's neck. It encodes **exactly what the user is
looking for** in terms of cap size, stage, timing, event type, and
which conviction signals matter — without making any "interesting"
judgement (that's Claude's job in M7).

---

## What it actually does, step by step

When you run `scripts/3_6_score_catalysts.py`:

1. **Loads `config/scoring.yaml`**, validates it with pydantic, and
   computes a `rules_version` string of the form `v1.0:<sha7>` where
   the sha7 is the first 7 chars of the YAML's SHA-256 hash. Every
   edit to the YAML produces a different `rules_version`, so re-runs
   after a tuning bump re-score even on PK collision.

2. **Picks the target snapshot(s).** Default: most recent
   `snapshot_date` in `catalyst_snapshots`. Override with
   `--snapshot-date YYYY-MM-DD` for one specific snapshot, or
   `--all-snapshots` to re-score every historical snapshot under
   the current YAML.

3. **Reads every catalyst** at that snapshot, joining
   `catalyst_snapshots` to `catalyst_timing` so each row carries
   `(market_cap_usd, stage, next_catalyst_type, price_history_30d,
   precision_tier, date_min, date_max)` — everything the filters
   and scorers need.

4. **Pre-fetches the insider trades** for those tickers from
   `v_executive_open_market_trades`, filtered to `buy_sell='Buy'`
   and `filing_date` within `[snapshot_date − 365d, snapshot_date]`.
   Snapshot-anchored so re-runs are reproducible.

5. **ATTACH-es `2_Funds_parser/2_fundparser.db`** read-only and
   computes per-ticker fund accumulation between its two most recent
   quarters (e.g., 2026-03-31 vs 2025-12-31). If the funds DB is
   missing or unreadable, M6 logs a warning and falls back to
   `--skip-funds` mode silently — no abort.

6. **For each catalyst row**, runs:
   - **`apply_hard_filters`** → `FilterVerdict(hard_pass,
     fail_reasons, timing_bucket)`. All H1–H6 are evaluated; all
     failures are recorded, not just the first.
   - **`compute_insider`** → role-weighted gross USD log-scaled to
     0–100. CEO trades count × 2.0, CFO trades × 1.0, everything
     else × 0.
   - **`compute_momentum`** → parse the BPC `price_history_30d`
     semicolon-string, compute 30d return%, map through the
     piecewise curve in YAML to 0–100.
   - **`compute_fund_accumulation`** → log-scale the positive-delta
     accumulation USD to 0–100. Score = 0 when ticker absent from
     funds DB.
   - **`composite`** → weighted sum: 0.35 × insider + 0.35 × momentum
     + 0.30 × funds. In `--skip-funds` mode the two remaining weights
     are renormalised so the composite still lands in [0, 100].

7. **Writes everything in a single transaction.** `INSERT OR REPLACE
   INTO catalyst_scores`. Every input row produces exactly one
   output row — hard-pass=0 rows still get persisted with
   `fail_reasons` populated so the user can audit exclusions later.

8. **Writes an `ingest_log` row** with `module = 'score_catalysts'`,
   `input_ref = <snapshot_date>`, row counts, and `status = 'success'`
   (or `'partial'` if the funds DB latest quarter is > 180 days
   before the snapshot — soft "stale" warning).

9. **Prints a summary** with the hard-pass count, bucket split, fail
   reasons distribution, and the two funds quarters used.

The HTML render is a second script `scripts/3_6_render_scores.py`
that re-reads `catalyst_scores`, JOIN-s `catalyst_snapshots` and
`catalyst_timing` for display columns, ATTACH-es the funds DB
again for per-fund expand-panel detail, and writes
`Outputs/catalyst_scores.html` (~700 KB self-contained).

---

## The six hard filters

Applied to every row; **all failures collected** (not short-circuit),
so the `fail_reasons` column on excluded rows shows every reason.
H1–H6 are designed so a clean small-cap clinical-readout catalyst
on an actively-traded ticker in the forward window passes all six.

> **D34 update (2026-05-28):** added H6 — the curated `delisted_tickers`
> allowlist. BPC's docx sometimes carries a market cap from before a
> ticker was delisted; H1 would let that row through, but yfinance and
> Anthropic can't price it. H6 closes that loophole. The allowlist lives
> in `biotech.db.delisted_tickers`; manage it via
> `scripts/3_flag_delisted_tickers.py --add <TICKER>`.

### H1 — Market cap band

`30_000_000 <= market_cap_usd < 2_000_000_000`

Small/mid-cap sweet spot. Above $2B, individual catalysts rarely
move the share price enough to matter (diluted by the rest of the
business). Below $30M ("dead pool" floor) the tape is too thin for
catalyst-driven re-rating to play out cleanly. **Rejects 303 of 572
rows** on the reference snapshot.

### H2 — Timing resolvable

`precision_tier != 'unknown'`

Can't rank a catalyst with no inferable date. **Rejects 0 rows** on
the current snapshot because M5's resolver caught everything; this
filter is here for future snapshots where unknowns may occur.

### H3 — Forward-looking window

`date_min >= snapshot_date + 14 days`

T+14 is the locked Discovery/Execution window start (spec §11).
Catalysts inside 14 days are too late to position. There is **no
upper bound** — the user wants to see anything forward-looking even
if 200+ days out, with the precision class shown via the timing
bucket. **Rejects 362 of 572 rows** — this is the biggest filter, and
catches most of the BPC stale-row problem.

### H4 — Window not entirely past

`date_max >= snapshot_date`

Removes rows where the entire catalyst window has lapsed but BPC
still ships them in the source CSV. **Rejects 5 of 572 rows.**

### H5 — Clinical readout event

```
stage IN ('phase1', 'phase2', 'phase3')
AND next_catalyst_type IN (
    'Interim Data', 'Initial Data', 'Topline Data',
    'Full Results', 'Conference Presentation'
)
```

The user-locked scope: **phase 1/2/3 clinical readouts only**.
Explicitly excluded by H5:

- `Regulatory Decision` (PDUFA) — approval-decision plays, not data
- `Submission` (NDA/BLA filing milestones) — process, not data
- `End of Phase Meeting` — FDA process, not a readout
- `stage = phase4` (post-pivotal commercial)
- `stage = phase5` (already approved)
- NULL `next_catalyst_type` (BPC's no-imminent-event placeholders)

**Rejects 136 of 572 rows.**

### H6 — Ticker not on the delisted allowlist (D34)

```
UPPER(ticker) NOT IN delisted_tickers
```

Curated table `biotech.db.delisted_tickers`, populated manually via
`scripts/3_flag_delisted_tickers.py --add <TICKER> --reason "<why>"`.
Seeded with DVAX (yfinance 404; reverse-split delisting). Case-insensitive
match. Rejects 1 of 572 rows currently.

Side benefits of routing exclusion through this gate (rather than ad-hoc
filtering in downstream code):

- M7 dispatcher (`fetch_hard_pass_candidates`) already queries
  `WHERE hard_pass = 1`, so delisted tickers are automatically excluded
  from Anthropic dispatches with no extra code.
- The live-price server's hard-pass allowlist (D28) is built from the
  same query — delisted tickers never reach yfinance, avoiding wasted
  API calls and stderr noise.
- The HTML naturally shows delisted rows in the Excluded tab with an
  H6 chip; the H-gate legend explains where the list comes from.

### After H1–H6: timing-bucket partition (NOT a filter)

Surviving rows are tagged with `timing_bucket`:

| Bucket | `precision_tier` values | Window width |
|---|---|---|
| `catalyst_date_defined` | `specific`, `conference`, `month`, `quarter` | ≤ 90 days |
| `catalyst_date_undefined` | `half`, `year` | > 90 days |

The HTML report shows these as two separate tabs. Ranking happens
within each bucket. The mental model: "we know when this is
happening" vs "this is sometime in H2 2026."

---

## The three soft signals

User-locked: **exactly three signals**, no others. Everything else
that could have been weighed (sentiment, momentum hard-cap, LOA,
13D/G, multi-catalyst optionality, recency decay) was actively
considered and explicitly rejected — see D8 in `spec/decisions.md`.

### Signal 1 — Insider score (35% weight)

CEO + CFO open-market buys over the last 365 days from
`v_executive_open_market_trades`. The view is already a UNION of
EDGAR Form 4 transactions (filtered to `is_open_market = 1`) and
the BPC insider supplement.

```
insider_gross_weighted_usd
    = SUM_over_trades( role_weight × gross_usd )

  where role_weight = { CEO: 2.0, CFO: 1.0, everything else: 0 }

insider_score
    = log10(1 + insider_gross_weighted_usd)
      / log10(1 + insider_norm_cap)
      × 100
      capped at 100
      0 when no qualifying trades
```

- Default `insider_norm_cap = $5_000_000`. A CEO buy of $5M alone
  hits 100.
- **No recency decay.** A buy from day -350 contributes the same as
  one from day -5, as long as it's inside the 365-day window.
- **10% owners are excluded** — they're typically PIPE participants
  buying at private placements, not a conviction signal.
- **Directors / CMO / COO / CSO / President / Chair / Other-officer
  are all excluded** (weight 0). The view classifies them for audit,
  but only CEO and CFO actually count toward the score.

### Signal 2 — Momentum score (35% weight)

30-day price return from BPC's `price_history_30d` column (a
semicolon-separated string, oldest-first), mapped through a
piecewise-linear curve:

| 30d return % | momentum score |
|---|---|
| ≤ −50% | 0 (broken / pipeline-failure tape) |
| −50% → −10% | linear 0 → 60 |
| −10% → +10% | flat 100 (consolidation = ideal pre-catalyst tape) |
| +10% → +30% | linear 100 → 70 |
| +30% → +60% | linear 70 → 20 (price already moved on the catalyst) |
| ≥ +60% | 0 (already priced in) |
| NULL (< 2 valid prices) | 50 (neutral; do not penalise missing data) |

The curve is **inverted-U around 0%**: we want pre-catalyst
consolidation, not "the stock already ran." But the score is **never
a hard exclusion** — even a +100% mover stays in the shortlist,
just with momentum_score = 0.

### Signal 3 — Fund accumulation score (30% weight)

Cross-database read of `2_Funds_parser/2_fundparser.db` (22
specialist biotech funds tracked: Baker Brothers, Deerfield, OrbiMed,
BVF, Perceptive, RA Capital, RTW, Redmile, Cormorant, EcoR1, SIO,
Avoro, PFM Health Sciences, ARCH, Atlas, 5AM, Versant, Janus
Henderson Biotech, Boxer, Sofinnova, Athos).

For each (BPC ticker, latest quarter Q, previous quarter Q−1):

```
for each (ticker, fund_id):
    share_delta = shares_Q − shares_Q_prev    # missing rows = 0
    price_proxy = market_value_Q / shares_Q   # only when shares_Q > 0
    fund_contribution = MAX(0, share_delta) × price_proxy

fund_accumulation_usd = SUM(fund_contribution) across all funds

fund_accumulation_score
    = log10(1 + fund_accumulation_usd)
      / log10(1 + fund_norm_cap)
      × 100
      capped at 100
      0 when ticker absent from funds DB or accumulation_usd ≤ 0
```

- Default `fund_norm_cap = $50_000_000`. Sized for 22 funds (~10×
  the insider cap reflecting ~10× the participant count). Ticker
  with $50M net positive accumulation across all funds = 100.
- **Only positive deltas count.** A fund trimming its position doesn't
  subtract from the score (mirrors the insider "buys only" symmetry).
- **No recency decay** — quarterly granularity already imposes a 90d
  floor.
- **Ticker not in funds DB** → score 0 (no signal, no penalty).
  ~38% of BPC tickers fall here on the reference snapshot.
- **Stale funds DB** (latest quarter > 180 days before snapshot) →
  warning logged, `ingest_log.status = 'partial'`, but still scored.

`funds_holding_latest` and `funds_holding_previous` are persisted
in `catalyst_scores` as informational breadth metrics — they help
audit why a score is what it is, but don't enter the formula
directly (the dollar-weighted accumulation already captures both
breadth and magnitude).

### Composite

```
composite_score
    = 0.35 × insider_score
    + 0.35 × momentum_score
    + 0.30 × fund_accumulation_score
```

Weights sum to 1.0 → composite stays in [0, 100]. Ranking within
each timing bucket uses `composite_score DESC, insider_score DESC,
fund_accumulation_score DESC` as the tiebreak chain — the user
weighted insider as the highest-conviction signal, so it breaks
ties even though the formula treats it as equal-weight to momentum.

When you run with `--skip-funds` (e.g., the funds DB is being
refreshed), the funds weight drops to 0 and the two remaining
weights are **renormalised** to 0.5/0.5 so composite still lands
in [0, 100].

---

## What "slightly lower than CEO/CFO" means here

The user described fund accumulation as "scored with slightly lower
score as purchase by CEO/CFO." That translated to:

- `w_funds = 0.30` (slightly lower)
- `w_insider = 0.35` (the conviction reference)
- `w_momentum = 0.35` (held at equal weight to insider per the
  earlier "same as CEO/CFO" decision)

It is not "half the weight" — the funds weight is 86% of the insider
weight. Tune `composite.weight_*` in `config/scoring.yaml` if the
empirical rankings feel off in either direction.

---

## The output

### `catalyst_scores` (one row per `catalyst_snapshots` row)

| Column | Type | What it holds |
|---|---|---|
| `snapshot_date` | DATE | (PK) — matches `catalyst_snapshots` |
| `ticker` | TEXT | (PK) |
| `drug` | TEXT | (PK) |
| `nct_number` | TEXT | (PK) |
| `next_catalyst_type` | TEXT | (PK) |
| `hard_pass` | BOOLEAN | 1 if all H1–H6 passed; 0 otherwise |
| `fail_reasons` | TEXT | comma-joined H-codes; NULL on hard_pass=1 |
| `timing_bucket` | TEXT | `catalyst_date_defined` / `catalyst_date_undefined`; NULL on hard_pass=0 |
| `insider_gross_weighted_usd` | REAL | role-weighted CEO+CFO buy total; NULL if zero |
| `insider_score` | REAL | 0–100 |
| `return_30d_pct` | REAL | raw 30d return from price_history; NULL on parse fail |
| `momentum_score` | REAL | 0–100 |
| `fund_quarter_latest` | TEXT | e.g. `2026-03-31`; NULL if funds DB skipped/absent |
| `fund_quarter_previous` | TEXT | e.g. `2025-12-31` |
| `funds_holding_latest` | INTEGER | count of tracked funds with positive position |
| `funds_holding_previous` | INTEGER | same, previous quarter |
| `fund_accumulation_usd` | REAL | positive Δshares × price proxy; NULL if zero |
| `fund_accumulation_score` | REAL | 0–100 |
| `composite_score` | REAL | 0–100; NULL when hard_pass=0 |
| `computed_at` | TIMESTAMP | when this row was written (ISO UTC) |
| `rules_version` | TEXT | `v1.0:<sha7>` of the YAML at compute time |

Two indexes: `(snapshot_date, composite_score DESC)` for top-N
queries, and `(timing_bucket, hard_pass)` for bucket-level scans.

### `Outputs/catalyst_scores.html`

Self-contained dark-themed HTML report (~700 KB). Three tabs:

1. **Defined timing** (36 rows on reference snapshot) — hard-pass
   rows where `precision_tier` is specific/conference/month/quarter.
2. **Undefined timing** (33 rows) — hard-pass rows in half/year.
3. **Excluded** (503 rows) — hard-pass=0, with `fail_reasons` tags
   shown in place of the precision tier.

Features:

- KPI strip on top: total / hard-pass / per-bucket counts /
  with-insider-buy / with-fund-accum / fail-reason summary.
- Composite score distribution bar (proportional segments for
  80–100, 60–80, 40–60, 20–40, 0–20 bands).
- Sortable column headers (click to toggle asc/desc, indicator
  arrow shown).
- Filter bar: minimum composite score, insider-buy required/none,
  fund-accum required/none, stage dropdown, free-text search by
  ticker or drug. **All filters persisted to `localStorage`** so
  the next render restores your view.
- Click any row to expand: catalyst text with the M5 matched-phrase
  highlighted, full signal breakdown (raw gross USD, raw return%,
  fund quarters, funds-holding count), per-fund position delta
  table (reading the attached funds DB at render time), and the
  qualifying CEO/CFO insider buys table.

### `ingest_log` (one row per `score_catalysts` run)

The standard audit row: `module = 'score_catalysts'`, `input_ref =
<snapshot_date>`, `rows_in`, `rows_inserted`, `rows_updated`,
`rows_rejected` (always 0 — M6 doesn't reject; it tags),
`status` (`success` / `partial` if funds stale / `failed`),
`started_at`, `finished_at`.

### Production results (snapshot 2026-05-27)

| Metric | Value |
|---|---|
| catalyst_scores rows | 572 (one per input) |
| hard_pass = 1 | 69 |
| catalyst_date_defined bucket | 36 |
| catalyst_date_undefined bucket | 33 |
| hard_pass = 0 with H1 | 303 (large-cap) |
| hard_pass = 0 with H3 | 362 (past/too-soon) |
| hard_pass = 0 with H4 | 5 (entirely past) |
| hard_pass = 0 with H5 | 136 (PDUFA / Submission / phase4–5) |
| composite_score range | 16.7 .. 89.1 (avg 45.9) |
| fund signal positive (of hard_pass) | 39 of 69 (57%) |
| funds quarters used | 2026-03-31 vs 2025-12-31 |

Top of the defined-timing leaderboard:

| Rank | Ticker | Drug | Stage | Composite | Insider | Mom | Funds |
|---:|---|---|---|---:|---:|---:|---:|
| 1 | MBX | MBX 4291 | phase1 | 88.8 | 92.6 | 100.0 | 71.3 |
| 2 | CMPX | CTX-10726 | phase1 | 81.3 | 69.0 | 85.7 | 90.5 |
| 3 | TENX | TNX-103 (oral levosimendan) | phase3 | 72.7 | 74.5 | 48.1 | 99.3 |

Top of the undefined-timing leaderboard: ALXO at 89.1 (all-three-
signals strong), CABA at 77.4, BCAX at 65.0.

Top fund accumulator across the whole universe: SNDX at $110M
(rank 9 in defined-timing because its insider signal is 0 and
momentum mid-range — strong single-signal name).

---

## What it explicitly does NOT do

- ❌ It does not call the Anthropic API. M7 does that.
- ❌ It does not reach out to the internet at all. M6 is fully
  offline — `catalyst_snapshots`, `catalyst_timing`,
  `v_executive_open_market_trades`, and the ATTACH-ed funds DB are
  the only inputs.
- ❌ It does not score sentiment, BPC's "Bull/Neutral/Bear" string,
  Historical LOA/POP, 13D/G ownership filings, multi-catalyst
  optionality, catalyst-date-slip across snapshots, recency of
  insider trades, or buy-vs-sell ratios. Each of these was
  considered and explicitly rejected by the user — see D8.
- ❌ It does not subtract from any score on negative signals (fund
  exits, sells, etc.). The two scoring signals are "buys only"
  symmetric.
- ❌ It does not cap or floor the number of rows sent to M7. The
  user picks the slice manually after reviewing
  `Outputs/catalyst_scores.html`.
- ❌ It does not modify `catalyst_snapshots` or `catalyst_timing`.
  M6 is a pure derived layer.
- ❌ It does not auto-refresh `2_Funds_parser/2_fundparser.db`. That
  pipeline runs independently on a quarterly cadence; M6 reads
  whatever quarters are present.
- ❌ It does not pin a Module 7 prompt or output schema. M7's design
  is unstarted.

---

## How to run it

From the parser folder, with the shared venv active:

```bash
# Default: score the most recent snapshot
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py

# Specific snapshot
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py \
    --snapshot-date 2026-05-27

# Re-score every historical snapshot (after a scoring.yaml edit)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py \
    --all-snapshots

# Bypass the funds signal (renormalises insider+momentum to 0.5/0.5)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py \
    --skip-funds

# Point at a non-default config
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py \
    --config config/scoring_experimental.yaml

# Render the HTML report
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_render_scores.py
```

Or run `run_3_Biopharmcatalyst_parser.bat` — M6 is wired in as
the final step with a y/N gate; after it succeeds, a second [Y/n]
gate offers to render the HTML report.

To verify everything still meets spec:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_module6_*.py -v
```

65 tests across config validation, hard-filter rules, scoring
math, funds_reader cross-DB JOIN logic, and end-to-end ingest with
a synthetic funds DB fixture.

---

## The files M6 is made of

```
src/module_6/
  __init__.py              # makes 'module_6' importable
  config.py                # pydantic ScoringConfig + YAML loader + rules_version hashing
  filters.py               # pure: apply_hard_filters(...) -> FilterVerdict
  scoring.py               # pure: compute_insider/momentum/fund_accumulation + composite
  funds_reader.py          # ATTACH 2_fundparser.db (read-only); per-ticker accumulation SQL
  ingest.py                # orchestrator: read inputs, ATTACH funds, score, upsert, log

scripts/
  3_6_score_catalysts.py   # CLI for scoring
  3_6_render_scores.py     # CLI for HTML render

config/
  scoring.yaml             # ALL thresholds, weights, curve points

tests/
  test_module6_config.py        # 7 tests — YAML validation
  test_module6_filters.py       # 22 tests — H1..H6 boundary coverage
  test_module6_scoring.py       # 23 tests — insider/momentum/funds/composite math
  test_module6_funds_reader.py  # 8 tests — synthetic mini-funds-DB
  test_module6_ingest.py        # 5 tests — end-to-end with attached funds DB
```

**`config.py`** is the gate. Edit `scoring.yaml`, and pydantic
validates: H1 min < max, timing-buckets disjoint, momentum curve
strictly ascending in `return_pct`, composite weights sum to 1.0
(±0.01). The file's SHA-256 first-7 is appended to
`rules_version_label` so any byte-level edit re-versions.

**`filters.py`** is pure (no DB, no config I/O at run time — takes
the validated config in). `apply_hard_filters(...)` runs H1–H6,
collecting all failures rather than short-circuiting.

**`scoring.py`** is pure. Four small functions:
- `compute_insider(trades, cfg)` — apply role weights, log-scale.
- `compute_momentum(price_history_30d, cfg)` — parse string,
  piecewise-linear-interpolate.
- `compute_fund_accumulation(accumulation_usd, cfg)` — log-scale.
- `composite(insider_score, momentum_score, fund_accumulation_score,
  cfg, skip_funds=False)` — weighted sum with skip-funds
  renormalisation.

**`funds_reader.py`** wraps the cross-DB pattern:
- `attach_funds_db(conn, path)` — adds the funds DB at alias
  `funds` to an existing biotech.db connection.
- `detach_funds_db(conn)` — best-effort cleanup; idempotent.
- `load_fund_accumulation(conn, tickers, snapshot_date,
  stale_warning_days)` — one SQL query identifies the two latest
  quarters and computes per-ticker positive Δshares × price proxy
  across all funds. Returns a `FundsContext` with quarter labels,
  stale flag, and `{ticker: FundAccumulationRow}` dict.

**`ingest.py`** is the orchestration: open biotech.db, ATTACH funds
DB (or fall back to skip), apply filters, score, upsert with
`INSERT OR REPLACE`, write `ingest_log`. Catches `FundsDBError` and
falls back to `skip_funds = True` silently — a missing funds DB
should not abort the pipeline.

**`scripts/3_6_score_catalysts.py`** is the thin CLI: argparse,
config load, target-snapshot selection, calls into `ingest.py`,
prints the bucket/fail-reason breakdown.

**`scripts/3_6_render_scores.py`** is the renderer: re-reads
`catalyst_scores`, ATTACH-es the funds DB once more for per-fund
expand-panel detail, embeds everything as JSON inside an HTML
file with vanilla JS (no build step, no external network).

---

## When you'll need to re-run M6

- **Every time Module 1 ingests a new snapshot**, after Modules 2/3
  (insider data) and the M5 timing pass have run. The .bat wires
  this as the final step.
- **After `2_Funds_parser` ingests a new quarter** — re-run M6 to
  pull in the fresh accumulation signal. Just M6, not the whole
  pipeline.
- **After editing `config/scoring.yaml`** — the SHA-7 stamp changes,
  re-runs produce updated scores even on PK collision. Use
  `--all-snapshots` to back-fill historical snapshots under the
  new weights.
- **Re-running on the same snapshot with the same YAML** is a safe
  no-op (PK collision; `rows_inserted=0, rows_updated=572`; scores
  identical).
- **Never** as a side-effect of something else. M6 is always
  explicit (y/N gate in the bat).

---

## Why these design choices

A handful that aren't obvious from reading the code or the spec:

- **Hard-pass=0 rows are persisted, not dropped.** Every input row
  produces exactly one `catalyst_scores` row. Failed rows carry
  `fail_reasons` so you can audit "why didn't ticker XYZ make the
  cut" without re-running the filter. This also makes the HTML
  report's "Excluded" tab possible — a feature the user explicitly
  wanted (to spot edge cases where H5 might be too aggressive).

- **All H1–H6 failures are collected, not short-circuited.** A row
  that fails on H1 AND H5 gets `fail_reasons = 'H1,H5'`. This
  surfaces the full "shape" of why something was excluded; users
  often want to know "is this filtered because of cap size OR
  because of stage?" — both, in the dual-fail case.

- **The `unknown` precision tier is its own H2 filter, not silently
  filtered.** H2 stays in the rule set even when M5 produces zero
  unknowns. It documents the boundary contract: a row with no
  inferable date is structurally unscoreable.

- **No upper bound on `date_min` in H3.** Initially considered
  capping at T+180 (the spec's discovery window upper bound). The
  user removed the cap: they want to see distant catalysts too,
  classified into the "undefined" bucket. Lets the same scoring
  layer serve both near-term position management and a watch-list
  view of further-out names.

- **`fund_accumulation_usd = NULL when 0`, not `0.0`.** Same for
  `insider_gross_weighted_usd`. NULL distinguishes "we don't have
  this signal" from "we computed it and got exactly zero." For
  audit, the NULL is the right shape — `SELECT * FROM
  catalyst_scores WHERE fund_accumulation_usd IS NULL` returns the
  82 hard-pass rows with no fund signal, cleanly separate from any
  hypothetical "fund position unchanged" rows.

- **`rules_version` includes the YAML's SHA-7 hash.** Any byte-level
  edit to `scoring.yaml` produces a different `rules_version`.
  Re-runs on the same snapshot then re-score even though the PKs
  collide — `INSERT OR REPLACE` overwrites the old scores. You can
  query `SELECT rules_version, COUNT(*) FROM catalyst_scores GROUP
  BY 1` to detect partial-rollout situations.

- **Funds DB ATTACH-ed at runtime, not replicated into biotech.db.**
  Considered building a Module 4b that ETLs quarterly fund holdings
  into biotech.db, then dropped that idea (D9). Reasoning:
  `2_Funds_parser` refreshes quarterly (12× slower than M6's
  weekly cadence), so a replica would mostly be stale; ATTACH adds
  zero schema to biotech.db; the user already runs
  `run_2_Funds_parser.bat` independently. A missing funds DB
  triggers `--skip-funds` fallback silently, not an abort.

- **`--skip-funds` renormalises the two remaining weights to 0.5/0.5
  rather than scaling by 0.7.** If we just zeroed out the funds
  weight, composite scores would max out at 70 instead of 100 —
  which would distort the visual score-band thresholds in the HTML
  report. Renormalising keeps the score scale stable.

- **No recency decay on insider trades.** Considered for D8;
  rejected. The user reasoned that a CEO buy from day -350 is still
  a 365-day-window conviction signal — the relevant horizon for
  pre-catalyst positioning is much shorter than the typical
  position-holding period.

- **CMO / COO / CSO are weighted 0, not just downweighted.** The
  empirical analysis showed average CMO/COO/CSO buy size in the
  $10–15k range — looks like ESPP/automated participation rather
  than discretionary conviction. The user-chosen response was to
  zero them out rather than try to detect non-ESPP cases.

- **Tiebreaker prefers insider_score over fund_accumulation_score
  over momentum_score.** Even though formula-wise insider and
  momentum are equal-weighted, the user described insider as
  higher-conviction. The tiebreak chain expresses this preference
  in the rare composite-ties case.

- **Test isolation via `get_connection(tmp_path)`.** The existing
  `database/db.py` already supports a custom DB path; M6's tests
  pass a tmp path so they don't pollute production `biotech.db`.
  Synthetic mini funds DBs in the tests are built via raw
  `sqlite3.connect()` with a minimal `holdings` table — enough to
  exercise the SQL JOIN logic without depending on a real
  `2_fundparser.db`.

---

## Reading the production DB after a run

Quick sanity dump showing the bucket counts + a top-10 within each:

```bash
../.venv/Scripts/python.exe -c "
import sqlite3
c = sqlite3.connect('data/biotech.db'); c.row_factory = sqlite3.Row

snap = c.execute('SELECT MAX(snapshot_date) FROM catalyst_scores').fetchone()[0]
print(f'Latest snapshot: {snap}')
print()

# Bucket counts
for r in c.execute('''
  SELECT timing_bucket, COUNT(*) AS n
  FROM catalyst_scores
  WHERE snapshot_date = ? AND hard_pass = 1
  GROUP BY timing_bucket
''', (snap,)):
    print(f'  {r[\"timing_bucket\"]:26s} {r[\"n\"]:>4d}')
print()

# Top 10 within each bucket
for bucket in ('catalyst_date_defined', 'catalyst_date_undefined'):
    print(f'--- top 10 in {bucket} ---')
    print(f'  {\"Ticker\":7} {\"Stage\":7} {\"Comp\":>5} {\"Ins\":>5} {\"Mom\":>5} {\"Fund\":>5}')
    for r in c.execute('''
      SELECT cs.ticker, cs.composite_score, cs.insider_score, cs.momentum_score,
             cs.fund_accumulation_score, s.stage
      FROM catalyst_scores cs
      JOIN catalyst_snapshots s USING (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
      WHERE cs.snapshot_date = ? AND cs.hard_pass = 1 AND cs.timing_bucket = ?
      ORDER BY cs.composite_score DESC, cs.insider_score DESC
      LIMIT 10
    ''', (snap, bucket)):
        print(f'  {r[\"ticker\"]:7} {r[\"stage\"]:7} '
              f'{r[\"composite_score\"]:>5.1f} {r[\"insider_score\"]:>5.1f} '
              f'{r[\"momentum_score\"]:>5.1f} {r[\"fund_accumulation_score\"]:>5.1f}')
    print()
"
```

---

## TL;DR

Module 6 takes the ~570-row M5-output universe and produces a
ranked shortlist of investment candidates by:

1. **Hard-filtering** out catalysts the user doesn't care about
   (large-cap, post-approval, past-window, non-clinical-readout).
2. **Soft-scoring** the survivors on three weighted signals (CEO+CFO
   buys 365d / 30d price momentum / 22-fund accumulation last
   quarter) into a composite 0–100 score.
3. **Partitioning** survivors into two timing buckets — known-when
   vs sometime-this-year — so ranking happens at like-for-like
   precision.

All thresholds and weights live in `config/scoring.yaml` and are
content-hashed into `rules_version` for tunability without code
changes. Cross-database read of `2_Funds_parser/2_fundparser.db`
via ATTACH (read-only, runtime-attached, no schema replication).
Output is the `catalyst_scores` table plus a self-contained
`Outputs/catalyst_scores.html` report with three tabs, sortable
columns, localStorage-persisted filters, and expandable per-row
detail showing the full signal breakdown.

The user then reviews the report and picks the slice to send to
Module 7. No automatic top-N cap — the funnel's narrowing happens
at hard-filter + composite-score-ranking time, not by truncation.
