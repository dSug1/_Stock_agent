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

Downstream (NOT in this spec):
  Module 6 (Scoring & ranking)        — joins catalyst_timing, insider tables, fund overlay
  Module 7 (Claude API deep-dive)     — top-N from Module 6
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

## 2. Database Schema (Module 0)

The schema is bootstrapped at first run by `db.py:initialize_db()`. The function is idempotent (uses `CREATE TABLE IF NOT EXISTS`).

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

For each CIK, fetch `https://data.sec.gov/submissions/CIK{padded_cik}.json` and walk the `filings.recent` arrays to find all Form 4 filings within the lookback window. Filings beyond `recent` (older than ~1000 filings) are in paginated files — for biotech small-caps this is rarely an issue, but the implementation should handle the pagination case (out of scope for v1 if it complicates things; document as a known limitation).

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
- `--full-refresh` deletes existing rows for the targeted CIKs/accessions before re-loading.

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

For each CIK, walk `submissions` to find filings where `form` matches: `SC 13D`, `SC 13G`, `SC 13D/A`, `SC 13G/A`.

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

---

## 12. Downstream modules (preview only, not v1)

For architectural context — these are NOT specified yet, just stubbed:

- **Module 6 — Scoring & ranking:** apply pre-filter (Phase 1/2, MCap < $2B, timing window 14–180/60 days from Module 5), then soft scoring (insider buy strength, smart-money overlay, sentiment, momentum, catalyst-date-slip detection from snapshot history). Produces ranked candidate list.
- **Module 7 — Claude API deep-dive:** for top-N candidates from Module 6, call `claude-opus-4-7` with `web_search` enabled, request JSON-shaped output (POS estimate vs base rate, expected move on positive/negative, dilution risk, key risks, sizing rec). Cost-controlled (cap N per run).
- **Module 8 — Dashboard:** dark-themed iOS-optimized HTML, expandable cards per ticker, sortable by composite score. Static file output; optionally auto-pushed to a hosting target.

---

## 13. Out of scope (explicitly)

- Trade execution / brokerage integration. Position decisions remain manual.
- Real-time price data. The 30-day price history in the BPC CSV is sufficient for ranking; live prices aren't needed for daily/weekly research cadence.
- Options-market data (IV, implied move). Worth a Module 9 later if a free/cheap data source is available.
- Anything that requires bypassing a ToS or WAF. The BPC CSV-download path keeps everything on legitimate footing.

