# Module 4 — "Train Hasn't Left" Filter & Ranking

**Status:** Locked specification
**Last updated:** 2026-04-22

Module 4 is split into two sub-modules that run sequentially:

- **Module 4a — Hard Filters.** Snapshot-only binary cuts. Eliminates unfit tickers before any expensive computation.
- **Module 4b — Price History, Ratio Matrix, Archetype Ranking.** Fetches price history only for survivors, computes the ratio matrix, matches against archetypes, produces the ranked candidate list for Module 5.

The split exists so that expensive price-history fetching targets only survivors, and so that Module 4a can be re-run cheaply when filter thresholds change.

---

## Module 4a — Hard Filters

### Purpose

Eliminate tickers unfit for further analysis using snapshot data only. No price history, no LLM, no expensive computation. Output is a survivors list that Module 4b uses to trigger targeted price-history fetching.

### Inputs

- **`enriched_snapshot.parquet`** (from Module 3): one row per ticker. Columns: `ticker`, `price_today`, `market_cap`, `shares_outstanding`, `prefunded_warrants`, `adv_30d`, `sector`, `industry`, `ipo_date`, `pct_from_52w_low`, `pct_from_52w_high`, `weeks_of_history_available`.
- **`fund_positions_aggregated.parquet`** (from Module 2): columns: `ticker`, `fund_count`, `qoq_share_change`, `qoq_fund_count_change`, `new_positions`, `increased_positions`.
- **`config/filters.yaml`**: thresholds (schema below).

### Outputs

- **`_intermediate_outputs/survivors_{quarter}.parquet`**: tickers that passed all filters. Columns: all enrichment columns + all fund position columns.
- **`_intermediate_outputs/hard_filter_rejections_{quarter}.parquet`**: one row per rejected ticker. Columns: `ticker`, `rejection_reason` (first-failing filter name), `rejection_value` (the actual value that failed).
- **`_outputs/filter_summary_{quarter}.html`**: funnel visualization + rejection breakdown table.

### Config schema

```yaml
# config/filters.yaml
hard_filters:
  market_cap_min_usd: 50_000_000
  market_cap_max_usd: 3_700_000_000
  adv_30d_min_usd: 250_000
  fund_count_min: 1
  require_min_price_usd: 0.90
  require_min_history_weeks: 12
  sector_allowlist: null              # null = all; or ["Healthcare", "Biotechnology"]
  sector_blocklist: null              # null = none; or ["Financial Services"]
  exclude_pink_sheet: true
  exclude_if_recent_reverse_split: false    # placeholder, requires corp actions data
```

All thresholds exposed and tunable. Defaults are starting points.

### Algorithm

Left-to-right evaluation with ordinal priority. First failure determines `rejection_reason`, so logs are interpretable.

Priority order:

1. `require_min_price_usd`
2. `market_cap_min_usd` / `market_cap_max_usd`
3. `adv_30d_min_usd`
4. `require_min_history_weeks`
5. `sector_allowlist` / `sector_blocklist`
6. `fund_count_min`
7. `exclude_pink_sheet`

### Function signatures

```python
# module_4a_hard_filters.py

def apply_filter(
    ticker_row: pd.Series,
    filters_config: dict,
) -> tuple[bool, str | None, float | None]:
    """Return (passed, rejection_reason, rejection_value)."""

def run_hard_filters(
    enriched_snapshot_path: str,
    fund_positions_path: str,
    filters_config_path: str,
    output_dir: str,
    quarter: str,
    debug: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (survivors_df, rejections_df). Writes both to Parquet."""

def generate_filter_summary_html(
    rejections_df: pd.DataFrame,
    survivors_count: int,
    total_count: int,
    output_path: str,
) -> None: ...
```

### Failure modes

- **Missing field in snapshot row**: treat as filter failure, `rejection_reason = "missing_{field_name}"`.
- **Fund positions missing for ticker**: set `fund_count = 0`, proceed (will fail `fund_count_min` naturally).
- **Config YAML malformed**: validate at load, fail loudly with line number.

### Caching

None needed. Pure computation on snapshot inputs; re-runs in seconds.

### Acceptance tests

1. Ticker with `market_cap = $10M` → rejected with `rejection_reason = "market_cap_min_usd"`.
2. Ticker with all fields valid → passes, appears in survivors.
3. Filter-only run with modified YAML → output changes deterministically.
4. Rejection counts in summary HTML sum to `total_count - survivors_count`.

