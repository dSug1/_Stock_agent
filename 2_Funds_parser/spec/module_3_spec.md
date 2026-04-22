# Module 3 — Data Bridge

**Status:** Implemented 2026-04-22. See [decisions.md § Module 3 — Implemented 2026-04-22](decisions.md) for deviations from spec and verified acceptance tests.
**Last updated:** 2026-04-22
**Runtime:** Deterministic. DB read + in-memory pandas aggregation + Parquet write. No network.

Module 3 is the pipeline's **seam** between raw 13F filings (Module 2) and the ranking stages (Modules 4+). It reads Module 2's per-filing holdings, classifies each row by share type, drops non-equity positions to audit files, aggregates per fund then across funds, and produces the per-ticker universe that Module 4 consumes.

---

## Prerequisites (all cleared 2026-04-22)

- `holdings.title_of_class` and `holdings.put_call` columns exist and are populated for all 77,763 rows ([decisions.md § Schema extension 2026-04-22](decisions.md)).
- Module 1 config loader exposes `resolve_quarter`, `quarter_to_date_end` (to be added), and the `PipelineConfig` dataclass ([src/module_1/](../src/module_1/)).
- `2_fundparser.db` is populated: 21 funds, 502 filings, 77,763 holdings.

---

## Role

1. Resolve the target quarter (Module 1 config → `YYYYQn` → `period_of_report` quarter-end date).
2. Read `holdings` scoped to the 21 tracked funds and that `period_of_report`.
3. Deduplicate amendments: for each `(fund_id, period_of_report)`, keep only the latest `filing_date`'s rows.
4. Classify each row's `(title_of_class, put_call)` into a **share class**: `common | prefunded_warrant | regular_warrant | put | call | preferred | debt | unknown`.
5. Drop `put`/`call`/`preferred`/`debt`/`regular_warrant` to `dropped_rows_{quarter}.parquet`; flag `unknown` to `unresolved_positions_{quarter}.parquet` but retain in-flow.
6. Aggregate retained rows per `(fund_id, ticker | cusip-fallback)`.
7. Repeat Steps 2–6 for the prior quarter to enable QoQ deltas.
8. Aggregate across funds per ticker; compute QoQ metrics.
9. Write three Parquet files to `_intermediate_outputs/`.

Module 3 does **not** fetch market data, does **not** call any external API, and does **not** decide ranking. It reshapes fund-position data into a per-ticker universe for Module 4.

---

## Inputs

- `2_fundparser.db` (read-only) — Module 2's output.
- `config/pipeline.yaml` via Module 1 — `quarter`, `db_schema`, `paths`.
- `config/share_types.yaml` — classification rules (schema below).
- Optional CLI: `--quarter {YYYYQn|auto-latest}` overrides `config.quarter`.

## Outputs

All three land in `_intermediate_outputs/` and are idempotent (re-running on the same quarter overwrites):

- **`universe_{quarter}.parquet`** — one row per ticker (or per CUSIP when ticker unresolved). Consumed by Module 4.
- **`dropped_rows_{quarter}.parquet`** — audit. One row per position dropped by share-class filtering.
- **`unresolved_positions_{quarter}.parquet`** — audit. One row per position whose `title_of_class` matched no rule.

---

## Algorithm

### Step 0 — Resolve quarter and derive dates

- `quarter = resolve_quarter(config)` → `YYYYQn`.
- `target_period = quarter_to_date_end(quarter)` → `YYYY-03-31` / `YYYY-06-30` / `YYYY-09-30` / `YYYY-12-31`.
- `prior_period = quarter_to_date_end(prior_quarter(quarter))` for QoQ.

### Step 1 — Load current-quarter holdings (amendment-deduplicated)

```sql
WITH latest AS (
    SELECT fund_id, period_of_report, MAX(filing_date) AS latest_filing
    FROM holdings
    WHERE period_of_report = :target_period
    GROUP BY fund_id, period_of_report
)
SELECT h.fund_id, f.name AS fund_name, h.filing_date, h.period_of_report,
       h.name_of_issuer, h.ticker, h.ticker_source, h.cusip,
       h.shares, h.market_value, h.title_of_class, h.put_call
FROM holdings h
JOIN latest l
  ON h.fund_id = l.fund_id
 AND h.period_of_report = l.period_of_report
 AND h.filing_date = l.latest_filing
JOIN funds f ON f.id = h.fund_id;
```

