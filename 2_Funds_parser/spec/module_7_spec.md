# Module 7 — Outcome tracking + feedback loop

**Status:** 📋 Draft specification (2026-04-25). Not implemented.
**Last updated:** 2026-04-25
**Runtime:** Pure Python, no API calls. Periodic (cron-style) data collection + on-demand reporting.

Module 7 closes the loop between what Module 6 + Module 6b *predicted* and what
the market *actually did*. It captures snapshots of every prediction at scoring
time, fills in forward prices on a schedule, classifies outcomes when each
horizon elapses, and produces calibration reports — including per-component
calibration of the 14 score-modifier components from D47/D52.

**Per D12, M7 is advisory only.** It surfaces empirical evidence — "factor 0.80
on `crowding 5+` looks too aggressive given N=12 historical samples" — but never
auto-updates `archetypes.yaml`, `scoring_modifier.yaml`, or M6 prompts. The user
retains editorial control. Auto-tuning weights from realised-return data risks
fitting noise; M7 surfaces the evidence and the user decides.

---

## Role and contract

**Reads.**
- `llm_scores.db` (M6 + M6b output) — `llm_scores`, `final_rankings`, `llm_runs`
- `data/prices.db` (M4b output) — historical OHLC for forward-price fills
- `context_packs.db` (M5 output) — pack metadata (archetype, industry) for grouping
- `config/module_7.yaml` *(new)* — windows, cadence, classification thresholds

**Writes.**
- `data/outcomes.db` *(new)* — three tables:
  - `predictions` — one row per `(ticker, scoring_date, horizon)`, snapshot of
    everything the prediction was based on, including `score_modifier_json` for
    per-component analysis
  - `forward_prices` — one row per `(ticker, scoring_date, weeks_offset)`,
    progressively filled by the periodic runner
  - `outcomes` — one row per `(ticker, scoring_date, horizon)`, computed once
    `scoring_date + horizon_weeks` has elapsed
- `Outputs/m7_calibration_<asof>.html` — calibration report (on-demand)
- `Outputs/m7_predictions_<quarter>.html` — per-prediction tracking dashboard
  (on-demand)

**Does not write.** `llm_scores.db`, `data/prices.db`, `context_packs.db`,
`scoring_modifier.yaml`, `archetypes.yaml`. Per D12, M7 emits **suggestions**
in the report, never edits configs.

---

## Where M7 runs in the pipeline

Three call sites, all decoupled from M6's API-cost path:

1. **Snapshot hook** — appended to `scripts/6_score.py` Step 10, *after*
   `apply_modifiers_to_run` writes the modifier columns. Each completed run
   emits one `predictions` row per `(ticker, horizon)` actually scored.
   Idempotent: re-running the same run_id replaces the prior snapshots.
2. **Periodic runner** — `scripts/7_track_outcomes.py`. Runs on a schedule
   (weekly default per Overall_spec; configurable). Walks `predictions` rows
   missing forward-price cells, fills from `data/prices.db` (or yfinance
   fallback), classifies outcomes when each horizon has elapsed.
3. **On-demand report generator** — `scripts/7_calibration_report.py`. Pure
   read; generates the HTML report from `outcomes.db`. No side effects.

The snapshot hook costs ~milliseconds per ticker; safe to always-on. The
periodic runner does no API calls (just `prices.db` lookups + occasional
yfinance backfills for missing tickers).

---

## Three-increment build plan

M7 is sized to ship in three increments. **The data must start compounding ASAP**
— without snapshots taken at scoring time, future calibration is impossible.

| Phase | Scope | Effort | Built when |
|---|---|---|---|
| **M7-α** | Snapshot hook + forward-price collection. NO reports yet. **Goal: start capturing the data so it compounds.** | ~half day | Build first; nothing in M7-β/γ is possible without this. |
| **M7-β** | Outcome classification + per-archetype + per-LLM-decile reports. Per-component reports stubbed. | ~1 day | After 1 quarter of M7-α data. |
| **M7-γ** | Per-component calibration with co-firing-aware regression + suggested YAML edits. Optional: per-component × archetype interaction grid. | ~1.5 days | After 2–4 quarters of M7-α data; meaningful when N per band ≥ 30. |

