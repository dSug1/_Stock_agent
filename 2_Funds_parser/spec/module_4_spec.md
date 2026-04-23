# Module 4 — "Train Hasn't Left" Filter & Ranking

**Status:** Locked specification (revised 2026-04-23)
**Last updated:** 2026-04-23

Module 4 is split into two sub-modules that run sequentially:

- **Module 4a — Snapshot Enrichment + Hard Filters.** Reads Module 3's universe, fetches a per-ticker snapshot (market cap, sector, ADV, exchange) from Yahoo Finance into a durable SQLite cache, then applies binary cuts. Snapshot is cached so filter-threshold changes never trigger a refetch.
- **Module 4b — Price History, Ratio Matrix, Archetype Ranking.** Fetches daily price history only for survivors into the same SQLite database, computes the ratio matrix, matches against archetypes, produces the ranked candidate list for Module 5.

The split exists so expensive operations target only survivors, and so each phase can be re-run independently when configs change.

---

## Downstream contract from Module 3 (do not re-litigate)

Module 4 reads `_intermediate_outputs/universe_{quarter}.parquet` (18 columns; see `_UNIVERSE_COLUMNS` in [src/module_3/bridge.py](../src/module_3/bridge.py)). Per Module 3's locked downstream contract, the following must be handled non-fatally — never prompt the user at runtime:

- `ticker IS NULL` (CUSIP-bucketed rows): exclude from market-data fetches; carry through audit files only.
- `has_unknown_class = True`: retain, flag in output. May be filtered out via config later.
- `has_regular_warrants = True`, `has_options = True`: retain, flag. (`has_prefunded_warrants` is informational only — pre-funded warrants are already aggregated into common-equity exposure by Module 3.)
- `qoq_*` columns may be `NULL` for the first quarter of available data: treat as missing, not as failure.

---

## Storage layout

| Path | Purpose | Lifetime |
|---|---|---|
| `2_Funds_parser/data/prices.db` | SQLite. Snapshot metadata + daily OHLCV + fetch logs. Persistent. | Cross-run cache; gitignored. |
| `2_Funds_parser/_intermediate_outputs/survivors_{quarter}.parquet` | 4a output → 4b input. | Per-run. |
| `2_Funds_parser/_intermediate_outputs/hard_filter_rejections_{quarter}.parquet` | Audit. One row per rejected ticker. | Per-run. |
| `2_Funds_parser/_intermediate_outputs/ranked_candidates_{quarter}.parquet` | 4b output → Module 5 input. | Per-run. |
| `2_Funds_parser/Outputs/filter_summary_{quarter}.html` | User-facing funnel + rejection breakdown. | Per-run. |
| `2_Funds_parser/Outputs/ranking_report_{quarter}.html` | User-facing sortable ranked table. | Per-run. |
| `2_Funds_parser/Outputs/ranking_report_{quarter}.xlsx` | User-facing Excel with conditional formatting. | Per-run. |

Folder-bucket convention is the pipeline-wide rule documented in [decisions.md § Output folders](decisions.md). `data/` is a new top-level sibling of `Input/`, `Outputs/`, `_intermediate_outputs/`, `_outputs/`. The directory is created at first run by `module_1.paths.ensure_dir`.

---

## Module 4a — Snapshot Enrichment + Hard Filters

### Purpose

Eliminate tickers unfit for further analysis using cheap fund-side filters first, then snapshot data fetched from Yahoo Finance and cached durably. No price history, no LLM. Output is a survivors list that Module 4b uses to trigger targeted price-history fetching.

### Inputs

- **`_intermediate_outputs/universe_{quarter}.parquet`** (Module 3 output, 18 columns).
- **`config/filters.yaml`** (schema below).
- **`data/prices.db`** if it exists (snapshot cache; created on first run).

### Outputs

- **`_intermediate_outputs/survivors_{quarter}.parquet`**: tickers that passed all filters. Schema = all 18 universe columns + snapshot columns (`market_cap`, `sector`, `industry`, `exchange`, `adv_30d`, `last_close`, `snapshot_fetched_at`).
- **`_intermediate_outputs/hard_filter_rejections_{quarter}.parquet`**: one row per rejected ticker. Columns: `ticker, cusip, name_of_issuer, quarter, rejection_reason, rejection_value`.
- **`Outputs/filter_summary_{quarter}.html`**: funnel chart + per-filter rejection counts + a sample of dropped tickers per reason (for calibration).

