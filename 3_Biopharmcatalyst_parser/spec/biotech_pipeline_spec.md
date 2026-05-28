# Biotech Catalyst Pipeline — Technical Specification

**Version:** 1.0
**Scope of this document:** Ingest + timing layer (Modules 0–5). Downstream modules (scoring, deep-dive, dashboard) are previewed in §12 for architectural context but not fully specified yet.

---

## 1. Overview

### 1.1 Purpose

An automated, locally-runnable pipeline that:

1. Ingests Biopharmacatalyst (referred below as BPC) FDA catalyst CSV downloads (weekly cadence) into a versioned local store, preserving every historical snapshot.
2. Ingests SEC EDGAR Form 4 (insider transactions) and Schedule 13D/13G (5%+ ownership) filings for selected tickers
3. Optionally cross-references with manually-extracted BPC insider data.
4. Derives a normalized `(date_min, date_max, precision_tier)` for every catalyst, resolving the BPC date-bucketing problem so downstream code can apply true window filters.
5. Produces a clean, queryable SQLite database that downstream modules (scoring, Claude-API deep dive, dashboard) read from.

### 1.2 Tech stack

- **Language:** Python 3.11+
- **Storage:** SQLite (single file `data/biotech.db`), accessed via `sqlite3` stdlib or `sqlalchemy` (implementer's choice)
- **Schema validation:** `pydantic` v2 with strict mode
- **HTTP:** `requests` with explicit `User-Agent` header for SEC endpoints
- **XML parsing:** `xml.etree.ElementTree` (stdlib) for Form 4
- **HTML parsing:** `beautifulsoup4` + `lxml` for 13D/G metadata
- **CLI:** `argparse` or `click` (implementer's choice)
- **Testing:** `pytest`
- **Dependency management:** **shared repo venv** at `..\.venv\` (same one `2_Funds_parser/` uses). Dependencies tracked in the root `requirements.txt`. **No `pyproject.toml`, no `uv`/`poetry`** — keeps the new pipeline aligned with the existing project so a single `pip install -r requirements.txt` covers everything.

### 1.3 not used

### 1.4 not used

### 1.5 Module dependency graph

```
Module 0  (Project + DB schema)
  │
  ├── Module 1  (Catalyst CSV ingest)
  │     │
  │     ├──→ Module 5  (Timing extraction — depends only on Module 1)
  │     │
  │     └── feeds ticker universe to →
  │           Module 2  (EDGAR Form 4)
  │           Module 3  (EDGAR 13D/13G)
  │
  └── Module 4  (BPC insider supplement)              [optional]

Module 6  (Scoring & ranking) — joins catalyst_snapshots + catalyst_timing
            + v_executive_open_market_trades; consumes config/scoring.yaml
            (depends on Modules 1, 2, 4, 5)

Downstream (NOT in this spec):
  Module 7 (Claude API deep-dive)     — user-selected subset of Module 6 output
  Module 8 (Dashboard)                — renders Module 7 output
```

Each module is independently runnable and idempotent: re-running the same input produces the same database state.

### 1.6 General engineering rules

- **Idempotency:** every module must be safely re-runnable. Re-loading the same CSV / re-fetching the same filing / recomputing the same snapshot must not create duplicate rows or corrupt data.
- **Logging:** structured logging via stdlib `logging`. Every run writes a row to `ingest_log` (see §2.6) with row counts, errors, runtime.
- **Strict validation:** all input data passes through pydantic models with `strict=True`. Schema mismatches must raise a clear, actionable error before any DB write.
- **No silent data drops:** if a row fails validation, log it explicitly with the reason. Don't filter silently.
- **Atomic writes:** use SQLite transactions. A failed run leaves the DB in its prior consistent state.

### 1.7 Repo conventions (inherited from `2_Funds_parser/`)

Cross-cutting rules that govern how the pipeline is invoked, where files live, and what gets reused vs. duplicated. These are repo-wide; do not deviate without a spec rev.

- **Working directory for all commands:** `3_Biopharmcatalyst_parser/`. Scripts run against the shared venv with `PYTHONPATH=src`.
  ```bash
  # From the repo root (where .venv lives):
  PYTHONPATH=3_Biopharmcatalyst_parser/src .venv/Scripts/python.exe 3_Biopharmcatalyst_parser/scripts/<name>.py
  # From inside 3_Biopharmcatalyst_parser/:
  PYTHONPATH=src ../.venv/Scripts/python.exe scripts/<name>.py
  ```
- **Top-level script naming carve-out:** scripts under `3_Biopharmcatalyst_parser/scripts/` are prefixed `3_<module>_<verb>.py` — e.g. `3_0_init_db.py`, `3_1_ingest_catalysts.py`, `3_2_ingest_edgar_form4.py`, `3_3_ingest_edgar_13dg.py`, `3_4_ingest_bpc_insider.py`, `3_5_compute_timing.py`. This mirrors `2_Funds_parser`'s `2_*` / `4_*` / `4c_*` convention.
- **Python package naming rule:** packages under `src/` **cannot** start with a digit. Use `src/module_0/`, `src/module_1/`, … `src/module_5/`. (Same carve-out as `2_Funds_parser/src/module_4c/`, `src/module_7/`.)
- **Pipeline orchestrator:** `run_3_Biopharmcatalyst_parser.bat` chains M0 → M1 → M5 → M4 → M2 → M3 with y/N gates per step. Mirrors `2_Funds_parser/run_2_Funds_parser.bat`. Updated at every script-completion milestone (per repo memory `feedback_update_daily_runner`).
- **`.env`** lives at the repo root and is shared with `2_Funds_parser/`. Provides `EDGAR_USER_AGENT`, `ANTHROPIC_API_KEY`, `EDGAR_RATE_LIMIT_PER_SEC`. Do not duplicate per-project.
- **Output folder convention** (pipeline-wide, see memory `feedback_output_folder_convention`):
  - `_csv_source/` — user-supplied BPC CSV downloads (input). Past loads are archived under `_csv_source/archive/<snapshot_date>_<original_filename>`.
  - `data/` — durable cross-run cache: the single SQLite file `data/biotech.db`. `.db` is gitignored.
  - `Outputs/` — files the user opens themselves (HTML/XLSX reports, when added later).
  - `_intermediate_outputs/` — code-only intermediate artifacts (Parquet, JSON sidecars).
- **EDGAR code provenance (v1 = copy-paste, refactor later):** Form 4 XML parser is copy-pasted from `2_Funds_parser/src/module_4c/edgar_client.py`. The EDGAR rate limiter (`_EDGAR_LIMITER` in `2_Funds_parser/src/layer_1/edgar_13f.py`) and the ticker→CIK resolver pattern are similarly duplicated. Promotion to a shared `layer_1/edgar_form4.py` is **deferred** — touching `2_Funds_parser` is risky while it is in production use. Track the eventual refactor as a known limitation.

---

## 2. Module 0 — Bootstrap (docx→csv conversion + DB schema)

Module 0 has two sub-steps that always run at the top of the pipeline (idempotent, free):

### 2.0 Module 0a — `.docx` → `.csv` conversion

The user pastes BPC's website data table into a Word document. That `.docx` therefore contains a raw HTML `<table>` inside its paragraph stream — not a Word-native table. M0a parses that HTML and writes the 19-column CSV that M1's pydantic schema expects.

**Auto-mode (default behaviour from the orchestrator):** scan `_csv_source/*.docx` and, for any `.docx` whose sibling `.csv` is missing or older than the docx, convert it. The CLI:

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_convert_docx_to_csv.py            # auto-scan _csv_source/
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_convert_docx_to_csv.py --force    # rebuild every CSV from its docx
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_convert_docx_to_csv.py _csv_source/biotech_catalysts_v4.docx   # single-file mode
```

**Extraction rules** — for each `<tr>` in the table, take the 20 `<td>` cells and emit 19 CSV columns (the docx col-9 "Options" is dropped):

| CSV column | docx col | Source | Notes |
|---|---:|---|---|
| `Ticker` | 0 | blurred-text | |
| `Name` | 1 | blurred-text | |
| `Price` | 2 | blurred-text | e.g. `213.1200` (numeric string, 4 decimals from BPC) |
| `30 Day Price Change` | 3 | blurred-text, even indices | parse `"p,ts,p,ts,…"`; keep prices, join with `; ` |
| `Drug` | 4 | **blurred-text required** | visible text appends FDA badges ("FTD", "BTD", "ODD") and "View Clinical Trial Data" link text that pollute the PK |
| `NCT Number` | 5 | text | |
| `Indication` | 6 | text | |
| `Stage` | 7 | blurred-text | visible text shows human label ("PDUFA priority review"); attribute carries the canonical `phaseN` |
| `Status` | 8 | text | |
| (col 9 Options) | 9 | **dropped** | not in the M1 schema |
| `Next Catalyst` | 10 | text | |
| `Catalyst Date` | 11 | blurred-text | ISO `YYYY-MM-DD` (visible text shows DD/MM/YYYY ET) |
| `Catalyst` | 12 | **blurred-text required** | visible text is UI-truncated with `"… read more"`; attribute carries the full body |
| `Conference` | 13 | text | (no blurred-text attribute on this column) |
| `Historical LOA` | 14 | blurred-text | |
| `Historical POP` | 15 | blurred-text | |
| `Bullish or Bearish` | 16 | **special parser** | regex `Community NN% NN% NN%` → `"Bull X% / Neutral Y% / Bear Z%"`; defaults to `"Bull -% / Neutral -% / Bear -%"` on no match |
| `Market Cap` | 17 | blurred-text | integer string (e.g. `376538886012` not `"376.54B"`) |
| `Last Updated` | 18 | blurred-text | ISO `YYYY-MM-DD HH:MM:SS` |
| `No Of Shares` | 19 | blurred-text | integer string |

**Why `blurred-text` is canonical** — BPC's website uses this attribute on the inner `<div>` of each cell to carry the sort/copy/export value. The visible text contains UI noise (`$` prefixes, suffixes like " ET", screen-reader labels, FDA-badge tags, truncation ellipses). The attribute is consistently present on every cell that has a non-text canonical form.

**Idempotency and edit detection** — `auto_convert_directory()` compares mtimes:
- CSV missing → convert.
- CSV exists and `csv.mtime >= docx.mtime` → skip with `[INFO] skip … — CSV is up-to-date`.
- CSV older than docx (or `--force` passed) → reconvert.

**Acceptance criteria (verified on the live v3 + v4 docx)**:

| File | Rows | PK match vs prior manually-converted CSV |
|---|---:|:--:|
| `biotech_catalysts_v3.docx` | 600 | 572/572 PKs match (28 within-CSV dupes coalesce in M1, as before) |
| `biotech_catalysts_v4.docx` | 100 | 100/100 PKs match |

Field-level diffs against the prior manually-converted CSVs are limited to known-equivalent representations: ISO vs `DD/MM/YYYY` dates (M1 accepts both per D11), trailing decimal places on `Price` (parsed by `float()`), and ISO timestamps with seconds (M1 accepts `%Y-%m-%d %H:%M:%S` per D11).

### Module 0b — Database schema bootstrap

The schema is bootstrapped at first run by `db.py:initialize_db()`. The function is idempotent (uses `CREATE TABLE IF NOT EXISTS`). Tables are documented per-table in the subsections below.

### 2.1 `catalyst_snapshots`

Stores every row of every BPC catalyst CSV download, tagged by snapshot date.

```sql
CREATE TABLE catalyst_snapshots (
    snapshot_date         DATE    NOT NULL,
    ticker                TEXT    NOT NULL,
    drug                  TEXT    NOT NULL,
    nct_number            TEXT    NOT NULL,  -- '' when blank in source
    next_catalyst_type    TEXT    NOT NULL,  -- 'Interim Data', 'Initial Data', etc.
    name                  TEXT,
    price                 REAL,
    price_history_30d     TEXT,              -- raw semicolon-separated string preserved
    indication            TEXT,
    stage                 TEXT,              -- 'phase1', 'phase2', ...
    status                TEXT,
    catalyst_date         DATE,
    catalyst_text         TEXT,              -- the unstructured 'Catalyst' description column
    conference            TEXT,
    historical_loa        REAL,
    historical_pop        REAL,
    sentiment             TEXT,              -- the 'Bullish or Bearish' column verbatim
    market_cap_usd        REAL,
    no_of_shares          INTEGER,
    bpc_last_updated      TIMESTAMP,
    PRIMARY KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
);

CREATE INDEX idx_catalyst_ticker     ON catalyst_snapshots (ticker);
CREATE INDEX idx_catalyst_date       ON catalyst_snapshots (catalyst_date);
CREATE INDEX idx_catalyst_latest     ON catalyst_snapshots (snapshot_date DESC, ticker);
```

**Key rationale:** `(snapshot_date, ticker, drug, nct_number, next_catalyst_type)` uniquely identifies "Company X's expected Phase 2 interim readout on Drug Y in indication Z, as observed on date D." When NCT is blank in the source CSV, normalize to `''` (not NULL) so the PK works.

### 2.2 `edgar_form4_filings`

One row per Form 4 filing (parent table).

```sql
CREATE TABLE edgar_form4_filings (
    accession_number      TEXT    PRIMARY KEY,  -- e.g. '0001127602-26-012345'
    cik_issuer            TEXT    NOT NULL,     -- zero-padded 10-digit
    ticker                TEXT,                  -- resolved at fetch time
    issuer_name           TEXT,
    reporting_owner_cik   TEXT,
    reporting_owner_name  TEXT,
    is_director           BOOLEAN,
    is_officer            BOOLEAN,
    is_ten_percent_owner  BOOLEAN,
    officer_title         TEXT,
    filed_date            DATE    NOT NULL,
    fetched_at            TIMESTAMP NOT NULL
);

CREATE INDEX idx_form4_ticker_date ON edgar_form4_filings (ticker, filed_date DESC);
CREATE INDEX idx_form4_cik         ON edgar_form4_filings (cik_issuer);
```

### 2.3 `edgar_form4_transactions`

One row per non-derivative transaction within a Form 4 filing.

```sql
CREATE TABLE edgar_form4_transactions (
    transaction_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number      TEXT    NOT NULL,
    transaction_date      DATE    NOT NULL,
    transaction_code      TEXT    NOT NULL,     -- 'P', 'S', 'A', 'M', 'F', 'D', etc.
    transaction_code_meaning TEXT,              -- denormalized human-readable
    acquired_disposed     TEXT,                  -- 'A' or 'D'
    shares                REAL,
    price_per_share       REAL,
    shares_owned_following INTEGER,
    is_open_market        BOOLEAN,              -- TRUE iff code IN ('P','S')
    direct_or_indirect    TEXT,                  -- 'D' or 'I'
    FOREIGN KEY (accession_number) REFERENCES edgar_form4_filings(accession_number)
);

CREATE INDEX idx_txn_accession   ON edgar_form4_transactions (accession_number);
CREATE INDEX idx_txn_code_date   ON edgar_form4_transactions (transaction_code, transaction_date DESC);
```

### 2.4 `edgar_ownership_filings` (13D/13G)

Metadata-only in v1; full HTML parsing deferred to a later milestone.

```sql
CREATE TABLE edgar_ownership_filings (
    accession_number      TEXT    PRIMARY KEY,
    cik_issuer            TEXT    NOT NULL,
    ticker                TEXT,
    issuer_name           TEXT,
    form_type             TEXT    NOT NULL,     -- 'SC 13D', 'SC 13G', 'SC 13D/A', 'SC 13G/A'
    filed_date            DATE    NOT NULL,
    filer_name            TEXT,                  -- best-effort from filing index
    filing_url            TEXT    NOT NULL,
    percent_of_class      REAL,                  -- NULL in v1; populated when HTML parser added
    fetched_at            TIMESTAMP NOT NULL
);

CREATE INDEX idx_ownership_ticker_date ON edgar_ownership_filings (ticker, filed_date DESC);
```

### 2.5 `bpc_insider_supplement`

Manually-extracted BPC insider CSV, kept separate from EDGAR-derived data.

```sql
CREATE TABLE bpc_insider_supplement (
    snapshot_date         DATE    NOT NULL,
    ticker                TEXT    NOT NULL,
    name                  TEXT,
    insider_name          TEXT    NOT NULL,
    insider_position      TEXT,
    filing_date           DATE    NOT NULL,
    buy_sell              TEXT    NOT NULL,
    stock_or_option       TEXT    NOT NULL,
    shares                REAL,
    shares_change_pct     REAL,
    trade_price           REAL,
    cost                  REAL,
    final_shares          INTEGER NOT NULL,
    no_of_shares          INTEGER,
    PRIMARY KEY (snapshot_date, ticker, insider_name, filing_date, buy_sell,
                 stock_or_option, shares, final_shares)
);
```

### 2.6 `ingest_log`

Audit table written by every ingest or processing run.

```sql
CREATE TABLE ingest_log (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module          TEXT    NOT NULL,           -- 'catalysts', 'edgar_form4', 'compute_timing', etc.
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          TEXT    NOT NULL,           -- 'success', 'partial', 'failed'
    input_ref       TEXT,                       -- CSV filename, or ticker list summary, or snapshot_date
    rows_in         INTEGER,
    rows_inserted   INTEGER,
    rows_updated    INTEGER,
    rows_rejected   INTEGER,
    error_message   TEXT
);
```

### 2.7 `ticker_cik_map`

Cached ticker → CIK mapping. Refreshed by Module 2 weekly.

```sql
CREATE TABLE ticker_cik_map (
    ticker      TEXT    PRIMARY KEY,
    cik         TEXT    NOT NULL,               -- zero-padded 10-digit
    name        TEXT,
    last_refreshed TIMESTAMP NOT NULL
);
```

### 2.8 `catalyst_timing`

Output of Module 5. One row per `catalyst_snapshots` row.

```sql
CREATE TABLE catalyst_timing (
    snapshot_date         DATE    NOT NULL,
    ticker                TEXT    NOT NULL,
    drug                  TEXT    NOT NULL,
    nct_number            TEXT    NOT NULL,
    next_catalyst_type    TEXT    NOT NULL,
    date_min              DATE,                  -- NULL only when precision_tier='unknown'
    date_max              DATE,                  -- NULL only when precision_tier='unknown'
    precision_tier        TEXT    NOT NULL,      -- see §7.3
    source_lane           TEXT    NOT NULL,      -- 'conference' | 'catalyst_date_specific' | 'text_parse' | 'catalyst_date_bucket' | 'unknown'
    matched_phrase        TEXT,                  -- exact substring that produced the match (audit)
    computed_at           TIMESTAMP NOT NULL,
    rules_version         TEXT    NOT NULL,      -- e.g. 'v1.0' — bumped when regex/rules change
    PRIMARY KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type),
    FOREIGN KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        REFERENCES catalyst_snapshots(snapshot_date, ticker, drug, nct_number, next_catalyst_type)
);

CREATE INDEX idx_timing_dates ON catalyst_timing (date_min, date_max);
CREATE INDEX idx_timing_tier  ON catalyst_timing (precision_tier);
```

### 2.9 Acceptance criteria (Module 0)

- Running `PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_init_db.py` from `3_Biopharmcatalyst_parser/` on an empty directory creates `data/biotech.db` with all tables.
- Re-running `3_0_init_db.py` is a no-op (no errors, no schema changes).
- `PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_db.py` verifies all tables exist with correct columns and primary keys.

---

## 3. Module 1 — Catalyst CSV Ingest

### 3.1 Purpose

Load a single BPC FDA-calendar CSV download into `catalyst_snapshots`, tagged with today's date (or a user-supplied date).

### 3.2 CLI

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_1_ingest_catalysts.py <csv_path> [--snapshot-date YYYY-MM-DD]
```

- Default `<csv_path>` is the most recent `*.csv` under `_csv_source/` (the input folder) when omitted; an explicit path always wins.
- If `--snapshot-date` omitted, use today (UTC date).
- After load, the CSV is copied to `_csv_source/archive/<snapshot_date>_<original_filename>` so we always have the raw artifact. The `_csv_source/archive/` subfolder is created on first run.

### 3.3 Expected CSV columns (strict)

The validator must require exactly these columns (case-sensitive), in any order:

```
Ticker, Name, Price, 30 Day Price Change, Drug, NCT Number, Indication,
Stage, Status, Next Catalyst, Catalyst Date, Catalyst, Conference,
Historical LOA, Historical POP, Bullish or Bearish, Market Cap,
Last Updated, No Of Shares
```

If any column is missing or any unexpected column is present, fail with a clear error listing the diffs. Do not write anything.

### 3.4 Field-level validation (pydantic, strict mode)

| CSV column | DB column | Rule |
|---|---|---|
| `Ticker` | `ticker` | required, uppercase, strip whitespace |
| `Name` | `name` | nullable |
| `Price` | `price` | float; allow blanks → NULL |
| `30 Day Price Change` | `price_history_30d` | preserved as raw string |
| `Drug` | `drug` | required; empty → `''` |
| `NCT Number` | `nct_number` | empty → `''` (never NULL) |
| `Indication` | `indication` | nullable |
| `Stage` | `stage` | must match `^phase[0-5]$`; else reject row, log warning |
| `Status` | `status` | nullable |
| `Next Catalyst` | `next_catalyst_type` | empty → `''` (real CSV has ~45 blank rows — big-pharma pipeline trackers without a specific next-event tag). PK uniqueness still holds via the other four components. See decisions.md D2. |
| `Catalyst Date` | `catalyst_date` | parse `DD/MM/YYYY` → ISO date; if unparseable → NULL + warn |
| `Catalyst` | `catalyst_text` | preserved as-is |
| `Conference` | `conference` | nullable |
| `Historical LOA` | `historical_loa` | float 0–100; blanks → NULL. **Blank sentinels** treated as NULL: `''`, em dash `—` (U+2014, BPC's "not applicable" for big-pharma rows), ASCII `-`, `n/a`/`N/A`/`NA`. |
| `Historical POP` | `historical_pop` | float 0–100; blanks → NULL (same sentinel set as Historical LOA). |
| `Bullish or Bearish` | `sentiment` | preserved as-is |
| `Market Cap` | `market_cap_usd` | parse scientific notation (`5.82485E+11`) → float |
| `Last Updated` | `bpc_last_updated` | parse `DD/MM/YYYY HH:MM` → ISO timestamp |
| `No Of Shares` | `no_of_shares` | int; blanks → NULL |

### 3.5 Idempotency

Use `INSERT OR REPLACE` keyed on `(snapshot_date, ticker, drug, nct_number, next_catalyst_type)`. Re-running the same CSV with the same snapshot date is a safe no-op (overwrites identical rows).

### 3.6 Acceptance criteria

- Loading `_csv_source/biotech_catalysts_v3.csv` (the current reference file) with snapshot date `2026-05-27` reports **`rows_in=600`, `rows_inserted=572`, `rows_updated=28`, `rows_rejected=0`**, ending in `catalyst_snapshots` with **572 distinct rows**.
  - The 28-row delta is **within-CSV duplicates** in the BPC source data — identical `(ticker, drug, nct_number, next_catalyst_type)` tuples appearing twice (e.g., MNKD-Afrezza and NUVL-Neladalkib both appear at rows 18+28 and 21+27 respectively). The composite PK correctly dedups them; the second occurrence in CSV order counts as an "update" because the first has already populated the PK. See decisions.md D2.
- Re-running the same command reports `rows_in=600`, `rows_inserted=0`, `rows_updated=600`, `rows_rejected=0`. DB row count stays at 572.
- Loading a CSV with a missing column raises `SchemaValidationError` listing the missing column, and writes 0 rows.
- Loading a CSV with `Stage = "phase99"` rejects that row, logs a warning with row number and ticker, and inserts the remaining valid rows.
- `ingest_log` row written with row counts.
- Original CSV copied to archive folder.

### 3.7 Edge cases to handle

- **Blank `NCT Number`:** common for regulatory catalysts (PDUFA, submissions). Normalize to `''`.
- **Blank `Next Catalyst`:** ~45 of the 600 reference rows have it blank (e.g., big-pharma rows where BPC tracks the company's pipeline without committing to a specific imminent event). Normalize to `''`. PK collisions don't occur because `(snapshot_date, ticker, drug, nct_number)` is already discriminating.
- **BPC "N/A" sentinels in numeric columns:** Historical LOA/POP cells frequently contain the em dash `—` (U+2014, BPC's "not relevant" marker for tickers where historical-base-rate data doesn't apply). Treat the em dash plus a small fixed set (`-`, `n/a`/`N/A`/`NA`) as blank → NULL. Do not extend this set casually; sentinel proliferation is a data-quality smell.
- **Multiple rows per ticker:** common — a ticker can appear with different drugs / catalysts. The composite PK handles this.
- **Same drug, different next-catalyst-type:** also legitimate (e.g., a drug with both "Interim Data" and "Topline Data" expected at different dates).
- **Scientific-notation market cap:** `float(str(v).strip())` handles this cleanly (Python's float() accepts `5.82485E+11`).
- **Date format:** BPC uses DD/MM/YYYY (European). Unparseable dates → NULL + warn (not row-rejecting). If a high fraction of rows fail parsing, the source is likely US-format and the loader should be audited — current behaviour does not crash on a single mis-formatted row.

---

## 4. Module 2 — EDGAR Form 4 Insider Transactions

### 4.1 Purpose

For every ticker present in the most recent catalyst snapshot, fetch all Form 4 filings within a configurable lookback window (default 365 days) and parse the non-derivative transactions into `edgar_form4_transactions`.

### 4.2 CLI

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_2_ingest_edgar_form4.py [--lookback-days 365] [--tickers AAPL,BMY,...] [--full-refresh]
```

- Default: process all tickers from the latest `snapshot_date` in `catalyst_snapshots`.
- `--tickers`: explicit subset (override).
- `--full-refresh`: re-fetch all filings in window even if already in DB. Default is incremental (only fetch filings newer than the most recent `filed_date` already stored per CIK).

### 4.3 Sub-components

#### 4.3.1 Ticker → CIK resolver (`edgar/ticker_cik.py`)

- On first call (or if cache > 7 days old): download `https://www.sec.gov/files/company_tickers.json` and refresh `ticker_cik_map`.
- For unresolved tickers: log a warning, continue with remaining tickers.

#### 4.3.2 Rate-limited HTTP client (`edgar/client.py`)

- All SEC requests use a single `requests.Session` configured with:
  - `User-Agent: <EDGAR_USER_AGENT>` (from env)
  - `Accept: application/json` for JSON endpoints
- Token-bucket rate limit at `EDGAR_RATE_LIMIT_PER_SEC` (default 9.5/sec — matches `2_Funds_parser`'s setting; safely under SEC's 10/sec fair-use cap). Spec originally specified 5/sec; bumped per D5 update for cross-project consistency.
- Retry with exponential backoff on 429 and 5xx (max 3 retries).
- Hard fail on 403 (likely missing User-Agent).

#### 4.3.3 Filings index

For each CIK, fetch `https://data.sec.gov/submissions/CIK{padded_cik}.json` and walk the `filings.recent` arrays to find all Form 4 filings within the **per-ticker lookback window**.

**Per-ticker incremental floor (D5 optimization, 2026-05-27):** the window floor is not unconditionally `today - --lookback-days`. Instead:

- If `edgar_form4_filings` already has ≥1 row for the CIK → `since_floor = max(today - lookback_days, MAX(filed_date))`. This skips re-scanning months of filings we've already stored. Mode reported as `incremental` in per-ticker stats.
- If `edgar_form4_filings` has no rows for the CIK → `since_floor = today - lookback_days`. Full first-time window. Mode reported as `new`.
- If `--full-refresh` is passed → existing rows for the CIK are deleted first, then the floor is reset to the full window. Mode reported as `full_refresh`.

The user-supplied `--lookback-days` always bounds the outer window — `since_floor` is never older than that, regardless of how recently the ticker was last fetched. This keeps a tightened lookback meaningful and never silently looks further back than asked.

Filings beyond `recent` (older than ~1000 filings) are in paginated files — for biotech small-caps this is rarely an issue, but the implementation should handle the pagination case (out of scope for v1 if it complicates things; document as a known limitation).

#### 4.3.4 Form 4 XML parser (`src/module_2/form4_parser.py`)

**Provenance (v1):** copy-pasted from `2_Funds_parser/src/module_4c/edgar_client.py` (the M4c Form 4 path, already field-validated against the XSL prefix quirk). The EDGAR rate limiter and the `submissions` index walker are likewise duplicated rather than imported, per §1.7. Refactor to a shared `layer_1/edgar_form4.py` is deferred.

For each Form 4 accession, fetch the primary XML document and parse:

**Filing-level (one row per filing):**
- accession_number
- issuer CIK, name, trading symbol
- reporting owner CIK, name
- relationship flags (`isDirector`, `isOfficer`, `isTenPercentOwner`, `isOther`)
- `officerTitle` if present

**Non-derivative transactions (zero or more rows per filing):**
- transaction date
- transaction code (single letter)
- acquired/disposed code
- shares
- price per share
- shares owned following transaction
- direct/indirect ownership

Add a computed `transaction_code_meaning` per the SEC Form 4 instructions:

| Code | Meaning | `is_open_market` |
|---|---|---|
| P | Open-market or private purchase | TRUE |
| S | Open-market or private sale | TRUE |
| A | Grant/award | FALSE |
| M | Exercise of derivative (option exercise) | FALSE |
| F | Tax withholding | FALSE |
| D | Disposition to issuer | FALSE |
| G | Bona fide gift | FALSE |
| X | Exercise of in-the-money derivative | FALSE |
| C | Conversion of derivative | FALSE |
| (other) | "Other" | FALSE |

Document the full list in a constants module; the table above is the v1 minimum.

### 4.4 Idempotency

- Filings keyed by `accession_number` (PK). `INSERT OR IGNORE` — if accession already in DB, skip the fetch+parse entirely.
- Transactions: parent filing's accession + autoincrement transaction_id. Because filings are skipped if already present, transactions are too.
- **Per-ticker incremental floor** (D5 optimization, §4.3.3): the submissions-list scan is also narrowed to filings filed after `MAX(filed_date)` for each CIK, so a re-run of a stable universe touches each ticker once (just the submissions JSON) without re-iterating already-stored filings client-side.
- `--full-refresh` deletes existing rows for the targeted CIKs/accessions before re-loading, which resets the incremental floor back to the full window.

### 4.5 Acceptance criteria

- Running with `--tickers CRBP,DTIL,STTK,TRDA,VSTM` (the five validated overlap tickers) populates `edgar_form4_filings` and `edgar_form4_transactions` with at least one filing each.
- A row in `edgar_form4_transactions` with `transaction_code = 'P'` and `is_open_market = TRUE` is correctly flagged.
- A row in `edgar_form4_transactions` for an option exercise (`transaction_code = 'M'`) has `is_open_market = FALSE`.
- Re-running the same command performs incremental fetch only; no duplicate transactions inserted.
- `ingest_log` row written with row counts and runtime.
- A test fixture (a real Form 4 XML saved to `tests/fixtures/`) is parsed correctly offline by `pytest`.

### 4.6 Edge cases

- **No filings in window:** valid result; log info, write 0 rows.
- **Ticker not in `ticker_cik_map`:** log warning, skip ticker, continue.
- **Form 4 with derivative-only transactions:** filing row written, zero transaction rows. This is correct.
- **Form 4/A (amended):** treat as a separate filing keyed by its own accession. Don't try to dedupe against the original; downstream code can filter on `filed_date DESC` if it wants latest-only.
- **Indirect ownership through trusts/family:** captured by `direct_or_indirect` field. Don't filter out.

---

## 5. Module 3 — EDGAR 13D/13G Ownership Filings

### 5.1 Purpose

Track when significant holders (5%+ stakes) initiate, increase, or exit positions in catalyst-universe tickers. v1 stores filing metadata only; HTML parsing of percentage-of-class deferred.

### 5.2 CLI

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_3_ingest_edgar_13dg.py [--lookback-days 365] [--tickers ...]
```

### 5.3 Behavior

For each CIK, walk `submissions` to find filings where `form` matches **any of the 8 ownership-filing form names**:

- Modern format: `SC 13D`, `SC 13G`, `SC 13D/A`, `SC 13G/A`
- Older/alternative format: `SCHEDULE 13D`, `SCHEDULE 13G`, `SCHEDULE 13D/A`, `SCHEDULE 13G/A`

Both formats coexist inside the same CIK's `recent[]` array — they are not era-stratified. Pfizer's most recent 6 ownership filings (2026-Q1) are all `SCHEDULE 13X` format; its older filings (2022–2024) are `SC 13X`. Filtering only on the `SC` variants silently drops a large fraction of real filings (calibration finding — see decisions.md D6 update 2026-05-27).

For each match, write a row to `edgar_ownership_filings`:

- `filer_name`: best-effort extraction from the filing index page (`https://www.sec.gov/cgi-bin/browse-edgar?...`) or the filing's `-index.htm`. If unable, leave NULL but still record the filing.
- `filing_url`: direct link to the filing's primary document.
- `percent_of_class`: NULL in v1.

### 5.4 Acceptance criteria

- Running on the catalyst universe populates `edgar_ownership_filings` with all 13D/G filings in the lookback window for resolved tickers.
- Each row has a working `filing_url`.
- Idempotent: re-running is a safe no-op via `INSERT OR IGNORE` on `accession_number`.

### 5.5 Future work (not v1)

- Parse the filer name reliably from the HTML.
- Parse the percent-of-class from the form's "Item 11" or summary table.
- Distinguish initial filings (13D/G) from amendments (13D/A, 13G/A) and from changes that signal exits (typically a 13G/A reporting <5%).

---

## 6. Module 4 — BPC Insider Supplement

### 6.1 Purpose

Manual-cadence loader for BPC's pre-cleaned insider trading CSV (the file Andre extracts by hand). Kept separate from EDGAR-derived data so disagreements are auditable.

### 6.2 CLI

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_4_ingest_bpc_insider.py <csv_path> [--snapshot-date YYYY-MM-DD]
```

- Default `<csv_path>` is the most recent `*insider*.csv` under `_csv_source/` when omitted.
- Archive copy goes to `_csv_source/archive/<snapshot_date>_<original_filename>` (same convention as Module 1).

### 6.3 Expected CSV columns (strict)

```
Ticker, Name, Insider Name, Insider Position, Filing Date, Buy/Sell,
Stock/Option, Shares, Shares Change, Trade Price, Cost, Final Share, No Of Shares
```

Schema validation identical philosophy to Module 1: missing or extra columns → hard fail.

### 6.4 Field mapping

| CSV column | DB column | Rule |
|---|---|---|
| `Ticker` | `ticker` | uppercase, strip |
| `Name` | `name` | nullable |
| `Insider Name` | `insider_name` | required |
| `Insider Position` | `insider_position` | nullable |
| `Filing Date` | `filing_date` | parse `YYYY-MM-DD` |
| `Buy/Sell` | `buy_sell` | must be `Buy` or `Sell` |
| `Stock/Option` | `stock_or_option` | must be `Stock` or `Option` |
| `Shares` | `shares` | float |
| `Shares Change` | `shares_change_pct` | float (already a % in source) |
| `Trade Price` | `trade_price` | float; 0 allowed (option exercises) |
| `Cost` | `cost` | float |
| `Final Share` | `final_shares` | int |
| `No Of Shares` | `no_of_shares` | int |

### 6.5 Idempotency

`INSERT OR REPLACE` on the composite **8-column** PK
`(snapshot_date, ticker, insider_name, filing_date, buy_sell, stock_or_option, shares, final_shares)`. Re-loading the same file with the same snapshot date is a no-op.

`final_shares` is in the PK (calibration per D4) because the BPC source data contains legitimate same-day partial fills — same insider buying or selling the same number of shares twice on the same day, ending at different post-trade positions. Without `final_shares` in the PK those rows silently coalesce.

### 6.6 Acceptance criteria

- Loading `_csv_source/insider_data3.csv` (the current reference file, dated 2026-05-27) with snapshot `2026-05-27` reports `rows_in=1608, rows_inserted=1556, rows_updated=52, rows_rejected=0`, ending in `bpc_insider_supplement` with **1,556 distinct rows**.
  - The 52-row delta is **true within-CSV duplicates** in the BPC source (bit-for-bit identical across all 14 columns). 7 *additional* buckets where rows shared the 7-column-PK key but differed in `final_shares` are correctly preserved as distinct rows now that `final_shares` is in the PK.
- Re-running the same command reports `rows_in=1608, rows_inserted=0, rows_updated=1608, rows_rejected=0`. DB row count stays at 1,556.
- A diff query (see §6.7) returns disagreements between this table and EDGAR Form 4 for the same ticker+insider+date.

**Reference-file note:** the spec was originally drafted against a 696-row `insider_data.csv`; the current BPC pull is larger (`insider_data3.csv`, 1,608 rows). Update this count when a newer reference file replaces it.

### 6.7 Cross-validation views

Create two SQL views in `db.py` for downstream use:

- `v_latest_catalysts`: most recent snapshot per `(ticker, drug, nct_number, next_catalyst_type)`.
- `v_insider_signal_combined`: union of `edgar_form4_transactions` (open-market only) and `bpc_insider_supplement` (stock only), with a `source` column = `'edgar'` or `'bpc'`.

---

## 7. Module 5 — Catalyst Timing Extraction

### 7.1 Purpose

For every row in `catalyst_snapshots`, derive a `(date_min, date_max, precision_tier)` tuple and persist it to `catalyst_timing`. Resolves the BPC date-bucketing problem: most catalysts in the source CSV are dated at quarter-ends as placeholders (`31/12/2026`, `30/06/2026`, `30/09/2026`, `31/08/2026`), and the actual timing must be recovered from the `Catalyst` text column or the `Conference` column.

Downstream filtering in Module 6 will use these fields to test whether a catalyst falls in the **discovery window** (T+14 to T+180 days) or the narrower **execution window** (T+14 to T+60 days), via standard range-overlap logic.

### 7.2 The two windows (locked)

| Window | Range | Used for |
|---|---|---|
| **Discovery** | today + 14 to today + 180 days | Research / watchlist / pre-position scouting |
| **Execution** | today + 14 to today + 60 days | Deploy-capital candidates |

The 14-day lower bound is deliberate: within ~2 weeks of a binary readout, IV crush and binary risk dominate the setup. Catalysts inside T+14 are not filtered out but should be flagged separately as "imminent — IV regime."

**Range overlap test** (the actual filter logic, implemented in Module 6):
```
discovery: date_min <= today+180 AND date_max >= today+14
execution: date_min <=  today+60 AND date_max >= today+14
```

Module 5 only produces the date range. Window membership is computed at query time in Module 6.

### 7.3 The three lanes (locked) and precision tiers

Each catalyst row is assigned to **exactly one** lane, in this priority order. The first lane that produces a result wins.

| Priority | Lane | Source | Typical precision tier |
|---|---|---|---|
| 1 | **Conference-tied** | `Conference` column has a parseable date range | `conference` (1–7 day window) |
| 2 | **Specific company-disclosed date** | `Catalyst Date` is a real date, not a known BPC placeholder | `specific` (single day) |
| 3 | **Text-parsed** | Regex extraction from `Catalyst` text column | `month` / `quarter` / `half` / `year` |
| 3b | **Bucket fallback** | `Catalyst Date` is a known BPC placeholder and text yields nothing | `quarter` / `half` / `year` (from bucket) |
| — | **Unknown** | All lanes fail | `unknown` (excluded from windows) |

**Precision tiers**, highest to lowest. Module 6 may gate execution-window eligibility on this.

| Tier | Meaning | Range width |
|---|---|---|
| `specific` | Single day | 1 day |
| `conference` | Conference start–end | 1–7 days |
| `month` | A specific month | ~30 days |
| `quarter` | A specific quarter (Q1/Q2/Q3/Q4 or 1Q-4Q) | ~91 days |
| `half` | 1H or 2H of a year | ~183 days |
| `year` | Entire calendar year | 365 days |
| `unknown` | No timing extractable | NULL/NULL |

**Recommended Module 6 default:** include `{specific, conference, month, quarter}` in execution window; include all non-`unknown` tiers in discovery window.

### 7.4 Lane 1 — Conference-tied

**Trigger:** the `Conference` column is non-empty AND contains a parseable date range.

**Conference text format** (empirically observed from the BPC CSV):
```
European Hematology Association Congress (EHA26) 11/06/2026 ET - 14/06/2026 ET Conference Calendar
American Society of Clinical Oncology Conference (ASCO26) 29/05/2026 ET - 02/06/2026 ET Conference Calendar
```

**Extraction regex:**
```python
CONFERENCE_DATE_RANGE = re.compile(
    r'(\d{1,2}/\d{1,2}/\d{4})\s+ET\s*-\s*(\d{1,2}/\d{1,2}/\d{4})\s+ET'
)
```

If matched:
- `date_min` = parsed start date (DD/MM/YYYY)
- `date_max` = parsed end date (DD/MM/YYYY)
- `precision_tier` = `conference`
- `source_lane` = `conference`
- `matched_phrase` = the full matched substring

If `Conference` is populated but the regex fails: log a warning with the raw conference string and **fall through to Lane 2**. Don't fail the row.

**Lane 1 beats Lane 2 even when both are present.** BPC defaults the `Catalyst Date` to the conference's last day for conference-tied rows, but the actual presentation could be any day of the conference. The full window is more accurate than the false-specific date.

### 7.5 Lane 2 — Specific catalyst date

**Trigger:** `Catalyst Date` parses as a valid date AND is **not** in the known BPC placeholder set.

**Known BPC placeholder dates** (derived from inspection of `biotech_catalysts_v3.csv`: these dates are wildly over-represented relative to a natural distribution):

| Date pattern (MM-DD) | Implied meaning | Bucket → range (used by Lane 3b) |
|---|---|---|
| `12-31` | By year end / FY | full year |
| `06-30` | 1H | Jan 1 – Jun 30 |
| `03-31` | Q1 / by end of Q1 | Jan 1 – Mar 31 |
| `09-30` | Q3 / by end of Q3 | Jul 1 – Sep 30 |
| `08-31` | "By end of summer" (ad-hoc BPC bucket) | Jul 1 – Aug 31 |

Implementation: store as a constant set of `(month, day)` tuples in `BPC_PLACEHOLDER_DATES` in `timing_rules.py`. Document the rationale inline.

**Output** if `Catalyst Date` is a valid date AND `(month, day)` is **not** in `BPC_PLACEHOLDER_DATES`:
- `date_min` = `date_max` = the parsed date
- `precision_tier` = `specific`
- `source_lane` = `catalyst_date_specific`
- `matched_phrase` = NULL

### 7.6 Lane 3 — Text parsing

**Trigger:** Lanes 1 and 2 both fail.

**Algorithm:**

1. Extract **all** temporal references from `catalyst_text` using the regex set in §7.7.
2. Compute each match's `(date_min, date_max, precision_tier, phrase)`.
3. Filter to matches where `date_max >= today` (still in the future).
4. If any future matches remain: pick the one with the **smallest `date_min`** (most imminent). That's the result.
5. If no future matches: drop to §7.8 bucket fallback.

**Rationale for "earliest future":** catalyst text often mentions multiple events (e.g., "Phase 1 dosed May 13, 2025. Data due in 2H 2026, with proof-of-concept data in 2027"). The earliest still-future reference is almost always the next catalyst.

### 7.7 Regex pattern set (case-insensitive)

All patterns are **module-level constants** in `timing_rules.py` with named groups and inline docstring examples. Define a `RULES_VERSION = "v1.0"` constant; bump it when patterns change.

| Pattern | Matches | Range mapping | Tier |
|---|---|---|---|
| `SPECIFIC_DATE_LONG` | `May 24, 2026` / `May 24 2026` / `24 May 2026` | day = day | `specific` |
| `MONTH_YEAR` | `May 2026` / `in May 2026` | first–last day of month | `month` |
| `QUARTER` | `Q3 2026` / `3Q 2026` / `third quarter 2026` / `Q3/2026` | first–last day of quarter | `quarter` |
| `HALF` | `1H 2026` / `H1 2026` / `first half 2026` / `2H 2026` | Jan 1–Jun 30 or Jul 1–Dec 31 | `half` |
| `RELATIVE_YEAR` | `early 2026` / `mid 2026` / `late 2026` | early=Q1, mid=Apr–Sep (half), late=Q4 | `quarter` or `half` |
| `YEAR_END` | `YE 2026` / `year-end 2026` / `by end of 2026` / `FY 2026` | Jan 1–Dec 31 of that year | `year` |
| `YEAR_ONLY` | `in 2026` / `during 2026` / `expected 2026` (last resort) | Jan 1–Dec 31 | `year` |

**Reference implementation sketch** (final implementer should test against the fixtures in §7.10):

```python
RULES_VERSION = "v1.0"

MONTHS = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

PATTERNS = [
    # specific date: "May 24, 2026" or "24 May 2026"
    (re.compile(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2}),?\s+(\d{4})\b', re.I), 'specific'),
    (re.compile(r'\b(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})\b', re.I), 'specific'),
    # quarter: "Q3 2026", "3Q 2026"
    (re.compile(r'\bQ([1-4])\s*[/\-]?\s*(\d{4})\b', re.I), 'quarter'),
    (re.compile(r'\b([1-4])Q\s*[/\-]?\s*(\d{4})\b', re.I), 'quarter'),
    # half: "1H 2026", "2H 2026", "H1 2026"
    (re.compile(r'\b([1-2])H\s*[/\-]?\s*(\d{4})\b', re.I), 'half'),
    (re.compile(r'\bH([1-2])\s*[/\-]?\s*(\d{4})\b', re.I), 'half'),
    (re.compile(r'\b(first|second)\s+half\s+(?:of\s+)?(\d{4})\b', re.I), 'half'),
    # relative: "early/mid/late 2026"
    (re.compile(r'\b(early|mid|late)\s+(\d{4})\b', re.I), 'relative_year'),
    # year-end
    (re.compile(r'\b(?:YE|year[\s-]?end|FY|by\s+end\s+of)\s+(\d{4})\b', re.I), 'year_end'),
    # month-year: "May 2026"
    (re.compile(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{4})\b', re.I), 'month'),
    # year-only fallback
    (re.compile(r'\b(?:in|during|expected\s+in)\s+(\d{4})\b', re.I), 'year'),
]
```

**Pattern ordering note:** specific date patterns must be tried before `month` patterns, since "May 24, 2026" also matches "May 2026". The implementation should record which positions in the source text have already been claimed by a higher-precision match and skip overlapping matches at lower precision.

### 7.8 Bucket fallback (Lane 3b)

If text parsing returns no future matches AND `Catalyst Date` is a known placeholder, fall through to a bucket-implied range:

| Placeholder `Catalyst Date` | `date_min` | `date_max` | `precision_tier` |
|---|---|---|---|
| `YYYY-12-31` | `YYYY-01-01` | `YYYY-12-31` | `year` |
| `YYYY-06-30` | `YYYY-01-01` | `YYYY-06-30` | `half` |
| `YYYY-03-31` | `YYYY-01-01` | `YYYY-03-31` | `quarter` |
| `YYYY-09-30` | `YYYY-07-01` | `YYYY-09-30` | `quarter` |
| `YYYY-08-31` | `YYYY-07-01` | `YYYY-08-31` | `quarter` |

Result: `source_lane = 'catalyst_date_bucket'`, `matched_phrase = NULL`.

### 7.9 Final fallback (`unknown`)

If neither text nor placeholder yields anything: `precision_tier = 'unknown'`, `date_min = date_max = NULL`, `source_lane = 'unknown'`. Log info-level message with ticker + catalyst text snippet; downstream filtering excludes these.

**Decision (locked):** when a catalyst's `date_max` has already passed and BPC hasn't refreshed (the company missed its own guidance), the row falls through to `unknown`. Module 5 deliberately keeps timing clean; date-slip detection lives in Module 6, which can query `catalyst_snapshots` history directly.

### 7.10 Test fixtures (real rows from Andre's CSV)

Reference date for "future" filtering: **2026-05-27**. Encode as `pytest` parametrized cases in `tests/test_timing.py`.

| # | Catalyst text excerpt | Conference | Catalyst Date | Expected `date_min` | Expected `date_max` | Expected tier | Expected lane |
|---|---|---|---|---|---|---|---|
| 1 | "PDUFA date set for May 24, 2026" | — | 24/05/2026 | 2026-05-24 | 2026-05-24 | `specific` | `catalyst_date_specific` |
| 2 | (any row with EHA26 conference) | `... EHA26 11/06/2026 ET - 14/06/2026 ET ...` | 13/06/2026 | 2026-06-11 | 2026-06-14 | `conference` | `conference` |
| 3 | "Phase 1 SAD & MAD data due by YE 2026" | — | 31/12/2026 | 2026-01-01 | 2026-12-31 | `year` | `text_parse` |
| 4 | "Pilot trial topline results due in 2H 2026, with a pivotal trial due in early 2027" | — | 31/12/2026 | 2026-07-01 | 2026-12-31 | `half` | `text_parse` |
| 5 | "Phase 1 study ongoing with data expected 4Q 2026" | — | 31/12/2026 | 2026-10-01 | 2026-12-31 | `quarter` | `text_parse` |
| 6 | "First patient dosed May 13, 2025. Data due in 2026." | — | 31/12/2026 | 2026-01-01 | 2026-12-31 | `year` | `text_parse` |
| 7 | (Conference empty, text empty, placeholder `30/06/2026`) | — | 30/06/2026 | 2026-01-01 | 2026-06-30 | `half` | `catalyst_date_bucket` |
| 8 | "Topline data expected June 2026" | — | 30/06/2026 | 2026-06-01 | 2026-06-30 | `month` | `text_parse` |

### 7.11 CLI

```
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py [--snapshot-date YYYY-MM-DD] [--all-snapshots] [--rules-version-override v1.1]
```

- Default: process only the most recent `snapshot_date` in `catalyst_snapshots`.
- `--snapshot-date`: process one specific snapshot.
- `--all-snapshots`: recompute timing for every snapshot ever loaded. Use after a `RULES_VERSION` bump.
- `--rules-version-override`: lets the implementer pin a non-default version label, useful in dev.

**Idempotency:** `INSERT OR REPLACE` on the primary key. Re-running on the same snapshot is a safe no-op.

**Recommended invocation order** (chain after catalyst ingest):
```bash
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_1_ingest_catalysts.py <csv>
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py
```

`run_3_Biopharmcatalyst_parser.bat` chains these (with the y/N gate) so the user normally runs the .bat rather than the scripts directly. The CLI command is a separate explicit step (not auto-triggered inside `3_1_ingest_catalysts.py`) so failures are isolated and the rules version is auditable per run.

### 7.12 Recomputation policy

When the regex rules or bucket mapping change:

1. Bump `RULES_VERSION` (e.g., `v1.0` → `v1.1`) in `timing_rules.py`.
2. Run `compute-timing --all-snapshots`.
3. The `rules_version` column makes it easy to query "which rows were computed under which rules" for debugging.

### 7.13 Acceptance criteria

- `PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py` on the 572-row catalyst snapshot (after M1's dedup) produces 572 `catalyst_timing` rows with no exceptions.
- Distribution of `source_lane` against the 2026-05-27 reference snapshot:
  - `conference`: **153** rows (snapshot dated days before ASCO 2026 — naturally conference-heavy. Spec originally predicted ~30–60; updated to 100–200 to reflect the actual late-May/early-June cluster.)
  - `catalyst_date_specific`: **63** rows (PDUFA dates, M&A close dates, etc.)
  - `text_parse`: **351** rows (the bulk — quarter/half/year guidance extracted from `Catalyst` text)
  - `catalyst_date_bucket`: **5** rows (placeholder Catalyst Date and no text — the smallest residual)
  - `unknown`: **0** rows (target was <5%; calibrated rules clear it entirely)
- Re-running the command updates the same 572 rows in place, inserts 0.
- All 8 fixtures in §7.10 pass as pytest parametrized cases.
- A sanity query joining `catalyst_snapshots` to `catalyst_timing` and filtering to the discovery window returns a non-empty result.
- `ingest_log` row written with `module = 'compute_timing'` and accurate counts.

### 7.14 Edge cases

| Case | Handling |
|---|---|
| Multiple temporal refs in text | Pick earliest future (§7.6). |
| Text mentions a date that's already past | Filter out in §7.6 step 3. |
| Conference column populated but unparseable date range | Log warning, fall through to Lane 2. |
| `Catalyst Date` missing AND text empty | `unknown` tier, NULL dates. |
| `Catalyst Date` is a placeholder AND text yields a more-specific match | Text match wins (Lane 3, not 3b). |
| `Catalyst Date` is a specific date AND text mentions a different earlier date | `Catalyst Date` wins (Lane 2). The text is not authoritative against a specific company-disclosed date. |
| Year mentioned without context: "as we noted in 2024" | `YEAR_ONLY` is intentionally narrow (requires "in/during/expected in"); should not catch this. Verify in tests. |
| Two halves mentioned: "1H 2026 and 2H 2027" | Both extracted, earliest future wins → 1H 2026 (if not past) else 2H 2027. |
| Catalyst date in distant past (data already reported or slipped) | `unknown` tier after future filter strips all matches. Module 6 detects date-slip separately from `catalyst_snapshots` history. |

---

## 8. End-to-end smoke test

Once all five modules are built (run from `3_Biopharmcatalyst_parser/`; or just `run_3_Biopharmcatalyst_parser.bat` which chains all of these with y/N gates):

```bash
# 1. Initialize
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_init_db.py

# 2. Load Andre's existing files (drop them in _csv_source/ first)
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_1_ingest_catalysts.py _csv_source/biotech_catalysts_v3.csv
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_4_ingest_bpc_insider.py  _csv_source/insider_data3.csv

# 3. Compute timing
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py

# 4. EDGAR ingest for catalyst-universe tickers
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_2_ingest_edgar_form4.py --lookback-days 365
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_3_ingest_edgar_13dg.py  --lookback-days 365

# 5. Sanity checks
sqlite3 data/biotech.db <<EOF
SELECT COUNT(*) FROM catalyst_snapshots;            -- expect 600
SELECT COUNT(*) FROM catalyst_timing;               -- expect 600
SELECT precision_tier, COUNT(*) FROM catalyst_timing GROUP BY precision_tier;
SELECT source_lane,    COUNT(*) FROM catalyst_timing GROUP BY source_lane;
SELECT COUNT(*) FROM bpc_insider_supplement;        -- expect 1608
SELECT COUNT(DISTINCT ticker) FROM edgar_form4_filings;
SELECT module, status, rows_inserted, rows_rejected
  FROM ingest_log ORDER BY run_id DESC LIMIT 10;
EOF
```

Expected: all five modules report `status = 'success'` in `ingest_log`, no exceptions during the run.

---

## 9. Build order recommendation

Hand to Claude Code in this order. Each step has its own acceptance criteria above; complete and merge before moving on.

1. **Module 0** — Project scaffold (`src/`, `scripts/`, `tests/`, `config/`, `_csv_source/archive/`, `data/`), `src/database/db.py` with schema bootstrap, `scripts/3_0_init_db.py` entry-point, `tests/test_db.py`. **No `pyproject.toml`** — module is importable via `PYTHONPATH=src` against the shared `..\.venv\`.
2. **Module 1** — Catalyst CSV ingest. Validate on the provided `biotech_catalysts_v3.csv`. This is the smallest module and the best place to nail down the validation/idempotency pattern that Modules 4 and 6 reuse.
3. **Module 5** — Timing extraction. Depends only on Module 1. Doing it next gives you an end-to-end test of the catalyst pipeline (CSV → DB → derived timing) before touching network-dependent modules.
4. **Module 4** — BPC insider supplement. Reuses the Module 1 CSV-ingest pattern; quick win, exercises the insider table.
5. **Module 2** — EDGAR Form 4. The most complex module; the only one with significant network and rate-limit considerations. Use the five overlap tickers (CRBP, DTIL, STTK, TRDA, VSTM) as the primary test set.
6. **Module 3** — EDGAR 13D/13G metadata. Trivial once Module 2's EDGAR client exists.

---

## 10. Open questions to resolve before downstream modules

These don't block ingest/timing work but will shape Modules 6+:

- **Cash runway estimation:** pull from 10-Q (cash + investments, quarterly burn). Not free; needs a 10-Q parser. Possibly out of scope for v1, with a manual override field instead.
- **Smart-money fund overlay:** cross-reference catalyst tickers against the 40-fund 13F holdings already tracked in Andre's separate workflow. Decision: does that data move into `biotech.db` or stay separate and join at query time?
- **Claude API deep-dive JSON schema:** the exact output structure for Module 7 (POS estimate, risks, sizing rec). Worth designing carefully before any prompt engineering.

---

## 11. Resolved decisions (locked)

For traceability, decisions already made in design discussions:

- **Two windows:** Discovery (T+14 to T+180) and Execution (T+14 to T+60). Locked.
- **Three lanes for timing extraction:** Conference > Specific date > Text-parsed (with bucket fallback). Locked.
- **Strict schema validation** on all CSV ingests. Locked.
- **Past-date catalysts** in Module 5 → `unknown` tier; date-slip detection deferred to Module 6 querying snapshot history. Locked.
- **`is_open_market`** flag derived from Form 4 transaction code IN (`P`, `S`), excluding option-exercise noise (`M`). Locked.
- **Module 6 hard filters (H1–H6)** locked per D8 + D34: market cap ∈ [\$30M, \$2B); precision_tier resolvable; window forward-looking (date_min ≥ snapshot+14d); window not entirely past (date_max ≥ snapshot); event ∈ {phase1/2/3 clinical readouts: Interim/Initial/Topline/Full Results + Conference Presentation}; ticker not on the curated `delisted_tickers` allowlist (D34). Explicitly excludes Regulatory Decision, Submission, End-of-Phase Meeting, phase4, phase5.
- **Module 6 soft scoring uses three signals only:** (i) CEO/CFO insider buy gross over 365 days (no decay, no other roles); (ii) 30d price momentum from `price_history_30d`; (iii) net positive fund accumulation across the 22 specialist biotech funds tracked in `2_Funds_parser/2_fundparser.db`, comparing the two most recent quarters. Default weights `0.35 / 0.35 / 0.30` (funds slightly lower than CEO/CFO per user). Tiebreak: insider_score → fund_accumulation_score. Locked per D8 + D9.
- **Module 6 timing-bucket split:** `catalyst_date_defined` (specific|conference|month|quarter) vs `catalyst_date_undefined` (half|year). Ranking happens within each bucket. Locked per D8.
- **No automatic top-N cap from Module 6 into Module 7.** User picks the slice manually. Locked per D8.
- **Module 6 reads `2_Funds_parser/2_fundparser.db` via `ATTACH DATABASE` (read-only)** at scoring time. No new tables added to `biotech.db` for fund data; no separate ETL. The `--skip-funds` CLI flag bypasses the cross-DB read and rescales the other two weights. Locked per D9.
- **Renderers default to ROLLING view + template/data split.** `Outputs/catalyst_timings.html` and `Outputs/catalyst_scores.html` are static templates loading `<name>_data.js` sidecars; only the sidecar is rewritten per pipeline run. Default rendering takes the most recent score per unique catalyst across all snapshots, hiding only those whose `date_max` has passed. Pass `--snapshot-date YYYY-MM-DD` to render a single historical snapshot. Locked per D11 + D12.
- **2_Funds_parser auto-refresh from the 3_Biopharmcatalyst pipeline.** Before Module 0, the orchestrator calls `scripts/3_auto_refresh_funds.py`. When today is within ±7 days of a 13F filing deadline (Feb 14 / May 15 / Aug 14 / Nov 14, i.e. 45 days after each quarter end) OR today is past that window and `2_Funds_parser/2_fundparser.db` does not yet hold the matching quarter, the script subprocesses `2_Funds_parser` modules M2..M5 and regenerates the consensus_builds HTML. Module 6 (Anthropic API, billed) stays user-gated and is excluded from the auto-run. Locked per D13.

---

## 11.5 Auto-refresh of 2_Funds_parser (cross-project trigger)

The 3_Biopharmcatalyst pipeline depends on `2_Funds_parser` for the fund-accumulation signal in Module 6. Because fund holdings refresh quarterly (13F filings), the orchestrator includes a calendar-aware auto-trigger that runs the funds pipeline through Module 5 inclusive when fresh data is due.

### 11.5.1 Trigger rules

The pure-function decision logic lives in `src/funds_refresh/decision.py::decide(today, latest_period_in_db)`. Two rules:

1. **Window rule** — today falls within ±7 days of any quarterly 13F deadline:

   | Quarter end | Deadline (=q_end+45d) | ±7d window |
   |---|---|---|
   | Mar 31 | May 15 | May 8 – May 22 |
   | Jun 30 | Aug 14 | Aug 7 – Aug 21 |
   | Sep 30 | Nov 14 | Nov 7 – Nov 21 |
   | Dec 31 | Feb 14 (next year) | Feb 7 – Feb 21 |

   ⇒ approximately 8 weeks per year — 2 weeks × 4 quarters — the auto-trigger fires on calendar alone.

2. **Catch-up rule** — today is past the most recent window AND `2_Funds_parser/2_fundparser.db::holdings.period_of_report` MAX < target quarter end. Handles the case where the user skipped a window.

Both rules: outside their conditions, the auto-trigger reports `skip` with the reason and the biopharm pipeline proceeds with the existing funds data.

### 11.5.2 Steps invoked

When triggered, `funds_refresh.runner.run_refresh(target_q, prev_q)` subprocesses each of these `2_Funds_parser` scripts in sequence:

| Step | Script | Notes |
|---|---|---|
| M2 ingest 13F-HR | `2_ingest_13f.py` | Idempotent; skips re-downloads. |
| M2 build report | `2_build_report.py` | `Outputs/2_funds_report.html`. |
| M3 build universe | `3_build_universe.py -v` | Per-ticker rollup. |
| M4a hard filters | `4_run_hard_filters.py -v` | Snapshot fetch. |
| M4b ranking | `4_rank.py -v` | Archetype scoring → `Outputs/ranking_report_<q>.html`. |
| M4c fundamentals | `4c_enrich_fundamentals.py -v` | Free SEC EDGAR. |
| M5 context packs | `5_build_context_packs.py -v` | → `Outputs/enrichment_report_<q>.html`. |
| Consensus builds HTML | `_q1_consensus_report.py --quarter <target> --prev-quarter <prev>` | → `Outputs/<YYYYQn>_consensus_builds.html`. |

The first M2..M5 fatal aborts the chain. The consensus report is fail-open (logs failure, continues). The biopharm pipeline itself never aborts on a funds-refresh failure — the orchestrator prints a warning and proceeds.

**Excluded from auto-run:** `6_estimate_cost.py`, `6_score.py`, `6_serve_report.py`, `7_track_outcomes.py`. These remain user-gated in `2_Funds_parser/run_2_Funds_parser.bat` because they call the Anthropic API or require user interaction.

### 11.5.3 CLI

```bash
# Standard invocation (called from the .bat). Reads today, checks DB, decides.
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_auto_refresh_funds.py

# Dry-run: print the decision but do not subprocess anything.
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_auto_refresh_funds.py --dry-run

# Override today (for testing).
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_auto_refresh_funds.py --today 2026-05-15 --dry-run

# Ignore the calendar window and run unconditionally (target = most recent completed quarter).
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_auto_refresh_funds.py --force
```

### 11.5.4 Architecture

```
src/funds_refresh/
├── __init__.py
├── decision.py      ← pure logic (decide, filing_deadline, previous_quarter_end, quarter_label)
└── runner.py        ← subprocess wrapper around 2_Funds_parser/scripts/*.py
scripts/
└── 3_auto_refresh_funds.py    ← CLI entrypoint
tests/
└── test_funds_refresh_decision.py   ← 21 tests across all 4 quarters + edge cases
```

**Test coverage** (D13 acceptance):
- 10 parametrized "within window" tests (start, deadline day, end — for all 4 quarters)
- 4 catch-up scenarios (past window + stale DB, empty DB, up-to-date DB, DB ahead of target)
- 2 between-window scenarios (DB up-to-date / DB stale)
- 3 calendar-helper tests (deadline math, previous-quarter math, label formatting)
- 2 invariants (in-window fires regardless of DB freshness; previous_quarter_end set on every decision)

### 11.5.5 Verified empirical (today = 2026-05-28)

```
today=2026-05-28, 2_Funds_parser latest period=2026-03-31
decision: should_run=False — today 2026-05-28 is past the 2026-03-31 filing window
          (deadline 2026-05-15); 2_Funds_parser already holds 2026-03-31
```

Correctly skips: we're 6 days past the May 22 window end, and the funds DB already holds Q1 2026 (target).

---

## 12. Module 6 — Scoring & Ranking

### 12.1 Purpose

Reduce the ~570-row catalyst universe to a Claude-API-ready shortlist by:
1. **Hard-filtering** out catalysts the user is structurally uninterested in (large-cap, non-clinical-result, past-window).
2. **Soft-scoring** surviving catalysts on two signals only — CEO/CFO insider buying and 30-day momentum — producing a composite score per catalyst.
3. **Partitioning** the output into two timing buckets (`catalyst_date_defined` vs `catalyst_date_undefined`) so the user can rank within each precision class.

Output drives Module 7 (Claude deep-dive). The user manually selects the cutoff for which scored rows to send to Claude; there is no automatic top-N cap inside Module 6.

### 12.2 CLI

```bash
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py                          # default: most recent snapshot
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py --snapshot-date 2026-05-27
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py --all-snapshots         # after RULES_VERSION bump
PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py --dry-run -v
```

### 12.3 Hard filters (deal-breakers — drops the row from the shortlist)

Applied in declared order; first failure short-circuits and the row is logged with the failing rule code. Hard-failed rows are still persisted in `catalyst_scores` with `hard_pass = 0` and the rule code(s) in `fail_reasons`, so downstream review can audit why a row was excluded.

| # | Rule | Predicate | Rationale |
|---|---|---|---|
| **H1** | Market cap band | `market_cap_usd IS NOT NULL AND 30e6 <= market_cap_usd < 2e9` | Small/mid-cap sweet spot. Floor at $30M avoids dead-pool tickers where a catalyst can't drive normal price discovery. Cap at $2B is the user-locked ceiling for binary-event sensitivity. |
| **H2** | Timing resolvable | `catalyst_timing.precision_tier != 'unknown'` | Cannot rank a catalyst with no inferable date window. |
| **H3** | Forward-looking window | `catalyst_timing.date_min >= snapshot_date + 14` | T+14 floor is the locked Discovery/Execution window start (§11). Anything sooner is too late to position. |
| **H4** | Window not entirely past | `catalyst_timing.date_max >= snapshot_date` | Drops BPC's stale-row problem: rows where the entire window has already lapsed but the catalyst still appears in the source CSV. |
| **H5** | Clinical result event | `stage IN ('phase1','phase2','phase3') AND next_catalyst_type IN ('Interim Data','Initial Data','Topline Data','Full Results','Conference Presentation')` | User scope: clinical-readout-driven re-ratings only. Explicitly excludes Regulatory Decision (PDUFA), Submission (NDA/BLA filing events), End of Phase Meeting (FDA process not a data readout), phase4 (post-pivotal commercial) and phase5 (already approved). |
| **H6** (D34) | Ticker not delisted | `ticker NOT IN delisted_tickers` (case-insensitive) | BPC's docx can still surface a market cap for a ticker that has been delisted (reverse split, suspension, bankruptcy). yfinance / Anthropic can't price these usefully. Curated allowlist in `biotech.db.delisted_tickers`; managed via `scripts/3_flag_delisted_tickers.py --add <TICKER>`. |

**Note H3 — timing-bucket partition (NOT a filter, applied AFTER H1–H5):**

Hard-passing rows are tagged with a `timing_bucket` column based on `precision_tier`:

| `timing_bucket` | `precision_tier` values | Window width |
|---|---|---|
| `catalyst_date_defined` | `specific`, `conference`, `month`, `quarter` | ≤ 90 days |
| `catalyst_date_undefined` | `half`, `year` | > 90 days |

Ranking happens within each bucket. Both buckets are written to `catalyst_scores`; the user picks how to send each to Module 7 (e.g., all defined + top-K undefined). The bucket is informational, not a filter — it lets the user separate "we know when this is happening" from "this is sometime in H2 2026."

### 12.4 Soft scoring (the only two weighted signals)

User-locked: scoring uses **exactly two signals**, weighted equally (50/50 default in `config/scoring.yaml`, tunable). No other features. Specifically out of scope per user decision: recency decay, buy/sell ratio, 13D/G overlay (manual review), sentiment, catalyst-date-slip, multi-catalyst optionality, timing precision bonus, stage bonus, LOA/POP, BPC sentiment string, ESPP-tier roles (CMO/COO/CSO), 10% owner buys (PIPE noise), director buys.

#### 12.4.1 Insider score — CEO + CFO buys over 365 days

```
insider_gross_weighted = (CEO_buy_gross_usd_365d * w_role_ceo)
                       + (CFO_buy_gross_usd_365d * w_role_cfo)

# defaults in scoring.yaml (tunable):
#   w_role_ceo = 2.0
#   w_role_cfo = 1.0
#   (no other roles count; weight = 0 for everyone else)

insider_score = log10(1 + insider_gross_weighted) / log10(1 + insider_norm_cap) * 100
              capped at 100
              0 if insider_gross_weighted == 0

# default insider_norm_cap = 5_000_000   # $5M weighted gross = 100-point ceiling
```

- **Source**: `v_executive_open_market_trades` filtered to `buy_sell = 'Buy'` AND `executive_role IN ('CEO','CFO')` AND `filing_date >= snapshot_date - 365`.
- **No recency decay** (user-locked). A buy from day -350 contributes as much as a buy from day -5, as long as it's inside the 365-day window.
- **Both EDGAR and BPC sources** count. The view already unions them with `source` tagged for traceability; the score sums both.
- **10% owners excluded** (user-locked — they're typically PIPE take-ups, not conviction signals).

#### 12.4.2 Momentum score — 30-day return

Parse `price_history_30d` (semicolon-separated, oldest → newest) and compute:

```
prices = [float(x.strip()) for x in price_history_30d.split(';') if x.strip()]
return_30d_pct = (prices[-1] / prices[0] - 1) * 100   # NULL if <2 valid prices

momentum_score = piecewise_curve(return_30d_pct)
```

`piecewise_curve` shape (inverted-U around 0%, soft penalty when price already ran, all bounds tunable in `scoring.yaml`):

| `return_30d_pct` | `momentum_score` |
|---|---|
| ≤ −50% | 0 (likely broken / pipeline-failure tape) |
| −50% to −10% | linear 0 → 60 |
| −10% to +10% | flat 100 (consolidation = ideal pre-catalyst tape) |
| +10% to +30% | linear 100 → 70 |
| +30% to +60% | linear 70 → 20 (price has already moved on the catalyst) |
| ≥ +60% | 0 (already priced in) |
| NULL (no parseable history) | 50 (neutral; do not penalize for missing data) |

**No hard exclusion** based on momentum per user decision — even +100% movers stay in the shortlist, they just score 0 on this dimension. The user wants to see them, not silently drop them.

#### 12.4.3 Fund accumulation score — net positive accumulation by specialist biotech funds

Cross-DB read from `2_Funds_parser/2_fundparser.db` (the quarterly 13F-parsed holdings of 22 specialist biotech funds — Baker Brothers, Deerfield, OrbiMed, BVF, Perceptive, RA Capital, RTW, Redmile, Cormorant, EcoR1, SIO, Avoro, PFM Health Sciences, ARCH, Atlas, 5AM, Versant, Janus Henderson Biotech, Boxer, Sofinnova, Athos, plus aliases). The user maintains and refreshes that DB quarterly via the separate `run_2_Funds_parser.bat` pipeline; Module 6 reads it at scoring time (no separate ingest into `biotech.db`).

```python
# Conceptually (real query is one SQL with ATTACH DATABASE in §12.7):

quarter_latest   = max(funds.holdings.period_of_report)
quarter_previous = max(funds.holdings.period_of_report WHERE period_of_report < quarter_latest)

# For each (BPC ticker, fund_id):
shares_latest   = funds.holdings.shares   for (ticker, fund_id, quarter_latest)   or 0
shares_previous = funds.holdings.shares   for (ticker, fund_id, quarter_previous) or 0
mv_latest       = funds.holdings.market_value for (ticker, fund_id, quarter_latest) or NULL

price_proxy = mv_latest / shares_latest   # only when shares_latest > 0
share_delta = shares_latest - shares_previous

# Per-fund contribution: only positive deltas count (we're scoring accumulation, like insider score scores buys)
fund_contribution_usd = MAX(0, share_delta) * COALESCE(price_proxy, 0)

# Per-ticker:
fund_accumulation_usd = SUM(fund_contribution_usd) across all funds
funds_holding_latest    = COUNT(funds where shares_latest > 0)
funds_holding_previous  = COUNT(funds where shares_previous > 0)

fund_accumulation_score = log10(1 + fund_accumulation_usd) / log10(1 + fund_norm_cap) * 100
                        capped at 100
                        0 if fund_accumulation_usd == 0 OR ticker absent from funds DB

# default fund_norm_cap = 50_000_000   # $50M = 100-point ceiling (10× insider cap; reflects ~22 funds vs ~2 insider roles)
```

- **No recency decay** (mirrors insider design — quarterly granularity already enforces a 90-day floor anyway).
- **Only positive deltas count.** A fund cutting its stake doesn't subtract from the score, but it also doesn't add. (Exits are captured for audit via `funds_holding_previous - funds_holding_latest`, but not used in scoring per the same "buys only" symmetry as the insider signal.)
- **`funds_holding_latest` / `funds_holding_previous`** are persisted in `catalyst_scores` as informational breadth metrics; they do NOT enter the score directly (the dollar-weighted formula already captures both magnitude and breadth implicitly).
- **Ticker not in funds DB** → `fund_accumulation_usd = 0`, `fund_accumulation_score = 0`. About 38% of BPC tickers (111 of 296 on the 2026-05-27 snapshot) fall in this bucket. Treated as "no signal," not "negative signal."
- **Stale funds DB:** if `quarter_latest < snapshot_date - 180 days`, ingest emits a warning to stderr and writes a row to `ingest_log` with `status='partial'` but still scores using the stale data. The user's 18:00 daily orchestrator pattern means the funds DB might be 1–3 months stale between quarterly refreshes; this is fine for ranking purposes since fund positions are themselves quarterly snapshots.
- **Share splits** are not adjusted in v1. A 1:10 reverse split between quarters would make a flat-position fund look like a huge seller. Across 22 funds the noise log-averages out; if a specific ticker's score looks wrong post-split, it surfaces in the audit and we calibrate in v2.

Empirical sizing (Q1 2026 vs Q4 2025, small/mid cap BPC tickers):
- 1 ticker with > $100M accumulation (SNDX at $110M)
- 17 tickers in $25M–$100M
- 27 tickers in $5M–$25M  ← the meaty middle
- 26 tickers in $0–$5M
- 48 tickers at zero / negative net accumulation
- ~80 tickers absent from funds DB entirely (small ineligible / IPO'd this quarter / not held by any tracked fund)

The $50M cap puts the top ~18 tickers at 92–100 score and gives the meaty middle a 75–90 range. Re-tune after first live run.

#### 12.4.4 Composite

```
composite_score = w_insider * insider_score + w_momentum * momentum_score + w_funds * fund_accumulation_score

# defaults (sum to 1.0 so composite_score stays in [0, 100]):
#   w_insider  = 0.35
#   w_momentum = 0.35
#   w_funds    = 0.30          # "slightly lower than CEO/CFO" per user decision D9
```

Ranking within `timing_bucket`: `ORDER BY composite_score DESC, insider_score DESC, fund_accumulation_score DESC` (insider as primary tiebreaker — the user weighted it as the highest-conviction signal — fund accumulation as secondary).

### 12.5 New schema — `catalyst_scores`

Added to `src/database/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS catalyst_scores (
    snapshot_date         DATE    NOT NULL,
    ticker                TEXT    NOT NULL,
    drug                  TEXT    NOT NULL,
    nct_number            TEXT    NOT NULL,
    next_catalyst_type    TEXT    NOT NULL,
    hard_pass             BOOLEAN NOT NULL,
    fail_reasons          TEXT,                  -- comma-joined H1..H6 codes (D34: H6 added); NULL when hard_pass=1
    timing_bucket         TEXT,                  -- 'catalyst_date_defined'|'catalyst_date_undefined'|NULL
    -- Insider signal
    insider_gross_weighted_usd  REAL,            -- ROLE-weighted gross over 365d (CEO*2 + CFO*1)
    insider_score         REAL,                  -- 0..100
    -- Momentum signal
    return_30d_pct        REAL,                  -- raw % return from price_history_30d; NULL on parse fail
    momentum_score        REAL,                  -- 0..100
    -- Fund accumulation signal (cross-DB from 2_Funds_parser)
    fund_quarter_latest         TEXT,            -- e.g. '2026-03-31'; NULL if funds DB absent or ticker not held
    fund_quarter_previous       TEXT,            -- e.g. '2025-12-31'
    funds_holding_latest        INTEGER,         -- count of tracked funds holding shares > 0 in latest quarter
    funds_holding_previous      INTEGER,         -- count of tracked funds holding shares > 0 in previous quarter
    fund_accumulation_usd       REAL,            -- sum of positive Δshares × price_proxy across funds
    fund_accumulation_score     REAL,            -- 0..100; 0 when ticker absent from funds DB
    -- Composite
    composite_score       REAL,                  -- 0..100; NULL when hard_pass=0
    computed_at           TIMESTAMP NOT NULL,
    rules_version         TEXT    NOT NULL,      -- 'v1.0' etc.
    PRIMARY KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type),
    FOREIGN KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        REFERENCES catalyst_snapshots(snapshot_date, ticker, drug, nct_number, next_catalyst_type)
);

CREATE INDEX IF NOT EXISTS idx_scores_composite ON catalyst_scores (snapshot_date, composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_bucket    ON catalyst_scores (timing_bucket, hard_pass);

-- D34 — curated delisted-ticker allowlist. M6's H6 gate fails any
-- catalyst whose ticker appears here. Managed via
-- scripts/3_flag_delisted_tickers.py --add / --remove / --list.
CREATE TABLE IF NOT EXISTS delisted_tickers (
    ticker       TEXT PRIMARY KEY,
    flagged_at   TIMESTAMP NOT NULL,
    reason       TEXT,
    source       TEXT       -- 'manual' / 'yfinance-probe' / 'edgar-suspension' / ...
);
```

`hard_pass = 0` rows still get a row in the table (with `composite_score = NULL` and `fail_reasons = 'H3,H5'` etc.) so the user can audit *why* a ticker dropped. `hard_pass = 1` rows have all scoring columns populated.

### 12.6 Configuration — `config/scoring.yaml`

Every threshold, weight and curve point lives in `config/scoring.yaml`; the module reads at startup, validates with pydantic, and stamps the file's content hash into `rules_version`. Tuning weights does not require code changes.

```yaml
# config/scoring.yaml (initial values from D8 + D9)
rules_version: v1.0

hard_filters:
  H1:
    mcap_min_usd: 30_000_000
    mcap_max_usd: 2_000_000_000
  H3:
    window_start_days: 14         # date_min >= snapshot + 14
  H5:
    allowed_stages: [phase1, phase2, phase3]
    allowed_catalyst_types:
      - Interim Data
      - Initial Data
      - Topline Data
      - Full Results
      - Conference Presentation

timing_buckets:
  catalyst_date_defined:   [specific, conference, month, quarter]
  catalyst_date_undefined: [half, year]

insider:
  lookback_days: 365
  role_weights:
    CEO: 2.0
    CFO: 1.0
    # all other roles = 0 (not listed = excluded)
  normalisation_cap_weighted_usd: 5_000_000

momentum:
  curve:
    - { return_pct: -50, score:   0 }
    - { return_pct: -10, score:  60 }
    - { return_pct:   0, score: 100 }   # peak
    - { return_pct:  10, score: 100 }   # peak (flat plateau)
    - { return_pct:  30, score:  70 }
    - { return_pct:  60, score:  20 }
    - { return_pct: 100, score:   0 }
  null_score: 50

funds:
  # Cross-DB read from 2_Funds_parser/2_fundparser.db
  db_path_relative_to_repo_root: "2_Funds_parser/2_fundparser.db"
  normalisation_cap_usd: 50_000_000          # $50M positive accumulation = 100-point ceiling
  stale_warning_days: 180                    # warn if quarter_latest older than this vs snapshot_date
  # No role list — funds DB tracks 22 specialist biotech funds curated in 2_Funds_parser's
  # config; the entire holdings table is treated as the signal source.

composite:
  weight_insider:  0.35
  weight_momentum: 0.35
  weight_funds:    0.30
  # Tiebreaker order: composite DESC, then insider_score DESC, then fund_accumulation_score DESC
  tiebreaker_chain: [insider_score, fund_accumulation_score]
```

### 12.7 Architecture

```
src/module_6/
├── __init__.py
├── config.py         # pydantic ScoringConfig + YAML loader; stamps rules_version from content hash
├── filters.py        # pure functions: apply_hard_filters(row, cfg) -> (hard_pass, fail_reasons, timing_bucket)
├── scoring.py        # pure functions: insider_score(), momentum_score(), fund_accumulation_score(), composite()
├── funds_reader.py   # cross-DB read: ATTACH 2_Funds_parser/2_fundparser.db (read-only); query per-ticker accumulation
├── ingest.py         # orchestrator: open biotech.db, ATTACH funds DB, apply filters, score, upsert
scripts/
├── 3_6_score_catalysts.py    # CLI
config/
├── scoring.yaml
tests/
├── test_module6_filters.py        # one test per H rule + bucket partition
├── test_module6_scoring.py        # insider/momentum/funds curve points + composite
├── test_module6_funds_reader.py   # synthetic mini-funds-DB fixture
└── test_module6_ingest.py         # end-to-end synthetic snapshot with attached funds DB
```

Pure-function design (mirrors M5) — `filters.py`, `scoring.py`, `funds_reader.py` know nothing about the orchestration layer. All DB I/O is in `ingest.py` + `funds_reader.py`.

**Cross-DB pattern (funds DB):**

```python
# In ingest.py orchestrator:
conn = get_connection()                                # opens data/biotech.db
funds_db_path = cfg.funds.db_path_relative_to_repo_root
conn.execute(f"ATTACH DATABASE '{funds_db_path}' AS funds")    # READ-ONLY via path; no write ops issued
# ... queries can now reference funds.holdings, funds.funds, funds.cusip_ticker_map ...
```

The ATTACH is per-connection and ephemeral — no permanent dependency in `schema.sql`. If the funds DB is missing or unreadable, `funds_reader.py` raises a friendly error and ingest aborts with `ingest_log.status='failed'`. A `--skip-funds` CLI flag bypasses the funds signal (scores `fund_accumulation_score = 0` for every row, useful when the funds DB is being refreshed or relocated).

**Why ATTACH instead of a separate ETL into biotech.db:**
1. Funds data refreshes on a quarterly cadence — far slower than biotech.db's weekly catalyst refresh — so duplicating the holdings table would mostly just go stale.
2. The user already runs `run_2_Funds_parser.bat` independently; that pipeline is the canonical source of truth for fund positions.
3. ATTACH adds zero new tables to biotech.db's schema — keeps the M0 contract clean.
4. Audit trail is preserved: anyone can replay the score by attaching the same funds.db and re-running M6.

### 12.8 Idempotency & recomputation

- `INSERT OR REPLACE` on the 5-col PK — same shape as M5.
- `rules_version` is the SHA-256 first 7 chars of the loaded YAML content. Edit `scoring.yaml` → hash changes → next run re-scores even if PK matches.
- `--all-snapshots` flag re-scores every snapshot in `catalyst_snapshots` (use after a rules bump).
- `ingest_log.module = 'score_catalysts'`. One row per run.

### 12.9 Acceptance criteria (Module 6)

On the live 2026-05-27 snapshot (572 catalyst rows), an initial-run with the YAML defaults above should produce:

- `catalyst_scores` row count = `COUNT(*) FROM v_latest_catalysts WHERE snapshot_date = '2026-05-27'` (every catalyst gets a row, hard_pass=0 included).
- `SUM(CASE WHEN hard_pass=1 THEN 1 ELSE 0 END)` ∈ the **20–60 range** based on the empirical filter sensitivity recorded in D8.
- `SUM(CASE WHEN hard_pass=1 AND timing_bucket='catalyst_date_defined' THEN 1 ELSE 0 END)` ≥ `... 'catalyst_date_undefined' ...` (defined-timing catalysts should outnumber undefined ones based on the current lane distribution).
- Top 5 by `composite_score` within `catalyst_date_defined` matches a hand-verified expected list (test fixture written when first live run is sanity-checked).
- `composite_score` strictly in [0, 100] for all hard_pass=1 rows.
- `fail_reasons` non-NULL on every hard_pass=0 row and references only codes in {H1, H2, H3, H4, H5}.
- **Fund accumulation acceptance:** `fund_quarter_latest = '2026-03-31'` and `fund_quarter_previous = '2025-12-31'` populated on all hard_pass=1 rows (matches the latest two quarters in the funds DB as of 2026-05-27). At least 60% of hard-passing tickers should have `fund_accumulation_usd > 0` (empirically ~62% of BPC small/mid cap tickers are held by at least one tracked fund). Top accumulator should be SNDX with ~$110M.
- **`--skip-funds` mode:** running with this flag must set `fund_accumulation_score = 0`, `fund_accumulation_usd = NULL`, and `fund_quarter_latest = NULL` on every row, but still produce valid `composite_score` (using only the insider + momentum signals, scaled by their summed weight 0.70 to keep composite ∈ [0, 100]).
- Re-running on the same snapshot with the same YAML produces 0 inserts, N updates (all PK collisions), identical scores.
- Bumping any value in `scoring.yaml` and re-running with no other change produces N updates with at least one column changed.

### 12.10 Open items deferred to first-run calibration

- **Momentum curve shape** — the 7-point piecewise curve is a first-pass guess. Validate against the live 2026-05-27 distribution and tune.
- **Insider normalisation cap ($5M)** — empirically observed CEO+CFO weighted gross may cluster lower; lowering the cap concentrates the score in the realistic range.
- **Funds normalisation cap ($50M)** — empirical Q1-2026 sample showed 1 ticker > $100M, 17 in $25–100M, 27 in $5–25M. The $50M cap may be too high; lowering to $25M would give the meaty middle a wider score range. Re-tune.
- **Funds DB quarter freshness** — Module 6's stale-warning threshold is 180 days. If the user's quarterly refresh cadence slips beyond that consistently, surface as a more visible warning or block.
- **Share-split adjustment in funds signal** — v1 does not adjust. If a specific ticker's score looks wrong post-split, calibrate in v2 (would need a SEC corporate-actions feed or a per-ticker manual override file).
- **Tiebreaker direction** — `insider_score` first, then `fund_accumulation_score`. Revisit after first ranking review.
- **HTML render of `catalyst_scores`** — optional sibling script `3_6_render_scores.py` that produces an Outputs/scored_shortlist.html similar to M5's report, partitioned by `timing_bucket`. Build only if M7 doesn't immediately consume `catalyst_scores` directly.

---

## 13. Downstream modules (preview only, not v1)

For architectural context — these are NOT specified yet:

- **Module 7 — Claude API deep-dive:** user-selected subset of `catalyst_scores` rows (where `hard_pass = 1`) → `claude-opus-4-7` with `web_search` enabled. Returns structured JSON per ticker (POS estimate vs base rate, expected move on positive/negative, dilution risk, key risks, sizing rec). Mandatory `[y/N]` cost-approval gate per memory `claude-api`. Heavy reuse from `2_Funds_parser/src/module_6/` (prompt caching + batch API + JSON validation). **User does NOT want an automatic top-N cap** — they pick the slice manually after reviewing Module 6 output.
- **Module 8 — Dashboard:** dark-themed iOS-optimized HTML, expandable cards per ticker, sortable by composite score + Claude-deep-dive findings. Static file output.

---

## 14. Out of scope (explicitly)

- Trade execution / brokerage integration. Position decisions remain manual.
- Real-time price data. The 30-day price history in the BPC CSV is sufficient for ranking; live prices aren't needed for daily/weekly research cadence.
- Options-market data (IV, implied move). Worth a Module 9 later if a free/cheap data source is available.
- Anything that requires bypassing a ToS or WAF. The BPC CSV-download path keeps everything on legitimate footing.
- **Smart-money / 13D/G overlay automation** — user decision: cross-referencing recent ownership filings against curated biotech-fund lists is done manually, not in code. Module 3's `edgar_ownership_filings` table remains a reference dataset, not a scoring input.