This spec covers **all three phases**. Implementation may pause between
increments — the schema is designed so M7-β and M7-γ add nothing to the
write side; they only read.

---

## Phase 1 (M7-α) — snapshot + forward price collection

### `predictions` schema

One row per `(ticker, scoring_date, horizon)`. **Replaced** on any re-snapshot
that targets the same key (per D57: `INSERT OR REPLACE`). This includes:
(a) `6b_apply_modifiers.py` rerunning the same `run_id`; (b) a fresh dispatch
on the same calendar day for an already-snapshotted ticker. "Latest dispatch
wins" semantics — readers needing the historical chain JOIN against
`llm_scores.score_modifier_json` (which is `run_id`-keyed and never overwritten).

```sql
CREATE TABLE predictions (
    -- identity
    ticker                                TEXT NOT NULL,
    scoring_date                          TEXT NOT NULL,    -- ISO date from llm_runs.started_at, day precision
    quarter                               TEXT NOT NULL,
    horizon                               TEXT NOT NULL,    -- '3mo' | '12mo'
    run_id                                INTEGER NOT NULL,
    prompt_version                        TEXT NOT NULL,
    model                                 TEXT NOT NULL,

    -- the prediction itself (per-horizon)
    current_price_usd                     REAL NOT NULL,
    target_price_usd                      REAL,
    time_to_catalyst_weeks                INTEGER,
    probability                           REAL,
    catalyst_type                         TEXT,
    catalyst_detail                       TEXT,

    -- the score that drove the ranking
    score_at_current_pct_per_month        REAL,             -- D46 raw
    score_modifier                        REAL,             -- D47/D52 composite (weights = 1.0)
    score_at_current_adjusted_pct_per_month REAL,
    score_modifier_json                   TEXT,             -- D47/D52 — full 14-component breakdown
                                                            -- KEY for per-component feedback (M7-γ)

    -- pack/M5 context for grouping
    archetype                             TEXT,
    sector                                TEXT,
    industry                              TEXT,
    fund_count                            INTEGER,
    market_cap_usd                        INTEGER,          -- basic, from M5 pack (audit only)

    -- bookkeeping
    snapshot_at                           TEXT NOT NULL,    -- when this row was written
    PRIMARY KEY (ticker, scoring_date, horizon)
);
CREATE INDEX idx_predictions_quarter_archetype  ON predictions(quarter, archetype);
CREATE INDEX idx_predictions_horizon_date       ON predictions(horizon, scoring_date);
```

**Why persist `score_modifier_json` verbatim?** The user can edit
`scoring_modifier.yaml` and re-run `6b_apply_modifiers.py` to retroactively
recompute every modifier. Without snapshotting, the historical record of
"what factors were applied to NTLA in 2025Q4" is overwritten. M7-γ's
per-component calibration is meaningless without the snapshot — it has to
ask "did F=0.80 on crowding actually predict −20% return correctly?" and
that needs the F=0.80 to be remembered as it was AT THE TIME of the
prediction.

### `forward_prices` schema

One row per `(ticker, scoring_date, weeks_offset)`. Filled progressively as
the periodic runner runs.