**Rationale.** A fund may file an amendment (same `period_of_report`, later `filing_date`, different `accession_number`). The latest `filing_date` supersedes prior ones. Dedup is per-`(fund, period)`, never cross-fund.

### Step 2 — Classify share type per row

Load `config/share_types.yaml`. For each row:

```python
def classify_share_type(title_of_class, put_call, rules) -> str:
    if put_call == "Put":  return "put"
    if put_call == "Call": return "call"
    title = (title_of_class or "").upper()
    for rule in rules:
        if rule["match"].upper() in title:
            return rule["classify_as"]
    return "unknown"
```

- `put_call` is checked first so a PUT titled `"COM"` (which happens) is correctly classified as `put`.
- Substring match is case-insensitive; **first rule wins** — rule order in the YAML is load-bearing.
- `None`/empty `title_of_class` → falls through to the loop; if no rules match → `unknown`.

Attach `share_class` as a new column on the DataFrame.

### Step 3 — Drop rules and flag propagation

Partition rows by `share_class`:

| `share_class`         | Destination                               | Counted in share totals? |
|-----------------------|-------------------------------------------|--------------------------|
| `common`              | in-flow                                   | yes                      |
| `prefunded_warrant`   | in-flow                                   | yes (share-equivalent)   |
| `unknown`             | in-flow + `unresolved_positions_*.parquet`| yes                      |
| `put`                 | `dropped_rows_*.parquet`                  | no                       |
| `call`                | `dropped_rows_*.parquet`                  | no                       |
| `regular_warrant`     | `dropped_rows_*.parquet`                  | no                       |
| `preferred`           | `dropped_rows_*.parquet`                  | no                       |
| `debt`                | `dropped_rows_*.parquet`                  | no                       |

Before dropping, set boolean flags on the corresponding `(fund_id, ticker)` group:

- `has_options` — any put/call present.
- `has_regular_warrants` — any regular_warrant present.
- `has_prefunded_warrants` — any prefunded_warrant present (also retained in share totals).
- `has_unknown_class` — any unknown present (also retained in share totals).

**Why retain unknown.** Silently dropping unclassified rows would hide biotech-relevant exposure (rare instruments like contingent value rights). The audit file plus the flag gives user one-shot triage without breaking the unattended daily run. Recurring unknowns get promoted into `share_types.yaml` at user review time.

### Step 4 — Per-`(fund, ticker)` aggregation

Group retained rows (`common` + `prefunded_warrant` + `unknown`) by `(fund_id, ticker)`, falling back to `(fund_id, cusip)` when `ticker IS NULL`. For each group:

- `shares_combined` = sum of `shares`
- `market_value_combined` = sum of `market_value`
- `has_prefunded_warrants`, `has_regular_warrants`, `has_options`, `has_unknown_class` — boolean OR across the group's rows (flags computed before the Step 3 drop so they survive).

Multiple rows per `(fund, ticker)` occur when a fund holds common + pre-funded warrants + an unknown-class position of the same issuer — those are summed into one fund-position row.

### Step 5 — Load prior-quarter aggregated fund positions

Re-run Steps 1–4 with `period_of_report = prior_period`. If no prior-quarter holdings exist (first quarter of tracking, or sparse historical data), skip QoQ: all QoQ columns become null, log once.

### Step 6 — Per-ticker aggregation across funds

Group by `ticker` (prefer ticker; fall back to `cusip` when ticker is null). For each bucket:

**Totals:**
- `fund_count` = distinct count of `fund_id`s holding share-equivalent positions.
- `total_shares` = sum of `shares_combined` across funds.
- `total_market_value` = sum of `market_value_combined` across funds.

**Provenance / flags:**
- `ticker_is_verified` = any fund's `ticker_source IN ('openfigi', 'manual')`. `'sec_name'` does **not** count (name-match fallback can return the wrong security class for the same issuer). If Module 2 adds `'manual'` via the fill-in workflow, treat it as verified.
- `has_prefunded_warrants`, `has_regular_warrants`, `has_options`, `has_unknown_class` = boolean OR across funds.
- `name_of_issuer` = latest non-null name across funds (deterministic: longest string wins on ties).

**QoQ metrics** (null if no prior-quarter data):