### Snapshot cache (in `prices.db`)

```sql
CREATE TABLE IF NOT EXISTS ticker_snapshot (
    ticker        TEXT PRIMARY KEY,
    short_name    TEXT,
    long_name     TEXT,
    sector        TEXT,
    industry      TEXT,
    exchange      TEXT,         -- yfinance .info exchange code (NMS, NYQ, PCX, PNK, …)
    currency      TEXT,
    market_cap    INTEGER,      -- USD; null if unavailable
    shares_out    INTEGER,
    last_close    REAL,
    adv_30d       REAL,         -- 30-trading-day avg dollar volume; computed from bars
    fetched_at    TEXT,         -- ISO datetime UTC
    fetch_status  TEXT,         -- 'ok' | 'partial' | 'failed'
    fetch_error   TEXT
);
```

The snapshot is durable. Filter-threshold changes (e.g., raising `fund_count_min` from 1 to 2) reuse the cached snapshot and never trigger a Yahoo Finance call. Snapshot rows have a TTL (`snapshot_ttl_days` in config, default 7) — only stale rows refetch.

### Config schema

```yaml
# config/filters.yaml
hard_filters:
  # Cheap (fund-side; data already in universe parquet) — evaluated first
  exclude_has_unknown_class: false       # if true, drop tickers with has_unknown_class=True
  exclude_lonely_seller: true            # drop fund_count==1 AND decreased_positions>=1
  fund_count_min: 1                      # universe is built from 21 funds; min 1 by construction
  require_ticker_resolved: true          # drop ticker IS NULL rows
  require_ticker_verified: false         # if true, require ticker_is_verified

  # Expensive (snapshot-side; require yfinance fetch) — evaluated after cheap filters
  market_cap_min_usd: 50000000                  # 50M  hard floor
  market_cap_fetch_ceiling_usd: 10000000000     # 10B  outer bound — keeps candidate pool wide
  market_cap_max_usd_default: 3700000000        # 3.7B default for the runtime user prompt / --no-prompt
  adv_30d_min_usd: 250000                       # 250K
  require_min_price_usd: 0.90
  sector_allowlist: null                        # null = all; or ["Healthcare", "Biotechnology"]
  sector_blocklist: null                        # null = none; or ["Financial Services"]

snapshot:
  static_ttl_days: 30                           # static-field TTL; price-derived recomputed every run
  fetch_descriptive_info: false                 # if true, also call .info for sector/industry/names
  bars_batch_size: 150
  info_max_workers: 4
  info_rate_per_s: 2.0
  bars_rate_per_s: 2.0
  retries: 2
  retry_backoff_s: [1.0, 2.0, 4.0]
  fetch_timeout_s: 30
```

All thresholds tunable. Defaults are starting points calibrated for biotech small-mid cap.

#### Two-cap design (the user-driven cap)

The market-cap upper bound is split into two separate values:

- **`market_cap_fetch_ceiling_usd` ($10B default)** — the outer boundary. Phase 2 keeps everything up to this size in a "candidate pool". Snapshots for the full pool are persisted in `data/prices.db`, so subsequent re-runs at any user cap below the ceiling cost zero Yahoo calls.
- **User-chosen cap (CLI flag, prompt, or `market_cap_max_usd_default`)** — the actual cut applied to produce the final `survivors_{quarter}.parquet`. Tickers above the user cap (but below the ceiling) are recorded with `rejection_reason='user_market_cap_max'`, so it's easy to see what's just over the line.

At the end of Phase 2, Module 4a prints a market-cap histogram of the candidate pool and prompts:
```
=== Market-cap distribution among 1443 candidates ===
  bucket          count    cum  bar
  <= $100M           4      4
  $100M-$500M      111    115  ###
  $500M-$1B        191    306  #####
  $1B-$2B          257    563  ########
  $2B-$3.7B        434    997  ##############
  $3.7B-$5B        152   1149  ####
  $5B-$7.5B        152   1301  ####
  $7.5B-$10B       142   1443  ####
  > $10B             0   1443  (already excluded by ceiling)
  (default: $3.70B; ceiling: $10.0B)
Enter max market cap in $M [Enter for default 3,700M, 'all' for full ceiling]:
```

User responses:
- Empty → uses `market_cap_max_usd_default` (3.7B).
- A number → interpreted as $M (e.g. `5000` → $5B).
- `all` / `ceiling` / `max` → keeps everything up to the fetch ceiling.