---

## Module 4b — Price History, Ratio Matrix, Archetype Ranking

### Purpose

For survivor tickers only: fetch and maintain daily price history in SQLite, compute the ratio matrix, match against archetypes, produce the ranked candidate list for Module 5.

### Inputs

- **`_intermediate_outputs/survivors_{quarter}.parquet`** (from Module 4a).
- **`prices.db`** (SQLite, persistent across runs).
- **`config/archetypes.yaml`**: archetype definitions with ranges and scores.
- **`config/ranking.yaml`**: min_confidence, tiebreakers, output behavior.

### Outputs

- **`prices.db`** updated with latest price history for all survivors.
- **`_intermediate_outputs/ranked_candidates_{quarter}.parquet`**: full ranked universe (survivors only). Columns:
  - `ticker`, `rank`, `archetype`, `archetype_score`, `match_confidence`, `composite_score`
  - `R_4`, `R_12`, `R_26`, `R_52`
  - `R_4_over_R_12`, `R_4_over_R_26`, `R_4_over_R_52`, `R_12_over_R_26`, `R_12_over_R_52`, `R_26_over_R_52`
  - `price_today`, `price_source_date_4w`, `price_source_date_12w`, `price_source_date_26w`, `price_source_date_52w` (audit trail)
  - `young_ticker_flag`, `weeks_of_history_used`
  - All passthrough columns from survivors (market_cap, fund_count, etc.) for Module 5 and the report
- **`_outputs/ranking_report_{quarter}.html`**: sortable table, archetype color-coded, matrix values per row.
- **`_outputs/ranking_report_{quarter}.xlsx`**: Excel with conditional formatting on `composite_score`.

### SQLite schema

```sql
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,                 -- ISO date YYYY-MM-DD
    close REAL NOT NULL,
    adjusted_close REAL NOT NULL,       -- split/dividend adjusted; used for all ratios
    volume INTEGER,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON prices(ticker, date DESC);

CREATE TABLE IF NOT EXISTS price_fetch_log (
    ticker TEXT PRIMARY KEY,
    first_date TEXT,
    last_date TEXT,
    last_fetched_at TEXT,               -- ISO datetime
    source TEXT DEFAULT 'yfinance',
    fetch_status TEXT,                  -- 'ok', 'partial', 'failed'
    fetch_error TEXT                    -- null or error message
);
```

### Algorithm

#### Step 1 — Incremental price fetch

For each ticker in survivors:

1. Query `price_fetch_log` for `last_date`.
2. If no row exists, fetch full ~1.5 years of history (400 trading days) — enough for 52w ratios plus buffer.
3. If row exists, fetch from `last_date + 1` to today.
4. If `last_fetched_at` is today already, skip (idempotent same-day re-runs).
5. Upsert into `prices` table. Update `price_fetch_log`.
6. On yfinance failure: log error, set `fetch_status='failed'`, continue. Ticker proceeds with existing data; if insufficient, hits young_ticker handling.

Batch yfinance calls in groups of 50 tickers. Retry failed tickers once with exponential backoff. After second failure, skip and flag.

#### Step 2 — Compute ratio matrix

For each ticker, pull last 400 trading days from SQLite. Use `adjusted_close` for all computations.

```
R_4  = price_today / price_on_date_closest_to(today - 4w)
R_12 = price_today / price_on_date_closest_to(today - 12w)
R_26 = price_today / price_on_date_closest_to(today - 26w)
R_52 = price_today / price_on_date_closest_to(today - 52w)
```

"Closest to" uses ±3 trading-day tolerance. If window predates first available price: set `R_Xw = 1.0`, set `young_ticker_flag = True`.

Record source dates for each window in output for audit.

Then compute the 6 inter-window ratios:

```
R_4_over_R_12  = R_4  / R_12
R_4_over_R_26  = R_4  / R_26
R_4_over_R_52  = R_4  / R_52
R_12_over_R_26 = R_12 / R_26
R_12_over_R_52 = R_12 / R_52
R_26_over_R_52 = R_26 / R_52
```

All 10 features stored per ticker.

#### Step 3 — Archetype matching

Load `config/archetypes.yaml`. For each ticker:

```python
def match(features, archetype):
    specified = archetype['ranges']
    matches = sum(
        1 for key, (lo, hi) in specified.items()
        if lo <= features[key] <= hi
    )
    confidence = matches / len(specified)
    return confidence
```

Each archetype only specifies the ranges it cares about. Tickers are not penalized for unspecified dimensions.

Among archetypes where `confidence >= ranking_config.min_confidence` (default 0.70), the ticker is assigned the one with the highest confidence. Ties broken by highest `|archetype_score|` (most opinionated archetype wins).

If no archetype qualifies: `archetype = "unclassified"`, `archetype_score = 0`, `confidence = 0`.

#### Step 4 — Composite score

```
composite_score = archetype_score × match_confidence
```

Pure trajectory-based ranking. Fund accumulation, compression, distance-to-52w-low, etc. are preserved in the output for human inspection and for Module 5's LLM context, but do not enter the score (decision logged in decisions.md).

#### Step 5 — Rank and write outputs

Sort by `composite_score` descending. Tiebreakers: `fund_count` descending, then `ticker` alphabetical (deterministic output).

Assign `rank` column (1-indexed). Write Parquet. Generate HTML and Excel reports.

### Config schemas

```yaml
# config/archetypes.yaml

archetypes:
  fresh_awakening:
    score: 10
    description: "Flat-to-down over year, awakening in recent 12 weeks"
    ranges:
      R_52:           [0.85, 1.10]
      R_26:           [0.90, 1.10]
      R_12:           [1.05, 1.30]
      R_4:            [1.02, 1.15]
      R_12_over_R_26: [1.05, 1.40]
      R_26_over_R_52: [0.95, 1.15]

  quiet_compression:
    score: 8
    description: "Flat across all windows, coiled spring"
    ranges:
      R_52: [0.90, 1.10]
      R_26: [0.92, 1.08]
      R_12: [0.94, 1.06]
      R_4:  [0.96, 1.04]

  post_crash_rebase:
    score: 6
    description: "Down sharply over year, bottomed and stabilizing"
    ranges:
      R_52:           [0.40, 0.75]
      R_26:           [0.85, 1.10]
      R_12:           [0.95, 1.10]
      R_4:            [0.98, 1.08]
      R_26_over_R_52: [1.15, 2.00]

  early_breakout:
    score: 7
    description: "Recent move starting, not yet parabolic"
    ranges:
      R_52:          [0.90, 1.15]
      R_12:          [1.10, 1.35]
      R_4:           [1.05, 1.20]
      R_4_over_R_12: [0.80, 1.10]

  mature_uptrend:
    score: 2
    description: "Sustained uptrend across all windows"
    ranges:
      R_52: [1.20, 2.50]
      R_26: [1.10, 1.80]
      R_12: [1.00, 1.30]
      R_4:  [0.98, 1.10]

  parabolic_blowoff:
    score: -10
    description: "Train has left — recent move extreme"
    ranges:
      R_4:           [1.25, 999]
      R_4_over_R_12: [1.30, 999]
      R_4_over_R_52: [1.15, 999]

  broken_trend:
    score: -5
    description: "Uptrend broken, recent weakness"
    ranges:
      R_52:           [1.10, 999]
      R_12:           [0.70, 0.95]
      R_4:            [0.85, 0.98]
      R_12_over_R_26: [0.60, 0.95]

  sustained_decline:
    score: -3
    description: "Falling across all windows — value trap risk"
    ranges:
      R_52: [0.001, 0.80]
      R_26: [0.001, 0.90]
      R_12: [0.001, 0.95]
      R_4:  [0.001, 0.98]
```

```yaml
# config/ranking.yaml
ranking:
  min_confidence: 0.70
  include_unclassified: true          # keep at bottom; if false, drop
  window_tolerance_trading_days: 3
  young_ticker_flat_fill: true        # false = exclude young tickers
  output_top_n: null                  # null = all survivors; or integer to cap
  tiebreakers:                        # applied in order after composite_score
    - fund_count_desc
    - ticker_asc
```

### Function signatures