- `qoq_share_change` = `total_shares` − `prior_total_shares` (treat missing prior as 0).
- `qoq_fund_count_change` = `fund_count` − `prior_fund_count`.
- `new_positions` = count of funds with a position this quarter AND no position last quarter.
- `increased_positions` = count of funds whose `shares_combined > prior_shares_combined`.
- `decreased_positions` = count of funds whose `shares_combined < prior_shares_combined`.
- `exited_positions` = count of funds with a position last quarter AND none this quarter (NOT contributing to `fund_count` but useful for Module 4 "accumulation" signal).

### Step 7 — Deterministic sort and write

Sort the universe by:

1. `fund_count` desc
2. `total_market_value` desc
3. `ticker` asc (nulls last; nulls sorted by `cusip` asc)

Write Parquet. Deterministic sort + fixed column order enables byte-identical re-runs for caching and diffing.

---

## Classification rules — `config/share_types.yaml`

Order matters: first case-insensitive substring match on `title_of_class` wins. `put_call` is handled outside this list — any row with `put_call IN ('Put','Call')` is classified accordingly regardless of `title_of_class`.

```yaml
classification_rules:
  # Pre-funded warrants — most specific, must precede generic WT/WARRANT.
  - match: "PRE-FUND"   ; classify_as: prefunded_warrant
  - match: "PREFUND"    ; classify_as: prefunded_warrant
  - match: "PFD WT"     ; classify_as: prefunded_warrant   # some filers use PFD as an abbrev for pre-funded
  - match: "PFW"        ; classify_as: prefunded_warrant   # rare abbreviation

  # Regular warrants and bundled instruments → dropped.
  - match: "WT"         ; classify_as: regular_warrant
  - match: "WARRANT"    ; classify_as: regular_warrant
  - match: "RIGHT"      ; classify_as: regular_warrant     # subscription rights; treat as warrant-equivalent drop
  - match: "UNIT"       ; classify_as: regular_warrant     # SPAC units (share+warrant bundles) → conservative drop

  # Preferred stock → dropped (not part of the common-equity universe we rank).
  - match: "PFD"        ; classify_as: preferred
  - match: "PREFERRED"  ; classify_as: preferred

  # Debt instruments → dropped.
  - match: "NOTE"       ; classify_as: debt
  - match: "BOND"       ; classify_as: debt
  - match: "DEBENTURE"  ; classify_as: debt
  - match: "CONV"       ; classify_as: debt                # convertible notes; err on debt rather than common

  # Common-equivalent share classes → kept.
  - match: "COM"        ; classify_as: common              # "COM", "COMM", "COMMON", "COM NEW", "COM CL A", "CL A COM"
  - match: "ORD"        ; classify_as: common              # ordinary shares (non-US issuers)
  - match: "ADR"        ; classify_as: common              # American Depositary Receipt
  - match: "ADS"        ; classify_as: common              # American Depositary Share
  - match: "SHS"        ; classify_as: common              # "SHS", "ORD SHS"
  - match: "CLASS "     ; classify_as: common              # "CLASS A", "CLASS B"
  - match: "CL "        ; classify_as: common              # "CL A", "CL B" — broad; last among common rules
  - match: "SH"         ; classify_as: common              # bare "SH" (ordinary-share abbrev) — broadest fallback
```

**Rationale.**

- **PFD before CL / SH.** A row titled `"CL A PFD"` or `"PFD A SH"` must classify as `preferred` (first rule match = PFD). Common fallback rules only fire when no preferred/debt/warrant signal is present.
- **COM before CL.** `"CL A COM"` classifies as common via `"COM"` rule. Only bare `"CL A"` / `"CL B"` reach the `"CL "` rule.
- **SH last.** Extremely broad — only reaches rows like bare `"SH"` that missed all more-specific rules.
- **Observed rule-miss count after this ruleset:** mostly `UNIT 99/99/9999` (373 rows, SPAC units — now caught by `"UNIT"`), various edge-case structured products. Expected `unknown` rate: <1% of rows.

**Why pre-funded warrants are share-equivalent.** Pre-funded warrants have a nominal ~$0.0001 exercise price; exercisable at any time; `shares` in the 13F reflects underlying share count. Economically identical to common stock. Biotech funds accumulate them alongside common to avoid 5% disclosure thresholds.

**Why regular warrants and rights are dropped.** Non-pre-funded warrants require material cash exercise; rights are limited-life; SPAC units bundle common+warrants with non-linear valuation. Including their `shares` field would overstate fund conviction in the underlying equity. Conservative default — surfaced in `dropped_rows_*.parquet` for user review.