CLI overrides:
- `scripts/4_run_hard_filters.py --market-cap-max-usd 5000000000` — explicit value, no prompt.
- `scripts/4_run_hard_filters.py --no-prompt` — uses YAML default; suitable for headless / scheduled runs.
- Non-TTY runs (e.g. piped through `tail`) auto-fall-back to the default.

### Algorithm

1. **Load universe** from `_intermediate_outputs/universe_{quarter}.parquet`.

2. **Phase 1 — cheap fund-side filters** (no network). Evaluate left-to-right; first failure determines `rejection_reason`:
   1. `require_ticker_resolved` (drop `ticker IS NULL`)
   2. `exclude_has_unknown_class` (if config flag is true)
   3. `require_ticker_verified` (if config flag is true)
   4. `fund_count_min`
   5. `exclude_lonely_seller` — drop rows where `fund_count == 1 AND decreased_positions >= 1`. (Captures "the only holder is selling" — not interesting.) `exited_positions` is not part of this check because exiting funds don't appear in current `fund_count`.

3. **Snapshot fetch for Phase-1 survivors only.** For each surviving ticker:
   - Look up `ticker_snapshot` row. If `fetched_at` within `snapshot_ttl_days` and `fetch_status='ok'`, reuse.
   - Otherwise call `yf.Ticker(t).info` for `short_name/long_name/sector/industry/exchange/currency/market_cap/shares_out`, and `yf.download(t, period='60d', interval='1d', auto_adjust=False)` for the last 30 trading-day bars used to compute `adv_30d = mean(close * volume)` and `last_close = bars['Close'].iloc[-1]`.
   - Batch the bars fetch in groups of `snapshot.batch_size` via the same `yf.download(tickers=" ".join(batch), …, group_by="ticker", threads=True)` pattern proven in [0_Renderer/2_stock_visualizer.py:420-442](../../0_Renderer/2_stock_visualizer.py#L420-L442). The `.info` calls are necessarily per-ticker.
   - On per-ticker failure: record `fetch_status='failed'` + `fetch_error`; ticker rejected with `rejection_reason='snapshot_unavailable'`. Retry once with exponential backoff before giving up.

4. **Phase 2 — expensive snapshot-side filters** on the freshly-enriched survivors:
   1. `require_min_price_usd` (vs. `last_close`)
   2. `market_cap_min_usd` / `market_cap_max_usd`
   3. `adv_30d_min_usd`
   4. `sector_allowlist` / `sector_blocklist`

5. **Write outputs.** Sort survivors by `(fund_count DESC, total_market_value DESC, ticker ASC)` for determinism. Write Parquet + HTML.

`require_min_history_weeks` is **not** a 4a filter — moved to 4b where price history is actually fetched.

### Function signatures

```python
# src/module_4/hard_filters.py

def apply_cheap_filters(
    universe_df: pd.DataFrame,
    filters_config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Phase 1. Returns (passing_df, rejections_df). No network."""

def fetch_or_reuse_snapshots(
    tickers: list[str],
    db_path: Path,
    snapshot_config: dict,
) -> pd.DataFrame:
    """Returns one row per ticker with snapshot columns + fetch_status.
    Reads cached rows where fetched_at within ttl; refetches stale rows."""

def apply_snapshot_filters(
    enriched_df: pd.DataFrame,
    filters_config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Phase 2. Returns (survivors_df, rejections_df)."""

def run_hard_filters(
    universe_path: Path,
    filters_config_path: Path,
    db_path: Path,
    output_dir: Path,
    quarter: str,
    debug: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """End-to-end orchestrator. Writes survivors + rejections parquet
    and filter_summary HTML. Returns (survivors_df, rejections_df)."""

def generate_filter_summary_html(
    rejections_df: pd.DataFrame,
    survivors_df: pd.DataFrame,
    universe_count: int,
    output_path: Path,
) -> None: ...
```

### Failure modes

- **yfinance returns empty `.info`**: snapshot row written with `fetch_status='partial'` (whatever fields populated) or `'failed'`; ticker rejected with `rejection_reason='snapshot_unavailable'`.
- **Universe ticker not on any exchange yfinance recognises**: same path — `fetch_status='failed'`.
- **Config YAML malformed**: validate at load via `module_1.config` patterns (`_require`, `_require_type`); fail loudly with line:column.
- **Empty universe (e.g., wrong quarter)**: write empty parquets + summary HTML; warning logged; pipeline does not crash.

### Acceptance tests

1. First run: fetches snapshots for Phase-1 survivors only (not the full 2,486 universe).
2. Second run same week: zero yfinance calls; survivors derived entirely from cached snapshots.
3. User raises `fund_count_min` from 1 → 2 in YAML and re-runs: zero yfinance calls; survivors recomputed from cached snapshot. **(This is the key durability guarantee.)**
4. Ticker with `fund_count==1, decreased_positions==1` rejected with `rejection_reason='exclude_lonely_seller'`.
5. Ticker with `market_cap = $10M` rejected with `rejection_reason='market_cap_min_usd'`, `rejection_value=10000000`.
6. Rejection counts in `filter_summary.html` sum to `universe_count - survivors_count`.
7. Synthetic universe row with `ticker=NULL` rejected with `rejection_reason='require_ticker_resolved'` before any yfinance call is attempted.

---

## Module 4b — Price History, Ratio Matrix, Archetype Ranking

### Purpose

For survivor tickers only: fetch and maintain daily price history in `data/prices.db`, compute the ratio matrix, match against archetypes, produce the ranked candidate list for Module 5.

### Inputs

- **`_intermediate_outputs/survivors_{quarter}.parquet`** (Module 4a output).
- **`data/prices.db`** (same file 4a uses for snapshots; persistent across runs).
- **`config/archetypes.yaml`**: archetype definitions with ranges and scores.
- **`config/ranking.yaml`**: min_confidence, tiebreakers, output behavior, young-ticker threshold.

### Outputs

- **`data/prices.db`** updated with latest price history for all survivors.
- **`_intermediate_outputs/ranked_candidates_{quarter}.parquet`**: full ranked universe (survivors only), one row per ticker. Columns:
  - Identity / passthrough: `ticker, cusip, name_of_issuer, quarter`, all 18 universe columns, all 4a snapshot columns.
  - Ranking: `rank, archetype, archetype_score, match_confidence, composite_score`.
  - Ratios: `R_4, R_12, R_26, R_52`, `R_4_over_R_12, R_4_over_R_26, R_4_over_R_52, R_12_over_R_26, R_12_over_R_52, R_26_over_R_52`.
  - Audit: `price_today, price_source_date_4w, price_source_date_12w, price_source_date_26w, price_source_date_52w, weeks_of_history_used, young_ticker_flag`.
- **`Outputs/ranking_report_{quarter}.html`**: sortable table, archetype color-coded, ratio values per row.
- **`Outputs/ranking_report_{quarter}.xlsx`**: Excel with conditional formatting on `composite_score`.

### SQLite schema (in same `data/prices.db`)

```sql
CREATE TABLE IF NOT EXISTS prices (
    ticker          TEXT NOT NULL,
    date            TEXT NOT NULL,         -- ISO date YYYY-MM-DD
    close           REAL NOT NULL,         -- raw close (for reference)
    adjusted_close  REAL NOT NULL,         -- split/dividend adjusted; used for all ratios
    volume          INTEGER,
    PRIMARY KEY (ticker, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON prices(ticker, date DESC);

CREATE TABLE IF NOT EXISTS price_fetch_log (
    ticker          TEXT PRIMARY KEY,
    first_date      TEXT,
    last_date       TEXT,
    last_fetched_at TEXT,                  -- ISO datetime UTC
    source          TEXT DEFAULT 'yfinance',
    fetch_status    TEXT,                  -- 'ok' | 'partial' | 'failed'
    fetch_error     TEXT
);

PRAGMA journal_mode = WAL;
```

`PRAGMA journal_mode = WAL` is set at first connection. Same WAL setup as [0_Renderer/2_stock_visualizer.py:228](../../0_Renderer/2_stock_visualizer.py#L228).

### Algorithm

#### Step 1 — Incremental price fetch

For each ticker in survivors:

1. Query `price_fetch_log` for `last_date` and `last_fetched_at`.
2. If `last_fetched_at` is today (UTC date), skip the fetch (idempotent same-day re-runs).
3. If no row exists, fetch full ~1.5 years (~400 trading days) — enough for 52w ratios plus buffer.
4. If row exists, fetch from `last_date + 1` to today.
5. **`yf.download(..., auto_adjust=False, actions=False)`** so both `Close` and `Adj Close` are returned. Store both. Spec D8 in [decisions_module_4.md](decisions_module_4.md) is satisfied by computing all ratios on `adjusted_close`.
6. Upsert into `prices`. Update `price_fetch_log`.
7. On failure: log error, set `fetch_status='failed'`, continue. If existing rows in DB suffice for required windows, the ticker proceeds. Otherwise it falls into `young_ticker` handling at ratio time.

Batch yfinance calls in groups of `ranking.fetch_batch_size` (default 50). Retry failed tickers once with exponential backoff. The batch fetch reuses the proven pattern from [0_Renderer/2_stock_visualizer.py:420-442](../../0_Renderer/2_stock_visualizer.py#L420-L442) — `yf.download(tickers=" ".join(batch), group_by="ticker", threads=True)` with per-ticker fallback on batch failure.

#### Step 2 — Compute ratio matrix

For each ticker, pull all available rows from `prices` (≤ 400 trading days). Use `adjusted_close` for all computations.

```
R_4  = price_today / price_on_date_closest_to(today - 4w)
R_12 = price_today / price_on_date_closest_to(today - 12w)
R_26 = price_today / price_on_date_closest_to(today - 26w)
R_52 = price_today / price_on_date_closest_to(today - 52w)
```

"Closest to" uses ±`window_tolerance_trading_days` (default 3) trading-day tolerance. If the target window predates the ticker's first available bar:

- If `ranking.young_ticker_flat_fill: true` → set `R_Xw = 1.0`, set `young_ticker_flag = True`.
- If `ranking.young_ticker_flat_fill: false` → exclude the ticker from ranking (write to a `young_ticker_excluded_{quarter}.parquet` audit file).

Apply the `require_min_history_weeks` filter here (default 12 weeks). Tickers with fewer weeks of history than the threshold are excluded from ranking and written to the same young-ticker audit file.

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

Each archetype only specifies the ranges it cares about (decisions_module_4.md D5). Tickers are not penalised for unspecified dimensions.

Among archetypes where `confidence >= ranking.min_confidence` (default 0.70), the ticker is assigned the one with the highest confidence. Ties broken by highest `|archetype_score|` (most opinionated archetype wins).

If no archetype qualifies: `archetype = "unclassified"`, `archetype_score = 0`, `confidence = 0`.

#### Step 4 — Composite score

```
alpha = ranking.confidence_floor_weight   # default 0.7
composite_score = archetype_score × (alpha + (1 - alpha) × match_confidence)
```

Score-floor weighting (locked 2026-04-23 after the pure-multiplication formula was rejected for letting `(score=10, conf=0.5)` tie `(score=5, conf=1.0)`). With `alpha = 0.7`, every ticker assigned to a +10 archetype outranks every ticker assigned to a +5 archetype regardless of confidence; confidence becomes the within-class tiebreaker. `alpha = 0` reproduces the legacy formula; `alpha = 1` ignores confidence (gate-only).

Pure trajectory-based ranking (decisions_module_4.md D2). Fund accumulation, compression, distance-to-52w-low, etc. are preserved in the output for human inspection and Module 5's LLM context, but do not enter the score.

#### Step 5 — Rank and write outputs

Sort by `composite_score` DESC. Tiebreakers from `ranking.tiebreakers` (default: `fund_count` DESC, then `ticker` ASC). Assign `rank` column (1-indexed).

Write Parquet to `_intermediate_outputs/`. Generate HTML and Excel reports to `Outputs/`.

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
  confidence_floor_weight: 0.7            # composite = score * (alpha + (1-alpha)*confidence)
  include_unclassified: true              # keep at bottom; if false, drop
  window_tolerance_trading_days: 3
  require_min_history_weeks: 52           # tickers below this excluded from ranking
  young_ticker_flat_fill: true            # false = exclude young tickers
  fetch_batch_size: 50
  output_top_n: null                      # null = all survivors; or integer to cap
  tiebreakers:                            # applied in order after composite_score
    - fund_count_desc
    - ticker_asc
```

### Function signatures

```python
# src/module_4/prices.py

def init_prices_db(db_path: Path) -> None:
    """CREATE TABLE IF NOT EXISTS for snapshots, prices, fetch logs. Sets WAL."""

def fetch_incremental_prices(
    tickers: list[str],
    db_path: Path,
    batch_size: int = 50,
    source: str = "yfinance",
) -> dict[str, str]:
    """Returns dict of ticker -> fetch_status.
    Reuses the batched yf.download pattern from 0_Renderer (separate database)."""

def get_price_on_date(
    conn: sqlite3.Connection,
    ticker: str,
    target_date: pd.Timestamp,
    tolerance_trading_days: int = 3,
) -> tuple[float | None, pd.Timestamp | None]:
    """Returns (adjusted_close, actual_date) or (None, None) if outside tolerance."""

# src/module_4/ratios.py

def compute_ratios_for_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    reference_date: pd.Timestamp,
    flat_fill: bool = True,
) -> dict:
    """Returns dict with R_4/12/26/52, all 6 inter-window ratios,
       source dates, young_ticker_flag, weeks_of_history_used."""

# src/module_4/archetypes.py

def match_archetypes(
    features: dict[str, float],
    archetypes_config: dict,
    min_confidence: float = 0.70,
) -> tuple[str, float, float]:
    """Returns (archetype_name, archetype_score, match_confidence)."""

# src/module_4/ranking.py

def rank_universe(
    survivors_path: Path,
    db_path: Path,
    archetypes_config_path: Path,
    ranking_config_path: Path,
    output_dir: Path,
    quarter: str,
    rerank_only: bool = False,
    debug: bool = False,
) -> pd.DataFrame:
    """End-to-end orchestrator. Updates prices.db (skipped if rerank_only=True),
       computes ratios, matches archetypes, writes ranked output and reports."""
```

### Re-ranking on config change

User edits `archetypes.yaml` or `ranking.yaml` (excluding `require_min_history_weeks` and `fetch_batch_size`), re-runs Module 4b with `--rerank-only`, gets updated ranking within seconds (no price fetch). Module 5's cached LLM responses remain valid (keyed by ticker + quarter, independent of ranking).

### CLI

Following the established pattern (`scripts/2_*.py`, `scripts/3_*.py`):

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/4_run_hard_filters.py --quarter 2025Q4
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/4_rank.py --quarter 2025Q4
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/4_rank.py --quarter 2025Q4 --rerank-only
```

Both chained into [run_2_Funds_parser.bat](../run_2_Funds_parser.bat) behind a third `[y/N]` prompt (after the existing Module 3 prompt).

### Caching

- `data/prices.db` is the durable cache for snapshots (4a) and daily bars (4b).
- Snapshots respect `snapshot.ttl_days` (default 7).
- Bars use `last_fetched_at` to skip same-day re-fetches.
- Ratio computations are fast; not cached.

### Failure modes

- **yfinance returns empty for a ticker**: retry once, then flag `fetch_status='failed'` in `price_fetch_log`. If sufficient history already in DB for required windows, proceed with cached data. Otherwise treat as young_ticker (per `young_ticker_flat_fill` config).
- **Price gap in middle of history**: use closest available within tolerance; if none, flag the affected window as missing for that ticker.
- **Archetype YAML malformed**: validate at load, fail loudly with line:column.
- **No survivors input**: empty output files, warning logged, pipeline doesn't crash.
- **All archetypes yield confidence < min_confidence for a ticker**: assign `unclassified`, retain in output at bottom (configurable via `ranking.include_unclassified`).

### Acceptance tests

1. First run with empty `data/prices.db` → full ~400-day history fetched for all survivors.
2. Second run same day → no price fetches, uses cached data.
3. Second run next day → one-day delta fetch per ticker.
4. Synthetic ticker with fabricated prices matching `fresh_awakening` exactly → confidence = 1.0.
5. Ranking is deterministic: two back-to-back runs produce identical Parquet (byte-for-byte after sorting).
6. `--rerank-only` after editing `archetypes.yaml` → no yfinance calls, output reflects new ranges.
7. Partial match: synthetic ticker matching 5/7 ranges of `fresh_awakening` → confidence = 0.714, qualifies if min_confidence = 0.70.
8. Ticker with 8 weeks of history (below default `require_min_history_weeks=12`) excluded from ranking; appears in `young_ticker_excluded_{quarter}.parquet`.

---

## Yahoo Finance throughput — optimisation plan (locked)

Module 4a snapshot fetch is the dominant network cost (cold start: ~1,500–2,000 tickers × `.info` calls). All optimisations below are part of the implementation contract; do not omit.

### 1. curl_cffi-backed session (the single biggest win)

The venv has `yfinance==1.3.0` and `curl_cffi==0.15.0`. curl_cffi impersonates Chrome's TLS fingerprint and JA3 hash, so Yahoo's bot-detection layer treats requests like a real browser — drastically reducing 429s and silent throttling versus the default `requests` backend.

```python
from curl_cffi import requests as curl_requests

_YF_SESSION = curl_requests.Session(impersonate="chrome")  # module-scope, shared
# pass session=_YF_SESSION to every yf.Ticker() and yf.download()
```

Single shared `Session` keeps the TLS/HTTP keep-alive pool warm across all calls in a run.

### 2. Bars — batched `yf.download(threads=True)`

One HTTP request per batch; yfinance dispatches sub-requests in parallel via its `multitasking` helper. Sweet spot is **100–200 tickers per call** for daily bars; larger batches risk URL-length limits and partial-fail behaviour.

```python
df = yf.download(
    tickers=" ".join(batch),       # 100-200 tickers
    period="60d",                  # 4a snapshot = 60d; 4b cold start = 400d-ish
    interval="1d",
    auto_adjust=False,
    actions=False,
    group_by="ticker",
    threads=True,
    progress=False,
    session=_YF_SESSION,
)
```

This is the **only** correct pattern for bars. Per-ticker bars fetching is reserved for the per-ticker fallback when a batch returns partial data (mirroring [0_Renderer/2_stock_visualizer.py:553-558](../../0_Renderer/2_stock_visualizer.py#L553-L558)).

### 3. `.info` — parallelised with a process-global rate limiter

There is no Yahoo batch endpoint for company metadata. yfinance's `Tickers` class is a thin wrapper that still issues one HTTP request per ticker. The only optimisation available is parallel dispatch under a bounded throttle:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading, time

class _TokenBucket:
    """Process-global rate limiter. Ported from layer_1/edgar_13f._RateLimiter
    pattern (proven against SEC EDGAR at 9 req/s)."""
    def __init__(self, rate_per_s: float):
        self.rate = rate_per_s
        self.allowance = rate_per_s
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.allowance = min(self.rate, self.allowance + (now - self.last) * self.rate)
            self.last = now
            if self.allowance < 1.0:
                wait = (1.0 - self.allowance) / self.rate
                time.sleep(wait)
                self.allowance = 0.0
            else:
                self.allowance -= 1.0

_YF_LIMITER = _TokenBucket(rate_per_s=5.0)  # 5 req/s sustained — Yahoo-safe with curl_cffi

def fetch_one_info(ticker: str) -> dict:
    _YF_LIMITER.acquire()
    return yf.Ticker(ticker, session=_YF_SESSION).info

with ThreadPoolExecutor(max_workers=8) as pool:
    futures = {pool.submit(fetch_one_info, t): t for t in tickers}
    for fut in as_completed(futures):
        ...
```

Tuning rationale:
- **5 req/s** is the empirical ceiling for `.info` against Yahoo with curl_cffi. Higher values trigger 429 cascades within minutes.
- **8 worker threads** > 5/s rate is intentional: workers spend most wall-time blocked on TCP/HTTP, so 8 threads keep the limiter saturated even when individual calls take 800–1500 ms.
- The limiter is **module-scope**, not per-thread. `.acquire()` is called immediately before the HTTP request (mirroring `_EDGAR_LIMITER` usage in [src/layer_1/edgar_13f.py](../src/layer_1/edgar_13f.py)).

Estimated cold-start cost at 5 req/s: ~2,000 `.info` calls = ~7 minutes. Acceptable as a one-time cost; the 7-day TTL means subsequent runs hit cache.

### 4. Retry policy

Exponential backoff on `429`, timeouts, and empty responses: **1s → 2s → 4s, max 2 retries**, then mark `fetch_status='failed'` and continue. Do not retry indefinitely; the SQLite log captures failures for triage.

### 5. SQLite cache is the dominant long-term optimisation

Snapshot rows respect `snapshot.ttl_days=7`. Bars respect `last_fetched_at` (skip if today). After the first run, daily updates touch only delta bars and zero `.info` calls until the snapshot row goes stale. This is why the durability guarantee in Acceptance Test 3 matters.

### 6. What we deliberately do NOT do

- **No `requests_cache`.** Adding HTTP-layer caching on top of the SQLite cache is redundant and adds a dependency. Our cache key (ticker + table) is more semantically correct than HTTP URL.
- **No async (`asyncio`/`aiohttp`).** yfinance is sync; wrapping it in async adds complexity without throughput gain (the bottleneck is Yahoo's per-IP rate limit, not local concurrency).
- **No `Tickers(plural)` class.** It does not batch `.info` internally — it just iterates per-ticker. Using `ThreadPoolExecutor` directly is more transparent.
- **No proxy rotation, no IP cycling.** Out of scope and against Yahoo ToS — addressed by the `project_data_provider_switch` memory (move to Twelve Data before public deploy).

### 7. Tunables in `config/filters.yaml` and `config/ranking.yaml`

```yaml
# filters.yaml
snapshot:
  ttl_days: 7
  bars_batch_size: 150              # tickers per yf.download() for ADV bars
  info_max_workers: 8
  info_rate_per_s: 5.0
  fetch_timeout_s: 30
  retries: 2
  retry_backoff_s: [1, 2, 4]
```

```yaml
# ranking.yaml
ranking:
  ...
  fetch_batch_size: 100             # tickers per yf.download() for 400d bars
  fetch_max_workers: 4              # only used in per-ticker fallback path
  fetch_rate_per_s: 5.0
```

All tunables surface so the user can dial throughput up if Yahoo's behaviour changes — without touching code.

### 8. Daily-run optimisation — `market_cap` is derived, not fetched

`market_cap = shares_outstanding × last_close`. The expensive part (`shares_out` from `fast_info`) changes ~quarterly. The cheap part (`last_close` from the bars batch) changes daily. Therefore:

- Snapshot rows have **two TTLs**: `static_ttl_days` (default 30) for `shares_out`, `sector`, `industry`, `exchange`, `currency`; price-derived fields (`last_close`, `adv_30d`, `market_cap`) recomputed every run from the always-fresh bars batch.
- Daily runs after the first cold start hit ZERO `.info` / `fast_info` calls — only the bars endpoint, which is reliable and batched. Wall time: ~1-2 min for ~2,000 tickers.
- After 30 days, a small subset of static rows expire and refresh.

This makes the user's scheduled daily run cheap and throttle-safe.

### 9. IP-throttle handling

Yahoo enforces an undocumented per-IP cap (~few thousand HTTPS requests per hour across both `quoteSummary`/`fast_info` and chart endpoints). When exceeded, requests return `YFRateLimitError: Too Many Requests` and stay blocked for ~30-60 min regardless of backoff.

Module 4's strategy: process-global throttle flag. First `YFRateLimitError` flips the flag; all subsequent yfinance calls in this run raise `YahooThrottled` immediately without HTTP. Whatever's already fetched persists; deferred tickers are marked `partial` (no `fetch_error`) so the next run's cache-staleness check picks them up. **No retry-storms; no compounding the throttle.**

The `.info` endpoint is opt-in via `snapshot.fetch_descriptive_info` (default `false`). With the default, only `fast_info` is called per ticker — halving HTTP volume vs the legacy fast_info+.info path. Enable `.info` only when `sector_allowlist`/`blocklist` are actually used.

### 10. Acceptance tests (additional)

10. **Daily-run fast path:** second run within `static_ttl_days` makes ZERO `fast_info` / `.info` calls; only bars batches. Wall time ≤ 2 min for ~2,000 survivors.
11. **Cold-start cap:** first run for ~2,000 tickers completes in ≤ 20 min wall (allowing for the 2 req/s default), or fails gracefully via `YahooThrottled` and persists what it got.
12. **Throttle resumability:** after `YahooThrottled` aborts mid-run, a subsequent run picks up only the deferred tickers (partial rows with no fetch_error are detected as stale).

---

## Open items deferred to v2 (post-MVP)

These are explicitly out-of-scope for the first Module 4 implementation; do not block the build on them.

- **Calibration tooling** (`module_4_tools/calibrate.py`, `sensitivity.py`, `distribution.py`) and `config/calibration.yaml`. Once Module 4 produces real rankings, these tools will compare against expected archetypes for reference tickers (TCRX, BCYC, MRNA starting set). Tracked in [decisions_module_4.md § Open / deferred](decisions_module_4.md).
- **`exclude_if_recent_reverse_split`** filter. Requires a corp-actions data source not currently available.
- **`fund_count_min` calibration**. Default `1` is a no-op (universe is built from 21 funds). User may raise to 2+ once real ranking is observed; the snapshot cache guarantees no refetch is needed when this changes.
- **Provider swap** (yfinance → Twelve Data) before public deploy. Per repo-wide memory `project_data_provider_switch`. Module 4's `prices.py` keeps `source` parameter pluggable; that is the only adapter point needed today.