```sql
CREATE TABLE forward_prices (
    ticker                TEXT NOT NULL,
    scoring_date          TEXT NOT NULL,
    weeks_offset          INTEGER NOT NULL,             -- 1, 4, 12, 26, 52 (configurable)
    asof_date             TEXT,                         -- nearest trading day to scoring_date + weeks*7
    close_usd             REAL,                         -- close on asof_date
    high_usd_to_date      REAL,                         -- max(high) over [scoring_date, asof_date]
    return_pct            REAL,                         -- (close_usd - current_price_at_scoring) / current_price * 100
    return_per_month      REAL,                         -- return_pct / (weeks_offset / 4.33)
    delisted              INTEGER NOT NULL DEFAULT 0,   -- 1 if no price after a known delist date
    last_filled_at        TEXT NOT NULL,
    PRIMARY KEY (ticker, scoring_date, weeks_offset)
);
CREATE INDEX idx_fp_offset_date ON forward_prices(weeks_offset, asof_date);
```

**Forward window choices (default):** `weeks_offset ∈ {1, 4, 12, 26, 52}`.
Configurable via `module_7.yaml`.
- **+1w**: catches immediate post-catalyst moves (e.g., NTLA's 2026-04-27
  HAELO topline)
- **+4w**: short-term momentum
- **+12w (3mo)**: matches the M6 3mo horizon
- **+26w (~6mo)**: midpoint check
- **+52w (12mo)**: matches the M6 12mo horizon

**Survivorship handling.** When `prices.db` returns no row beyond a known
delist date, set `delisted=1`. M7 reports treat delisted tickers explicitly
in their own bucket — never silently dropped (which would inflate realized
returns).

### Snapshot algorithm (`src/module_7/snapshot.py`)

```python
def snapshot_run(scores_conn, run_id: int) -> int:
    """Append one predictions row per (ticker, horizon) for this run.
    Replaces prior snapshots for the same (ticker, scoring_date, horizon).
    Returns count of rows written. Idempotent.
    """
```

Called from `scripts/6_score.py` Step 10 right before `close_run`. Reads
from `llm_scores` (which already has the modifier columns populated by
`apply_modifiers_to_run`) and writes to `outcomes.db.predictions`. Uses
`llm_runs.started_at` for `scoring_date` (one date per run, not per ticker).

### Forward-price collection (`scripts/7_track_outcomes.py`)

```python
def collect_forward_prices(outcomes_conn, prices_conn, *, asof_date=None) -> dict:
    """For every predictions row missing forward_prices cells, fill those
    cells whose asof_date <= now. Idempotent — only writes missing cells.
    Returns counts: {filled: int, missing_in_prices: int, delisted: int}.
    """
```

Logic per `(ticker, scoring_date, weeks_offset)`:
1. Compute `target_asof = scoring_date + weeks_offset × 7 days`
2. If `target_asof > today`, skip (not yet)
3. Look up the closest trading-day close in `prices.db` (forward search up
   to 5 days for weekends/holidays)
4. If found: write the row with `close_usd`, `high_usd_to_date` (max over
   the window), compute returns
5. If not found and ticker last seen > 90 days ago: mark `delisted=1`
6. If not found and ticker still active: do not write (try again next run)

**Cadence.** Default weekly (run via Task Scheduler / cron). User can run
daily for tighter tracking on near-term catalysts. Idempotency makes the
cadence harmless to over-run.

---

## Phase 2 (M7-β) — outcome classification + basic reports

### `outcomes` schema

One row per `(ticker, scoring_date, horizon)`. Computed when
`scoring_date + horizon_weeks` has elapsed AND the matching `forward_prices`
row is filled.

```sql
CREATE TABLE outcomes (
    ticker                TEXT NOT NULL,
    scoring_date          TEXT NOT NULL,
    horizon               TEXT NOT NULL,                -- '3mo' | '12mo'
    horizon_asof_date     TEXT NOT NULL,                -- scoring_date + (12 or 52)w

    -- realised vs predicted
    actual_close_usd                  REAL,
    actual_return_pct                 REAL,             -- (actual - current) / current * 100
    actual_return_per_month           REAL,             -- actual_return_pct / horizon_months
    actual_max_close_usd_in_window    REAL,             -- max close over [scoring, horizon_asof]
    target_appreciation_pct           REAL,             -- (target - current) / current * 100
    realised_vs_target_ratio          REAL,             -- actual_return_pct / target_appreciation_pct

    -- classification
    outcome_class                     TEXT,             -- 'target_hit' | 'partial_move' | 'touched_then_faded'
                                                        -- | 'flat' | 'loss' | 'delisted' | 'data_missing'
    classified_at                     TEXT NOT NULL,
    PRIMARY KEY (ticker, scoring_date, horizon)
);
CREATE INDEX idx_outcomes_class       ON outcomes(outcome_class);
CREATE INDEX idx_outcomes_horizon     ON outcomes(horizon);
```