**User-curated overrides.** The YAML is the single source of truth. Recurring entries in `unresolved_positions_*.parquet` are triaged by the user and promoted into the config.

---

## Output schemas

### `universe_{quarter}.parquet` — one row per `(ticker | cusip-fallback)`

| Column                     | Type    | Description                                                         |
|----------------------------|---------|---------------------------------------------------------------------|
| `ticker`                   | str?    | Resolved ticker; null if only CUSIP known                           |
| `cusip`                    | str     | CUSIP of the position (always present)                              |
| `name_of_issuer`           | str?    | Deterministic choice across funds (longest non-null name)           |
| `fund_count`               | int     | Distinct funds holding share-equivalent positions                   |
| `total_shares`             | int     | Sum across funds of `shares_combined`                               |
| `total_market_value`       | int     | Sum across funds of `market_value_combined` (raw USD)               |
| `qoq_share_change`         | int?    | `total_shares` − prior quarter; null if no prior                    |
| `qoq_fund_count_change`    | int?    | Fund count delta vs prior quarter                                   |
| `new_positions`            | int?    | Funds that opened a position this quarter                           |
| `increased_positions`      | int?    | Funds that added to existing positions                              |
| `decreased_positions`      | int?    | Funds that trimmed                                                  |
| `exited_positions`         | int?    | Funds that held last quarter but not this quarter                   |
| `ticker_is_verified`       | bool    | Any fund's `ticker_source ∈ {openfigi, manual}`                     |
| `has_prefunded_warrants`   | bool    | Any fund holds pre-funded warrants of this issuer                   |
| `has_regular_warrants`     | bool    | Any fund held (now-dropped) regular warrants                        |
| `has_options`              | bool    | Any fund held (now-dropped) puts/calls                              |
| `has_unknown_class`        | bool    | Any fund held a position with unclassified `title_of_class`         |
| `quarter`                  | str     | `YYYYQn` — copied into every row for downstream ergonomics          |

### `dropped_rows_{quarter}.parquet` — audit of dropped positions

| Column | Notes |
|--------|-------|
| `fund_id`, `fund_name` | |
| `filing_date`, `period_of_report` | |
| `cusip`, `ticker`, `name_of_issuer` | |
| `title_of_class`, `put_call` | |
| `share_class` | Classifier bucket that triggered the drop |
| `drop_reason` | Same as `share_class`; redundant column for SQL/filter ergonomics |
| `shares`, `market_value` | |
| `quarter` | |

### `unresolved_positions_{quarter}.parquet` — audit of unclassified

Same columns as `dropped_rows_{quarter}.parquet` but for rows where `share_class == "unknown"`. These rows are **retained** in the universe (contribute to share totals) and also emitted here for triage. User review cadence: after each run; recurring patterns promoted into `share_types.yaml`.

---

## Downstream contract (Module 4, 5, 6, 7)

Fields added by Module 3 that downstream modules **must** handle non-fatally — no runtime prompts, no pipeline aborts:

- **`ticker IS NULL`** — fallback identifier is `cusip`. Modules that require a ticker (Module 5 market-data fetch, Module 6 LLM context) skip these rows and log them as "unresolved"; they are not errors. User resolves via the manual-fill-in workflow (see below) at their own cadence.
- **`has_unknown_class=True`** — flag only; the row is valid share-equivalent data. Downstream reports may surface this as a "review" hint (e.g., bordered cell in HTML) but must not skip or error.
- **`has_regular_warrants=True`** / **`has_options=True`** — metadata flags; shares totals already exclude these. Downstream uses: Module 4 may penalise rank for excessive option activity; Module 6 context pack may surface warrant overhang as a qualitative signal.
- **`qoq_*` columns are null** — first-quarter-of-tracking case. Downstream computes ranking on current-quarter metrics alone; no backfill attempt from prior modules.

This contract is enforced by Module 3's output schema (all flag columns are bool, never null; ticker fallback is baked into the row-identity logic) and honoured by Modules 4+ at their implementation time. The contract is re-stated in each downstream spec.

---

## Manual-ticker fill-in workflow (Module 2 companion scripts)