```python
# module_4b_ratios_archetypes.py

def fetch_incremental_prices(
    tickers: list[str],
    db_path: str,
    source: str = "yfinance",
    batch_size: int = 50,
) -> dict[str, str]:
    """Returns dict of ticker -> fetch_status."""

def get_price_on_date(
    conn: sqlite3.Connection,
    ticker: str,
    target_date: pd.Timestamp,
    tolerance_trading_days: int = 3,
) -> tuple[float | None, pd.Timestamp | None]:
    """Returns (price, actual_date) or (None, None) if outside tolerance."""

def compute_ratios_for_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    reference_date: pd.Timestamp,
    flat_fill: bool = True,
) -> dict:
    """Returns dict with R_4, R_12, R_26, R_52, all 6 inter-window ratios,
       source dates, young_ticker_flag, weeks_of_history_used."""

def match_archetypes(
    features: dict[str, float],
    archetypes_config: dict,
    min_confidence: float = 0.70,
) -> tuple[str, float, float]:
    """Returns (archetype_name, archetype_score, match_confidence)."""

def rank_universe(
    survivors_path: str,
    prices_db_path: str,
    archetypes_config_path: str,
    ranking_config_path: str,
    output_dir: str,
    quarter: str,
    debug: bool = False,
) -> pd.DataFrame:
    """End-to-end orchestrator. Updates prices.db, computes ratios,
       matches archetypes, writes ranked output and reports."""
```

### Calibration tooling

Utility scripts in `module_4_tools/` (not production pipeline):

- **`calibrate.py`**: takes `calibration.yaml` with reference tickers and expected archetypes, outputs agreement table showing actual vs. expected classification.
- **`sensitivity.py --ticker TCRX --archetype fresh_awakening`**: shows per-range pass/fail with margins (how close each feature is to each range boundary).
- **`distribution.py`**: plots histograms of each of the 10 features across the surviving universe, overlays archetype range boundaries.

`calibration.yaml` schema:

```yaml
reference_tickers:
  - ticker: TCRX
    expected_archetype: quiet_compression
    as_of_date: 2024-08-15
    notes: "Canonical compression reference"
  - ticker: BCYC
    expected_archetype: fresh_awakening
    as_of_date: 2024-09-01
  - ticker: MRNA
    expected_archetype: parabolic_blowoff
    as_of_date: 2021-08-10
```

### Re-ranking on config change

Key operational requirement: user edits `archetypes.yaml` or `ranking.yaml`, re-runs Module 4b with `--rerank-only`, gets updated ranking within seconds (no price fetch). Module 5's cached LLM responses remain valid (keyed by ticker + quarter, independent of ranking). User then decides whether to trigger Module 5 on the new top-N.

CLI pattern:

```
python module_4a_hard_filters.py --quarter 2026Q1
python module_4b_ratios_archetypes.py --quarter 2026Q1
python module_4b_ratios_archetypes.py --quarter 2026Q1 --rerank-only
python module_5_llm_score.py --quarter 2026Q1 --top-n 150
```

### Caching

- SQLite `prices.db` is the durable cache.
- Ratio computations fast; not cached.
- `last_fetched_at` check prevents duplicate same-day fetches.

### Failure modes

- **yfinance returns empty**: retry once, then flag ticker with `fetch_status='failed'`. If sufficient history already in DB, proceed with cached data. Otherwise exclude from ranking.
- **Price gap in middle of history**: use closest available within tolerance; if none, flag window as missing.
- **Archetype YAML malformed**: validate at load, fail loudly with line number.
- **No survivors input**: empty output files, warning logged, pipeline doesn't crash.
- **All archetypes yield confidence < min_confidence for a ticker**: assign `unclassified`, retain in output at bottom (configurable).

### Acceptance tests

1. First run with empty `prices.db` → full 400-day history fetched for all survivors.
2. Second run same day → no fetches, uses cached data.
3. Second run next day → one-day delta fetch per ticker.
4. Synthetic ticker with fabricated prices matching `fresh_awakening` exactly → confidence = 1.0.
5. Ranking is deterministic: two back-to-back runs produce identical Parquet.
6. Calibration tickers classified per `calibration.yaml` (allowing iteration on ranges).
7. Partial match: synthetic ticker matching 5/7 ranges of `fresh_awakening` → confidence = 0.714, qualifies if min_confidence = 0.70.

### Open items for implementation

- Calibration tickers list to be finalized (TCRX, BCYC, MRNA are starting set; add IMUX, ARTV if available).
- yfinance rate limits may force batch_size tuning in practice.
- Whether to include `unclassified` tickers in Module 5 candidate pool or drop them — currently retained, can be toggled in `ranking.yaml`.