### Outcome classification

Tunable in `module_7.yaml`. Defaults:

```python
def classify(pred: PredictionRow, fp: ForwardPriceRow) -> str:
    if fp is None or fp.close_usd is None:
        return "data_missing"
    if fp.delisted:
        return "delisted"
    target_appr = (pred.target_price_usd - pred.current_price_usd) / pred.current_price_usd
    actual_appr = (fp.close_usd - pred.current_price_usd) / pred.current_price_usd
    max_appr   = (fp.high_usd_to_date - pred.current_price_usd) / pred.current_price_usd

    if actual_appr >= 0.95 * target_appr:        return "target_hit"
    if actual_appr >= 0.50 * target_appr:        return "partial_move"
    if max_appr    >= 0.80 * target_appr and actual_appr < 0.50 * target_appr:
                                                  return "touched_then_faded"
    if actual_appr >= -0.05:                      return "flat"
    return "loss"
```

`touched_then_faded` is the catch for "we were right about direction +
magnitude but got the timing wrong / market round-tripped" — useful signal
that catalysts hit but didn't sustain.

### M7-β reports

`Outputs/m7_calibration_<asof>.html` — single HTML page with three sections:

1. **Per-archetype performance** — for each archetype × horizon (3mo, 12mo):
   N predictions reached horizon, mean realised return per month, mean
   target appreciation per month, calibration ratio (mean realised / mean
   target), outcome-class breakdown.
2. **Per-LLM-score-decile calibration** — sort all reached-horizon
   predictions by `score_at_current_adjusted_pct_per_month`, bucket into
   deciles, show realised return per bucket. Top-decile vs bottom-decile
   realised return is the headline number.
3. **Per-quarter rolling table** — quarter-by-quarter scoreboard, for
   tracking systemic regression.