These scripts live under `2_Funds_parser/scripts/` and write to Module 2's `cusip_ticker_map` and `holdings` tables. They are triggered by Module 3's output but are logically part of Module 2's CUSIP-resolution layer (so Module 2's spec owns the `ticker_source='manual'` value).

### `scripts/list_unresolved_cusips.py`

**Purpose.** Dump every CUSIP present in the current universe with `ticker IS NULL`, together with every `name_of_issuer` variant observed, so the user can paste the list into an external AI tool for ticker lookup.

**Output.** Writes `_intermediate_outputs/unresolved_cusips_{quarter}.tsv` (tab-separated, pasteable into any tool) AND prints the same table to stdout. One row per CUSIP (deduped across funds). Columns: `cusip`, `name_of_issuer` (pipe-joined if multiple distinct names across funds), `fund_count`, `total_shares`, `total_market_value`.

**CLI.** `python scripts/list_unresolved_cusips.py [--quarter YYYYQn]`. Quarter defaults to Module 1's `auto-latest`.

### `scripts/apply_manual_ticker_mappings.py`

**Purpose.** Accept a user-edited TSV/CSV of `(cusip, ticker)` pairs and write them back to the DB.

**Input.** A TSV/CSV with columns `cusip` and `ticker` (extra columns ignored). Path via `--input <file>`. Blank `ticker` cells are skipped (user indicating "no resolution available"). Comments (lines starting with `#`) are skipped.

**Behavior:**
1. UPSERT into `cusip_ticker_map`: insert new rows with `ticker_source='manual'` and `resolved_date=today`; update existing rows only when `ticker IS NULL` (don't overwrite OpenFIGI hits). Log conflicts for user review.
2. UPDATE `holdings` where `cusip = ? AND ticker IS NULL` → set `ticker, ticker_source='manual', updated_at=now`.
3. Print a summary: rows inserted into map, rows updated in holdings, conflicts skipped.

**CLI.** `python scripts/apply_manual_ticker_mappings.py --input <path>`.

**Rendering policy.** `ticker_source='manual'` is treated as **verified** (the user vouched). HTML report renders `manual` rows identically to `openfigi` (no orange-italic warning). If precision turns out to be lower than expected, downgrade to a distinct render treatment in a future pass.

### End-to-end workflow

```
1. Run Module 3 → universe_{quarter}.parquet has some ticker=null rows.
2. Run list_unresolved_cusips.py → writes unresolved_cusips_{quarter}.tsv.
3. User pastes the TSV into an external AI tool, gets back tickers, saves as `manual_map.tsv`.
4. Run apply_manual_ticker_mappings.py --input manual_map.tsv → cusip_ticker_map + holdings updated.
5. Re-run Module 3 → universe now has ticker populated for those rows; ticker_is_verified=True.
```

Workflow is optional per-quarter; skipping it just leaves the null-ticker rows bucketed on CUSIP in the universe, which Modules 4+ handle gracefully (see Downstream contract above).

---

## CLI integration

### Entry script: `scripts/3_build_universe.py`

```
python scripts/3_build_universe.py                           # auto-latest quarter
python scripts/3_build_universe.py --quarter 2025Q4          # explicit quarter
python scripts/3_build_universe.py --quarter 2025Q4 --force  # overwrite audit files even if unchanged
```

### `run_2_Funds_parser.bat` chain

Module 3 runs chained after Module 2's ingest + report, gated by a user prompt:

```bat
REM ... existing Module 2 ingest + report steps ...

echo.
echo === Module 2 complete. ===
echo.
set /p RUN_M3="Proceed to Module 3 (build universe)? [y/N]: "
if /i "%RUN_M3%"=="y" (
    python 2_Funds_parser\scripts\3_build_universe.py
    if errorlevel 1 goto :error
    echo.
    set /p RUN_LIST="Run list_unresolved_cusips.py to surface ticker gaps? [y/N]: "
    if /i "%RUN_LIST%"=="y" (
        python 2_Funds_parser\scripts\list_unresolved_cusips.py
    )
)
```

Default quarter = Module 1's `auto-latest`. `apply_manual_ticker_mappings.py` is **not** chained into the .bat — the user runs it manually after populating the mapping file.

---

## Quarter-end date derivation

Exposed as a helper in `src/module_1/quarter.py` (new function) and imported by Module 3:

```python
def quarter_to_date_end(quarter: str) -> str:
    """'2025Q4' -> '2025-12-31'."""

def prior_quarter(quarter: str) -> str:
    """'2026Q1' -> '2025Q4'."""
```

| Quarter | `period_of_report` (quarter-end) |
|---------|-----------------------------------|
| Q1      | `YYYY-03-31`                      |
| Q2      | `YYYY-06-30`                      |
| Q3      | `YYYY-09-30`                      |
| Q4      | `YYYY-12-31`                      |

---

## Function signatures

```python
# src/module_3/bridge.py

def classify_share_type(
    title_of_class: str | None,
    put_call: str | None,
    rules: list[dict],
) -> str:
    """Return one of:
       common | prefunded_warrant | regular_warrant |
       put | call | preferred | debt | unknown."""


def load_share_type_rules(path: Path) -> list[dict]:
    """Parse config/share_types.yaml. Validates each rule has `match` (str)
       and `classify_as` (one of the 8 share-class literals)."""


def load_holdings_for_period(
    conn: sqlite3.Connection,
    db_schema: DbSchemaConfig,
    period_of_report: str,
) -> pd.DataFrame:
    """Amendment-deduplicated per-fund holdings for one quarter-end."""


def aggregate_fund_positions(
    holdings_df: pd.DataFrame,
    rules: list[ClassificationRule],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (fund_positions, dropped_rows, unresolved_positions, flags_by_key).
       fund_positions is per-(fund_id, position_key).
       flags_by_key is cross-fund any() keyed on position_key — carries
       has_options / has_regular_warrants signal even when a fund holds
       ONLY a dropped-class row (no kept common to anchor the flag to)."""


def aggregate_per_ticker(
    current_fund_positions: pd.DataFrame,
    prior_fund_positions: pd.DataFrame | None,
    quarter: str,
    current_flags_by_key: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Across-fund aggregation with QoQ metrics. Pass current_flags_by_key
    so options/warrant signals propagate across funds when the flag's fund
    has no kept common on the same ticker."""


def write_outputs(
    universe: pd.DataFrame,
    dropped: pd.DataFrame,
    unresolved: pd.DataFrame,
    quarter: str,
    paths: PathsConfig,
) -> None:
    """Write the three Parquets with deterministic column order."""


def build_universe(
    config: PipelineConfig,
    quarter: str | None = None,
) -> pd.DataFrame:
    """End-to-end orchestrator. Resolves quarter, loads current + prior
       holdings, classifies, aggregates, writes all three Parquet files,
       returns the universe DataFrame."""
```

### Module layout

```
2_Funds_parser/
├── config/
│   ├── pipeline.yaml
│   └── share_types.yaml                    (new)
├── src/
│   └── module_3/
│       ├── __init__.py
│       └── bridge.py
└── scripts/
    ├── 3_build_universe.py                 (new; Module 3 entry)
    ├── list_unresolved_cusips.py           (new; Module 2 companion)
    └── apply_manual_ticker_mappings.py     (new; Module 2 companion)
```

---

## Caching

- **Idempotent by overwrite.** No hash-based cache in v1 (aggregation on 77K rows is sub-second).
- Inputs are the DB + the YAML; DB changes whenever Module 2 re-ingests; YAML changes are rare and cheap to re-aggregate through.
- Revisit only if aggregation exceeds ~5 seconds on real data.

---

## Failure modes

| Condition                                              | Behavior                                                |
|--------------------------------------------------------|---------------------------------------------------------|
| `holdings` missing `title_of_class` or `put_call` col  | Fatal; message points to Module 2 schema extension      |
| `share_types.yaml` missing                             | Fatal; `ConfigError` with path                          |
| `share_types.yaml` malformed (bad YAML)                | Fatal; `ConfigError` with file:line (Module 1 pattern)  |
| `share_types.yaml` rule has unknown `classify_as`      | Fatal at load time; message lists valid values          |
| No holdings for target quarter (`period_of_report`)    | Write empty universe Parquet; log warning; exit 0       |
| No prior-quarter holdings                              | QoQ columns null; log once; proceed                     |
| 100% of rows classify as `unknown`                     | Fatal (schema drift suspected); message cites first 3 distinct `title_of_class` values |
| Ticker AND cusip both null in a row                    | Fatal (data integrity violation; Module 2's unique key should prevent this) |
| Amendment dedup yields zero rows                       | Fatal (invariant violation)                             |
| Output Parquet directory missing                       | Create via `ensure_dir` helper, do not fail            |

---

## Acceptance tests

1. **Happy-path aggregation.** Given synthetic two-fund, three-ticker universe where fund A holds 100 shares of TICKER1 and fund B holds 200 shares of TICKER1: output has `fund_count=2`, `total_shares=300`.
2. **Put/call drop.** Position with `put_call='Put'` appears in `dropped_rows_*.parquet` with `drop_reason='put'` and is absent from `universe_*.parquet`. `has_options=True` on the corresponding ticker row in the universe.
3. **Pre-funded warrant aggregation.** Fund holds 100 `COM` + 50 `PRE-FUND WT` of the same CUSIP → per-fund `shares_combined=150`, `has_prefunded_warrants=True`.
4. **Regular warrant drop.** 50 `WT` appears in `dropped_rows_*.parquet` with `drop_reason='regular_warrant'`; `has_regular_warrants=True`; 50 shares NOT counted in `shares_combined`.
5. **SPAC unit drop.** Row with `title_of_class='UNIT 99/99/9999'` → `drop_reason='regular_warrant'` (per rule "UNIT"), dropped from universe.
6. **QoQ metrics.** Two-quarter synthetic: ticker absent last quarter → `new_positions` includes that fund; ticker with fewer shares than prior → that fund counted in `decreased_positions`; ticker held last quarter but not this one → `exited_positions` counts it, `fund_count` does not.
7. **Amendment dedup.** Two filings for `(fund_id, period_of_report)=('X','2025-12-31')` with different `filing_date` → only the latest `filing_date`'s rows contribute.
8. **Unknown class retention.** Row with `title_of_class='EXOTIC RIGHT 2028'` (no rule match) appears in `unresolved_positions_*.parquet` AND is retained in the universe; the universe row has `has_unknown_class=True`.
9. **Case-insensitive classification.** Rows with `title_of_class` in `{'COM', 'Common', 'COMMON STOCK', 'com'}` all classify as `common`.
10. **`ticker_is_verified` logic.** Ticker held by one fund with `ticker_source='openfigi'` and two funds with `ticker_source='sec_name'` → `ticker_is_verified=True`. Ticker held only by funds with `ticker_source='sec_name'` → `ticker_is_verified=False`.
11. **Ticker-null fallback.** Row with `ticker=NULL, cusip='12345X'` ends up in the universe with `ticker=None, cusip='12345X'`, sortable, no error.
12. **Determinism.** Two consecutive runs on the same DB snapshot produce byte-identical Parquet files (same columns, same order, same hash).
13. **Empty-quarter graceful exit.** Request `--quarter 2100Q1` (future, no data) → writes empty universe Parquet, logs warning, exits 0.

---

## Future requirement — historical-quarter re-runs (flagged, not scoped)

Module 7 (Outcome Tracking) and the feedback-loop features will need Module 3 re-run against historical `period_of_report` values to regenerate back-test universes. The `--quarter YYYYQn` CLI flag already covers mechanics.

**Implications to track in Module 7 spec:**
- `share_types.yaml` is NOT versioned per quarter — historical re-runs use today's taxonomy. This is intentional (unified taxonomy across back-tests) but must be documented.
- Output filenames include `{quarter}`, so historical re-runs don't collide with current-quarter output.
- `cusip_ticker_map` is not versioned by quarter — tickers resolved today apply retroactively. Edge cases (ticker reassignments, issuer mergers) flagged for future investigation.
- No Module 3 code changes required; flag carried forward.

---

## Implementation order

1. **`config/share_types.yaml`** — author the rules file with the taxonomy above.
2. **`src/module_1/quarter.py` helpers** — add `quarter_to_date_end` and `prior_quarter`.
3. **`src/module_3/bridge.py`** — implement the 6 pure functions in order (classify → load → aggregate_fund → aggregate_ticker → write → build_universe).
4. **`scripts/3_build_universe.py`** — thin CLI entry around `build_universe`.
5. **Smoke test** — run against current DB (2025Q4 auto-latest), inspect outputs.
6. **Companion scripts** — `list_unresolved_cusips.py` + `apply_manual_ticker_mappings.py`.
7. **`run_2_Funds_parser.bat` chaining** — add the `set /p` prompt and Module 3 invocation.
8. **Spec + decisions update** — record calibrations, verified acceptance tests, observed `unknown` rate.