No per-modifier-component calibration in M7-β yet (that's M7-γ).

---

## Phase 3 (M7-γ) — per-component calibration

This is the part that closes the modifier feedback loop. Hardest part of
M7. **Only meaningful with N ≥ ~30 predictions per component band.**

### The math

For each modifier component **C** (one of 14), each prediction has a per-row
factor F_C ∈ {discrete band values, e.g. 0.80, 0.90, 0.95, 1.00 for crowding}.
The component's *implied claim* is:

> A ticker scored with F_C = X is X-times-as-likely to hit its target as a
> ticker scored with F_C = 1.00, all else equal.

Equivalently: the realised return ratio between two factor bands should
approximate the factor ratio.

#### Naïve per-component calibration (M7-γ v1)

Group predictions by F_C value, compute mean realised return per bucket,
compare to the "implied" return scaling:

```
For each component C, factor band F:
  N        = count of predictions where score_modifier_json.components.C.factor == F
             AND outcome row exists (horizon reached)
  R(F)     = mean(actual_return_per_month) over those N rows
  R_base   = R(F=1.00) as the baseline reference
  ratio    = R(F) / R_base                          # observed return ratio
  delta    = ratio - F                              # calibration error
```

Verdict thresholds:
- `|delta| < 0.05` AND `N >= 30`: **calibrated** — no change
- `|delta| < 0.10` AND `N >= 30`: **mild miscalibration** — flag for monitoring
- `|delta| >= 0.10` AND `N >= 50`: **suggested edit** — propose new F value
  in the report
- `N < 30`: **insufficient data** — show but don't suggest

Confidence intervals via bootstrap (1000 resamples) on R(F). If the 95% CI
on `ratio` includes F, treat as calibrated regardless of point estimate.

#### Co-firing problem and the regression refinement (M7-γ v2)

Naïve per-component analysis is biased because components co-fire. A ticker
with `crowding = 0.80` AND `failures = 0.90` will underperform a baseline
ticker for both reasons; attributing the underperformance entirely to
`crowding` overstates that component's effect.

Mitigation: multi-variable OLS regression with each component's factor as a
feature:

```
actual_return_per_month ~ β_0
                        + β_crowding   × (F_crowding - 1.0)
                        + β_financing  × (F_financing - 1.0)
                        + ... (one term per component) ...
                        + β_archetype_X × archetype_indicator(X)
                        + ε
```

For each component C:
- **If β_C ≈ R_base:** factor map is calibrated (deviation from 1.0 in F_C
  produces proportional deviation in expected return)
- **If |β_C| > R_base:** factor too lenient — penalty/boost should be larger
- **If |β_C| < R_base:** factor too aggressive — penalty/boost should be
  smaller
- **If β_C statistically not different from 0:** component has no
  predictive power; consider disabling or merging with another

**Sample-size requirements:** OLS with ~14 component features + ~18
archetype features needs N ≥ 200 to be stable. So: realistic timeline for
M7-γ v2 is 4–6 quarters of M6 data, or about 1 year of running.

### M7-γ report sections (additions to M7-β)

4. **Per-component calibration table** (one row per `(component, factor band)`):

   | Component | Factor | Band | N | Mean realised /mo | Implied ratio (= F) | Realised ratio | Δ | Verdict | Suggested edit |
   |---|---:|---|---:|---:|---:|---:|---:|---|---|
   | crowding | 1.00 | 0 competitors | 8 | +2.1% | 1.00 (baseline) | — | — | — | — |
   | crowding | 0.95 | 1–2 competitors | 14 | +1.8% | 0.95 | 0.86 | −0.09 | mild | 0.93 |
   | crowding | 0.90 | 3–4 competitors | 9 | +0.6% | 0.90 | 0.29 | −0.61 | **MISCALIBRATED** (insufficient N) | (need N ≥ 50) |
   | crowding | 0.80 | 5+ competitors | 5 | −0.4% | 0.80 | -0.19 | -0.99 | (N too small) | — |

5. **OLS regression diagnostic** — table of `(β_C, std_err, p-value, suggested factor revision)` for each component. Only shown when the regression has converged with N ≥ 200.

6. **Suggested YAML edits** — copy-pasteable diff snippets at the bottom of
   the report. Per D12, **the YAML is not auto-modified.** User reviews,
   edits, runs `6b_apply_modifiers.py`, observes effect.

---

## `config/module_7.yaml` (new)

```yaml
# Module 7 — outcome tracking + calibration configuration.
# Spec: spec/module_7_spec.md
# Decisions: spec/decisions.md § D53.

# Forward-price windows (weeks). Filled by scripts/7_track_outcomes.py.
forward_windows_weeks: [1, 4, 12, 26, 52]

# Trading-day search window when looking up the closest price.
price_lookup_max_forward_days: 5

# Cadence for the periodic runner. Informational; actual scheduling lives
# in Task Scheduler / cron / run_2_Funds_parser.bat.
collection_cadence: weekly

# Outcome classification thresholds (multiplied against target_appreciation).
outcome_thresholds:
  target_hit_min_ratio:        0.95     # actual_appr >= 0.95 × target_appr
  partial_move_min_ratio:      0.50
  touched_min_max_ratio:       0.80     # max_appr >= 0.80 × target_appr
  flat_min_appr:              -0.05

# Per-component calibration thresholds.
calibration:
  min_n_for_verdict:          30        # N for "calibrated" verdict
  min_n_for_suggestion:       50        # N for "suggested edit" verdict
  bootstrap_iterations:       1000
  ci_confidence:              0.95      # 95% bootstrap CI
  delta_calibrated:           0.05      # |delta| < this = calibrated
  delta_mild:                 0.10      # |delta| < this = mild miscalibration

# OLS regression (M7-γ v2).
regression:
  min_n:                      200
  include_archetype_indicators: true
  include_industry_indicators:  false   # too sparse to be meaningful at v1

# Survivorship: how many days without a price before flagging delisted.
delist_grace_days:            90

# Outputs
report_path_pattern:          "Outputs/m7_calibration_{asof_date}.html"
predictions_dashboard_pattern: "Outputs/m7_predictions_{quarter}.html"
```

---

## File map (planned)

```
2_Funds_parser/
├── data/
│   └── outcomes.db                    ← NEW (M7-α)
├── config/
│   └── module_7.yaml                  ← NEW (M7-α)
├── spec/
│   └── module_7_spec.md               ← THIS FILE
├── src/
│   └── module_7/                      ← NEW
│       ├── __init__.py
│       ├── outcomes_db.py             ← schema + read helpers (M7-α)
│       ├── snapshot.py                ← snapshot_run() (M7-α)
│       ├── collect.py                 ← collect_forward_prices() (M7-α)
│       ├── classify.py                ← outcome classification (M7-β)
│       ├── calibration.py             ← per-component math + regression (M7-γ)
│       └── reports.py                 ← HTML report generation (M7-β + γ)
├── scripts/
│   ├── 7_track_outcomes.py            ← cron-style runner (M7-α + β)
│   └── 7_calibration_report.py        ← on-demand report (M7-β + γ)
└── (modified)
    └── scripts/6_score.py             ← appended snapshot_run() at end of step 10
```

---

## Phase ordering — what to build when

### M7-α (build now)

1. `data/outcomes.db` schema init (`src/module_7/outcomes_db.py`)
2. `src/module_7/snapshot.py::snapshot_run(conn, run_id)`
3. Hook into `scripts/6_score.py` Step 10:
   ```python
   from module_7 import snapshot_run
   snapshot_run(scores_conn, run_id=run_id, outcomes_db=outcomes_db_path)
   ```
   Same for `scripts/6b_apply_modifiers.py` and `scripts/6_recompute_scores.py`
   so any modifier change re-snapshots.
4. `src/module_7/collect.py::collect_forward_prices(...)`
5. `scripts/7_track_outcomes.py` — CLI: `--asof <date>`, `--quarter <q>`,
   `--dry-run`, `-v`. By default, fills all eligible cells.
6. **Backfill** existing run-2/3/4/7/8/9 predictions retroactively (no
   API cost; just walks `llm_scores`).
7. Add a step to `run_2_Funds_parser.bat` that calls
   `scripts/7_track_outcomes.py` after M6 / on weekly cron.

**Acceptance: M7-α is done when `outcomes.db.predictions` has one row per
(ticker, run, horizon) for runs 2–9, and `forward_prices` has +1w / +4w
cells for any prediction old enough.**

### M7-β (build after 1 quarter of α data)

1. `src/module_7/classify.py` — implements outcome classification per
   `module_7.yaml` thresholds.
2. Extend `scripts/7_track_outcomes.py` to also classify outcomes when
   `scoring_date + horizon_weeks` has elapsed AND `forward_prices` row is
   filled. Persist to `outcomes` table.
3. `src/module_7/reports.py` — implements:
   - Section 1: per-archetype × horizon table
   - Section 2: per-decile calibration scatter (matplotlib via openpyxl
     image embed; or pure HTML/SVG)
   - Section 3: per-quarter rolling scoreboard
4. `scripts/7_calibration_report.py` — generates the HTML.

**Acceptance: M7-β is done when running `7_calibration_report.py` produces
an HTML with sections 1–3, and the per-archetype table shows realised
return ratios for at least 3 archetypes with N ≥ 5.**

### M7-γ (build after 2–4 quarters of α data)

1. `src/module_7/calibration.py` — implements:
   - Naïve per-(component, band) tabulation
   - Bootstrap CIs on the realised ratio
   - Verdict assignment per `module_7.yaml` thresholds
   - Suggested-edit YAML diff generation
2. (v2, after sufficient data) Add OLS regression with archetype indicators
   using `numpy` / `statsmodels`.
3. Extend `7_calibration_report.py` to add Sections 4–6.

**Acceptance: M7-γ is done when the report's per-component table shows
verdicts for all 14 components, with at least 5 components having N ≥ 30
in their non-baseline bands.**

---

## Acceptance tests (per phase)

| # | Phase | Test | Expected |
|---|---|---|---|
| 1 | α | `snapshot_run(conn, run_id=4)` writes 4 rows (NTLA + TCRX × 2 horizons). | `SELECT COUNT(*) FROM predictions WHERE run_id=4` returns 4 |
| 2 | α | Re-running `snapshot_run(conn, run_id=4)` does not duplicate. | Row count unchanged |
| 3 | α | `collect_forward_prices(asof=2026-04-30)` for a 2026-04-25 prediction fills the +4w cell with the 2026-05-23 close. | `forward_prices` row populated |
| 4 | α | Ticker delisted before the +12w mark sets `delisted=1`. | Row written; outcome_class = `delisted` |
| 5 | β | A prediction whose actual_return >= 0.95 × target_return at horizon classifies as `target_hit`. | `outcomes.outcome_class = 'target_hit'` |
| 6 | β | Per-archetype report shows mean realised return per archetype × horizon. | Table renders with N per cell |
| 7 | γ | Per-component table shows N per (component, band) and the realised ratio. | Renders for all 14 components |
| 8 | γ | "Suggested edit" appears only for components with N ≥ 50 and `|delta| ≥ 0.10`. | Row in "Suggested YAML edits" section |
| 9 | γ | OLS regression (when N ≥ 200) emits β_C with std_err and p-value per component. | Diagnostic table renders |
| 10 | γ | Disabled component (`enabled: false` in YAML) is excluded from regression + table. | Component absent from report |

---

## Open items before / during implementation

1. **Trading calendar.** `prices.db` should align to trading days; weekends
   and holidays need handling. Default: forward-search up to 5 trading
   days from `scoring_date + weeks_offset × 7`. **Question**: should we
   also use the *closest* trading day (forward OR backward) when the
   target falls on a holiday? Default: forward only (more conservative —
   never reports return that hadn't been realised yet).

2. **Cross-quarter overlap.** A ticker scored in 2025Q4 and again in
   2026Q1 has two `predictions` rows. Forward windows overlap. M7 keeps
   them separate (each is its own observation). Per-component calibration
   treats them as independent samples — slight survivorship bias if the
   same ticker is included multiple times. **Question**: deduplicate per
   ticker per horizon period? **v1 answer**: no — multiple scorings of the
   same ticker carry independent prediction-quality signal.

3. **Score-modifier-json schema migrations.** The snapshot stores the JSON
   verbatim. If D52's component list changes (add/rename), older snapshots
   become harder to parse. **v1 answer**: use a `score_modifier_json`
   schema_version field in the JSON itself; M7-γ regression skips
   components whose schema version doesn't match the current YAML.

4. **Confidence intervals.** Bootstrap default is 1000 resamples — fast for
   N < 1000. For larger samples, may need parallelisation. **v1 answer**:
   keep at 1000; revisit if reports become slow.

5. **Survivorship via failed acquisitions.** If a ticker is acquired at
   $X above the bid before horizon, that's a *target_hit* outcome — but
   `prices.db` may show no post-acquisition price. **v1 answer**: treat
   acquisition-driven delisting as `target_hit` if the last close was
   ≥ 0.80 × target. Add a `delist_reason` field in M7-β.

6. **The outcomes DB grows monotonically.** ~98 tickers/quarter × 2
   horizons × 5 windows = ~1000 forward_prices rows per quarter +
   ~196 prediction rows. Negligible storage. No retention policy needed
   short of "ever".

7. **Cross-component correlation in the regression.** Some component
   factors are correlated (e.g., `mgmt` and `failures` often co-fire on
   the same struggling ticker). High collinearity inflates std_errs.
   **v1 answer**: use VIF (variance inflation factor) reporting in the
   regression diagnostic; flag components with VIF > 10 as
   "factor-correlated" and treat the OLS coefficient as suggestive only.

8. **Rolling vs cumulative.** Reports default to cumulative-since-inception.
   Option to filter to last N quarters lives behind a CLI flag.

---

## Decisions log entries

> **D53 — Module 7 outcome tracking + per-component calibration feedback loop**
> Spec: [module_7_spec.md](module_7_spec.md). Three-phase build (M7-α
> snapshot+collect / M7-β classify+basic-report / M7-γ per-component
> calibration). New `data/outcomes.db` with three tables (`predictions`,
> `forward_prices`, `outcomes`). Snapshot hook in `scripts/6_score.py`
> Step 10 + `6b_apply_modifiers.py` + `6_recompute_scores.py`. Periodic
> runner `scripts/7_track_outcomes.py`. On-demand report generator
> `scripts/7_calibration_report.py`. Per D12, M7 is **advisory only** —
> emits suggested YAML edits in the report; never auto-modifies
> `scoring_modifier.yaml` or `archetypes.yaml`. Per-component calibration
> uses naïve grouping (M7-γ v1) then OLS regression with archetype
> indicators (M7-γ v2, requires N ≥ 200). Bootstrap CIs gate verdicts;
> `score_modifier_json` snapshot is the canonical record of "what
> components fired with what factors AT THE TIME of the prediction"
> (essential because YAML edits can retroactively change current
> `llm_scores.score_modifier_json`).

---

## Why this design

- **Snapshot first, report later.** Without snapshots starting NOW, future
  reports have nothing to chew on. The 1-day cost of M7-α is the price of
  admission for any future calibration work.
- **Decoupled phases.** M7-β and M7-γ add only read-side code. The schema
  doesn't change between phases. This means M7-α can ship months before
  M7-β/γ are even started, with no rework.
- **Persisted modifier snapshots solve the temporal problem.** YAML edits
  + `6b_apply_modifiers.py` reruns can retroactively change
  `llm_scores.score_modifier_json`. Without M7's snapshot, you'd lose the
  audit trail of what was actually applied at the time.
- **Per-component analysis honestly handles co-firing.** Naïve grouping in
  v1 is intentionally simple but flagged as biased; OLS in v2 disentangles
  components when the data supports it. The user sees both.
- **Advisory only (D12).** Auto-tuning weights from realised data risks
  fitting noise from a small sample. The user sees evidence + suggestions;
  retains editorial control. This is also the difference between a useful
  tool and a closed-loop system that can drift in unobservable ways.

---

## Cross-cutting rules (do not re-litigate)

1. **No API calls.** M7 reads `prices.db` (M4b) and falls back to yfinance
   only for missing tickers (within yfinance's free-tier rate limits).
2. **Quarter format `YYYYQn`** — derived from `predictions.scoring_date`.
3. **Output folder convention.** `Outputs/m7_*.html` are user-facing.
   `data/outcomes.db` is durable cross-run state.
4. **`*.db` is gitignored.** `outcomes.db` is no exception.
5. **Schema evolves additively.** Future columns added via
   `_apply_additive_migrations` mirroring M6's pattern.
6. **D12** — advisory only; M7 never edits configs.
7. **D46/D47/D52** — M7 reads everything those decisions wrote; doesn't
   alter the score formula, the modifier components, or the slider UI.
