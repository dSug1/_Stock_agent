# Module 4c — Fundamentals enrichment (biotech, financials-only)

**Status:** 📋 Draft specification (2026-04-26). Build approved.
**Last updated:** 2026-04-26
**Runtime:** Pure Python, single free public API (SEC EDGAR). No paid dispatch. No yfinance. No ClinicalTrials.gov.

Module 4c sits between Module 4b (price history + archetype ranking) and
Module 5 (context packs). For biotechnology-industry tickers, it pre-fetches
**financial** structured data — cash, runway, capital raises, shelf
registrations, insider transactions — into `data/fundamentals.db`. M5
then injects a `fundamentals` block into each pack so M6's prompt can read
the financials instead of paying the LLM to web-search for them.

**Scope is financials-only.** Clinical trials, results, failures, interim
and final readouts are deliberately **excluded**: M6 is better positioned
to web-search those (richer context, multi-source synthesis, recency
checks). M4c does not touch ClinicalTrials.gov.

**Why this exists.** The current M6 prompt (m6-v3) instructs the LLM to
populate `research_brief.financials.{cash, runway, burn, shelf,
capital_raises, PFWs}` and `insider_activity.recent_transactions[]` via
`web_search`. M4c front-loads that retrieval into a free, structured,
cached pipeline step. The cost win is modest (~$15-20 per 98-ticker run
— ~20% search-budget reduction) but the reliability gain is real:
SEC-XBRL cash and Form-4 insider rows are deterministic where LLM
`web_search` occasionally hallucinated values in early m6-v3 runs.

**Per D32, all storage is SQL.** Raw EDGAR responses live in TEXT
columns inside `data/fundamentals.db` (queryable via `json_extract`).
No `*.json` files on disk, no Parquet sidecar.

**Per the existing `_RateLimiter` infrastructure**, M4c reuses
`src/layer_1/edgar_13f._EDGAR_LIMITER` (**9.5 req/s** since D56,
process-global token bucket). No new SEC limiter. No new User-Agent.

**Per D56**, the HTTP layer uses a module-level `requests.Session` with
keep-alive (`pool_connections=8, pool_maxsize=16`) and the per-ticker
loop runs in a `ThreadPoolExecutor` sized by `fundamentals.yaml`
`edgar.max_workers` (default 5). Workers share the same rate limiter
and session; each opens its own SQLite connection (WAL mode handles
concurrent writes). Result mutations serialize through a `threading.Lock`.

---

## Role and contract

**Reads.**
- `data/prices.db` — `ticker_snapshot` (industry classification, drives biotech-only gate)
- `_intermediate_outputs/ranked_candidates_{quarter}.parquet` — ticker list (the M4b ranked feed)
- `Outputs/sec_company_tickers.json` — ticker→CIK map (existing M2 cache, 7-day TTL refresh)
- `config/fundamentals.yaml` *(new)* — TTLs, biotech industry list, source toggles

**Writes.**
- `data/fundamentals.db` *(new)* — four tables (`financials`, `capital_raises`,
  `insider_transactions`, `fetch_log`)

**Does not write.** `2_fundparser.db`, `data/prices.db`, `context_packs.db`,
`llm_scores.db`. M5 is the only consumer (read-only).

**Does not call.** Anthropic API, yfinance, OpenFIGI, ClinicalTrials.gov.
Only SEC EDGAR.

---

## Where M4c runs in the pipeline

```
M4b (rank) → M4c (financials)  →  M5 (packs)  →  M6 (LLM)
              │                       │
              ▼                       ▼
       data/fundamentals.db    pack.fundamentals  ←  M5 reads M4c output
```

Three call sites:

1. **`run_2_Funds_parser.bat`** — y/N gate after M4b ("Proceed to Module 4c
   (free SEC enrichment)? [Y/n]"), default **y**. Skipping is harmless:
   M5 emits packs with an empty `fundamentals` block (status `"missing"`),
   and M6 falls back to `web_search` for those fields.
2. **Standalone CLI `scripts/4c_enrich_fundamentals.py`** — accepts
   `--quarter`, `--ticker`, `--tickers`, `--source`, `--force-refresh`,
   `--dry-run`, `-v`. Idempotent (per-row TTL gating).
3. **`run_enrichment` (M5)** — reads from `data/fundamentals.db` to inject
   the `fundamentals` block at pack-build time. M5 never invokes M4c — if
   the DB is missing or stale, M5 emits a partial pack and continues.

---

## Refresh cadence — per-row TTL

Mirrors the existing pattern in M4a (`ticker_snapshot.fetched_at` + 7-day
TTL) and M2 (`backfill_*` only when fields are NULL).

| Source | Strategy | TTL |
|---|---|---|
| **EDGAR companyfacts** (cash, R&D, G&A, PP&E, AR, op CF, shares) | row-level upsert per (ticker, period) | **30 days** (matches M4a static) |
| **EDGAR submissions** (filings list — discovery for Form 4 + raises) | poll for new accession #s | **7 days** |
| **EDGAR Form 4** (insider transactions) | append-only by (cik, accession_number) | **no refresh**; only fetch new filings since `MAX(filing_date)` per ticker |
| **EDGAR 8-K Items 1.01/3.02 + S-3 + 424B5** (capital raises) | append-only by accession # | **no refresh**; same as Form 4 |

Each row carries `fetched_at TEXT` (ISO UTC) + `fetch_status TEXT` ('ok' /
'partial' / 'failed'). The CLI's TTL probe filters by `fetched_at` against
the configured TTL before fetching.

---

## `data/fundamentals.db` schema

```sql
-- Per-ticker financial snapshot (one row per (ticker, period)).
CREATE TABLE IF NOT EXISTS financials (
    ticker                            TEXT NOT NULL,
    cik                               TEXT NOT NULL,            -- 10-digit padded
    period                            TEXT NOT NULL,            -- 'YYYY-Q1'..'YYYY-Q4' or 'YYYY-FY'
    period_end_date                   TEXT NOT NULL,            -- ISO date
    form                              TEXT,                     -- '10-Q' | '10-K'

    -- balance-sheet
    cash_and_equivalents_usd          INTEGER,
    short_term_investments_usd        INTEGER,
    cash_total_usd                    INTEGER,                  -- cash_and_eq + short_term_inv
    total_assets_usd                  INTEGER,
    total_liabilities_usd             INTEGER,
    accounts_receivable_usd           INTEGER,
    ppe_net_usd                       INTEGER,

    -- cash-flow (TTM-style — last 4 quarters from filing date; latest period only)
    rd_expense_ttm_usd                INTEGER,
    ga_expense_ttm_usd                INTEGER,
    quarterly_burn_usd                INTEGER,                  -- abs(operating CF) / 4
    runway_months                     REAL,                     -- cash_total / monthly_burn
    operating_cf_ttm_usd              INTEGER,

    -- equity
    basic_shares_count                INTEGER,
    diluted_shares_count              INTEGER,
    prefunded_warrants_count          INTEGER,                  -- 10-Q footnote heuristic; nullable
    fully_diluted_shares_count        INTEGER,                  -- diluted + PFW; nullable
    shelf_registration_usd_capacity   INTEGER,                  -- max remaining S-3 capacity

    -- raw
    companyfacts_raw_json             TEXT,                     -- truncated to most-recent block

    -- bookkeeping
    fetched_at                        TEXT NOT NULL,
    fetch_status                      TEXT NOT NULL,            -- 'ok' | 'partial' | 'failed'
    fetch_error                       TEXT,
    PRIMARY KEY (ticker, period)
);

CREATE INDEX IF NOT EXISTS idx_fin_ticker_date  ON financials(ticker, period_end_date DESC);
CREATE INDEX IF NOT EXISTS idx_fin_cik          ON financials(cik);

-- Append-only capital raises (PFW / equity / debt). One row per discrete event.
CREATE TABLE IF NOT EXISTS capital_raises (
    ticker                  TEXT NOT NULL,
    cik                     TEXT NOT NULL,
    accession_number        TEXT NOT NULL,                      -- SEC dedup key
    filing_date             TEXT NOT NULL,                      -- ISO date
    event_date              TEXT,                               -- effective date if disclosed
    form                    TEXT NOT NULL,                      -- '8-K' | 'S-3' | 'S-3/A' | '424B5'
    raise_type              TEXT,                               -- 'pfw' | 'equity' | 'debt' | 'shelf' | 'unknown'
    gross_proceeds_usd      INTEGER,
    net_proceeds_usd        INTEGER,
    shares_issued           INTEGER,
    price_per_share_usd     REAL,
    discount_to_market_pct  REAL,                               -- (market - price) / market; nullable
    description             TEXT,                               -- one-line synopsis
    raw_filing_url          TEXT,                               -- direct link to filing
    fetched_at              TEXT NOT NULL,
    fetch_status            TEXT NOT NULL,
    PRIMARY KEY (cik, accession_number)
);

CREATE INDEX IF NOT EXISTS idx_raises_ticker_date ON capital_raises(ticker, filing_date DESC);

-- Append-only Form 4 insider transactions. One row per reported transaction line.
CREATE TABLE IF NOT EXISTS insider_transactions (
    ticker                  TEXT NOT NULL,
    cik                     TEXT NOT NULL,
    accession_number        TEXT NOT NULL,
    filing_date             TEXT NOT NULL,
    transaction_date        TEXT,
    insider_name            TEXT NOT NULL,
    insider_cik             TEXT,                               -- the reporting individual's CIK
    role                    TEXT,                               -- 'CEO' | 'CFO' | 'Director' | '10% owner' | 'Officer' | 'Other'
    txn_type                TEXT NOT NULL,                      -- 'buy' | 'sell' | 'option_exercise' | 'option_grant' | 'gift' | 'other'
    shares                  INTEGER,
    price_usd               REAL,
    total_value_usd         INTEGER,                            -- shares * price
    raw_form4_url           TEXT,
    fetched_at              TEXT NOT NULL,
    fetch_status            TEXT NOT NULL,
    PRIMARY KEY (cik, accession_number, insider_name, transaction_date, txn_type, shares)
);

CREATE INDEX IF NOT EXISTS idx_insider_ticker_date  ON insider_transactions(ticker, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_insider_role         ON insider_transactions(role, txn_type);

-- Per-(ticker, source) fetch log. Drives TTL gating.
CREATE TABLE IF NOT EXISTS fetch_log (
    ticker                  TEXT NOT NULL,
    source                  TEXT NOT NULL,                      -- 'companyfacts' | 'submissions' | 'form4' | 'capital_raises'
    last_fetched_at         TEXT NOT NULL,
    last_status             TEXT NOT NULL,                      -- 'ok' | 'partial' | 'failed' | 'not_modified' | 'skipped_non_biotech'
    last_error              TEXT,
    rows_written            INTEGER NOT NULL DEFAULT 0,
    -- D56 — conditional-GET cache. Populated only by companyfacts when SEC
    -- ever returns ETag/Last-Modified. Currently always NULL since SEC's
    -- data.sec.gov sends Cache-Control: no-cache, no-store. Defensive
    -- future-readiness.
    etag                    TEXT,
    last_modified           TEXT,
    PRIMARY KEY (ticker, source)
);
```

**Schema evolves additively** via `_apply_additive_migrations` — same
pattern as M4 (`prices.py`) and M6 (`scores_db.py`). Migration list lives
in `src/module_4c/fundamentals_db.py::_ADDITIVE_MIGRATIONS`.

**`data/fundamentals.db` is gitignored** (per existing `*.db` rule).

---

## Sources fetched (4 — no CT.gov)

| Source key | Strategy | TTL | Writes to |
|---|---|---:|---|
| `companyfacts` | row-level upsert per (ticker, period) | 30d | `financials` |
| `submissions`  | poll for new accession #s (discovery — no rows of its own) | 7d  | `fetch_log` only |
| `form4`        | append-only by accession # | 30d (poll cadence) | `insider_transactions` |
| `capital_raises` | append-only by accession # | 30d (poll cadence) | `capital_raises` |

---

## `config/fundamentals.yaml` (new)

```yaml
# Module 4c — fundamentals enrichment configuration.
# Spec: spec/module_4c_spec.md
# Decisions: spec/decisions.md § D54.

# Industries that trigger M4c enrichment. Tickers in any other industry are
# skipped (logged as 'skipped_non_biotech' in fetch_log) — M5 will still
# emit packs without a fundamentals block; M6 falls back to web_search.
biotech_industries:
  - "Biotechnology"
  - "Drug Manufacturers - Specialty & Generic"
  - "Drug Manufacturers - General"

# Per-source TTLs (days). 0 disables the source for the run.
ttls:
  companyfacts_days:    30
  submissions_days:      7
  form4_days:           30      # poll cadence; rows are append-only
  capital_raises_days:  30

# Source toggles. Disable a source globally without touching code.
sources:
  companyfacts:    true
  submissions:     true
  form4:           true
  capital_raises:  true

# EDGAR client.
edgar:
  user_agent: "Stock-agent/1.0 sugitania846@gmail.com"
  # Rate limit reused from src/layer_1/edgar_13f._EDGAR_LIMITER (9 req/s).
  # Concurrency uses the same _EDGAR_LIMITER; do not duplicate.
  max_workers: 5

# Form 4 fetch policy.
form4:
  lookback_days_first_run:  1095     # 3y on cold start
  lookback_days_per_run:    180      # incremental window each run
  ignore_txn_types:         ["other"]   # filtered at write time

# Storage.
db_path: "data/fundamentals.db"
```

---

## Algorithms

### Ticker → CIK resolution

Reuses `src/layer_1/sec_ticker_resolver.load_sec_name_index` cache. New
helper in `src/module_4c/edgar_client.py::load_ticker_cik_map` returns
`{ticker: cik (10-digit)}`. A ticker absent from the SEC list (delisted,
foreign primary, etc.) gets `fetch_log.last_status = 'failed'` with
`'no CIK in SEC ticker map'` and all sources skip that ticker for the run.

### EDGAR companyfacts → financials row

XBRL concepts extracted (most recent 8 periods):
- `CashAndCashEquivalentsAtCarryingValue` (or fallback `Cash`)
- `ShortTermInvestments` (or `MarketableSecuritiesCurrent`)
- `Assets`, `Liabilities`
- `AccountsReceivableNetCurrent`
- `PropertyPlantAndEquipmentNet`
- `ResearchAndDevelopmentExpense`
- `GeneralAndAdministrativeExpense`
- `NetCashProvidedByUsedInOperatingActivities`
- `CommonStockSharesOutstanding` (or `EntityCommonStockSharesOutstanding`)

Computed:
- `cash_total_usd = cash_and_equivalents_usd + short_term_investments_usd`
- TTM aggregates (`rd_expense_ttm_usd`, `ga_expense_ttm_usd`, `operating_cf_ttm_usd`):
  written **only on the latest row** (sum 4 most-recent quarterly
  values, or take 10-K annual value directly).
- `quarterly_burn_usd = abs(operating_cf_ttm_usd) / 4` when negative
- `runway_months = cash_total_usd / (quarterly_burn_usd / 3.0)` when burn > 0

Edge cases:
- If `OperatingCashFlow` is positive (rare for biotech) → `runway_months = None`,
  `quarterly_burn_usd = None`
- If `Cash...` concept is missing → fall back to alternate alias
- Concepts missing entirely → write the row with whatever was extracted,
  set `fetch_status = 'partial'`, log to `fetch_log.last_error`

**Conditional GET (D56, infrastructure-only).** `fetch_companyfacts`
accepts `if_none_match` / `if_modified_since` from the prior fetch_log
row and stores any returned `ETag` / `Last-Modified` back into
`fetch_log.etag` / `fetch_log.last_modified`. A 304 response returns
`status='not_modified'` and skips the parse + write. **However: SEC's
`data.sec.gov` API explicitly disables caching** (`Cache-Control:
no-cache, no-store`, no `ETag`/`Last-Modified` in responses). The
infrastructure ships as a no-op until SEC changes their headers; cost
is two nullable DB columns and 4 always-null request bytes. See D56
for the empirical probe results.

### EDGAR submissions → form 4 + capital raises

`fetch_recent_filings(cik, since_date, forms)` returns the recent filings
list filtered by form ∈ `{'4', '8-K', 'S-3', 'S-3/A', '424B5'}` and
`filing_date >= since_date`.

Each filing dispatches to a form-specific parser:

- **Form 4** → `parse_form4_xml()` reads the OWNERSHIP DOCUMENT XML.
  SEC's `primaryDocument` for Form 4 is usually the XSLT-rendered HTML
  (e.g. `xslF345X05/ownership.xml`). The raw XML lives at the same
  filename **at the accession root** (no `xslF345X05/` prefix). **Per
  D56**, the fetch helper now: (1) tries the basename of `primary_doc`
  first (modern filings — succeeds in 1 call); (2) on 404, fetches
  `<accession>/index.json` once (~5 KB) and picks the actual `.xml`
  file (`_pick_form4_xml_name` prefers `primary_doc.xml`, then
  `ownership.xml`, then `wf-form4_*.xml`, then any `.xml`); (3) fetches
  that exact URL. Best case: 1 call. Worst case: 3. Replaces the prior
  guess-up-to-4-URLs chain that averaged 2-3 wasted 404s on legacy
  filings. One DB row per `(insider, transaction_date, txn_type, shares)`.
  Role classified from Form 4 boxes (CEO/CFO/Director/10% owner) +
  officer-title text.

- **8-K Items 1.01 / 3.02** → fetch the index, then the primary document
  excerpt (200KB max). Heuristic regex extracts gross proceeds + price
  per share. Failed parses set `fetch_status='partial'` with the raw URL
  preserved for manual review. `raise_type ∈ {'equity', 'debt', 'unknown'}`
  per Item code + body keywords (`"pre-funded"`, `"credit"`, `"note"`).

- **S-3 / S-3/A** → registration statement; `gross_proceeds_usd` lifted
  from cover page line "up to $X aggregate offering price" (regex).
  `raise_type='shelf'`.

- **424B5** → prospectus supplement off an existing S-3; the actual draw
  on shelf capacity. `raise_type='equity'` or `'pfw'` based on whether
  "pre-funded" appears in the title.

`shelf_registration_usd_capacity` (in `financials`) is computed at
write time as `MAX(S-3 gross_proceeds) - SUM(424B5 gross_proceeds)` over
the trailing 3-year window. Recomputed on every companyfacts run that
has fresh capital_raises rows.

---

## CLI — `scripts/4c_enrich_fundamentals.py`

```
usage: 4c_enrich_fundamentals.py [-h] [--quarter QUARTER]
                                  [--ticker TICKER | --tickers TICKERS]
                                  [--source SRC[,SRC...]]
                                  [--force-refresh] [--dry-run] [-v]

  --quarter QUARTER     Quarter YYYYQn (default: pipeline.yaml -> auto-latest)
  --ticker TICKER       Single ticker (overrides quarter feed)
  --tickers TICKERS     Comma-separated tickers
  --source SRC          Restrict to one or more sources
                        (companyfacts|submissions|form4|capital_raises);
                        default = all enabled in YAML
  --force-refresh       Ignore TTLs; re-fetch every selected (ticker, source)
  --dry-run             Resolve work plan + log; no HTTP, no DB writes
  -v                    Verbose
```

Default behavior: read `quarter` from `pipeline.yaml`, load the M4b
ranked feed, filter to biotech-industry tickers (via
`prices.db.ticker_snapshot`), fetch each (ticker, source) pair whose
`fetch_log.last_fetched_at` is older than the TTL, write to
`data/fundamentals.db`.

Failure-tolerant: a per-(ticker, source) failure logs to `fetch_log` and
continues. End-of-run summary lists `failed` tickers with copy-paste
retry commands (mirrors M6's D51 pattern).

---

## Run gate in `run_2_Funds_parser.bat`

After M4b's `4_rank.py` returns 0:

```bat
echo.
set /p RUN_M4C="Proceed to Module 4c (free SEC enrichment)? [Y/n]: "
if /i not "%RUN_M4C%"=="n" (
    echo [2_Funds_parser] Module 4c: enriching fundamentals...
    python scripts\4c_enrich_fundamentals.py -v
    if errorlevel 1 (
        echo [WARN] Module 4c reported errors; continuing to M5.
    )
)
```

Default-y: skipping is harmless (M5 falls back to empty fundamentals
block; M6 falls back to web_search).

---

## M5 wiring — `fundamentals` block in pack

New file `src/module_5/fundamentals.py`:

```python
def load_fundamentals_for_tickers(
    db_path: Path, tickers: list[str],
) -> dict[str, dict]:
    """Returns {ticker: fundamentals_dict} or {} if DB missing.

    fundamentals_dict shape:
    {
      "available": True/False,
      "as_of": "YYYY-MM-DD",                   # latest period_end_date in financials
      "fundamentals_fetched_at": "...Z",        # max(fetch_log.last_fetched_at)
      "financials": {
        "cash_and_equivalents_usd": ...,
        "short_term_investments_usd": ...,
        "cash_total_usd": ...,
        "quarterly_burn_usd": ...,
        "runway_months": ...,
        "operating_cf_ttm_usd": ...,
        "rd_expense_ttm_usd": ..., "ga_expense_ttm_usd": ...,
        "shelf_registration_usd_capacity": ...,
        "basic_shares_count": ...,
        "diluted_shares_count": ...,
        "prefunded_warrants_count": ...,         # may be null (heuristic)
        "fully_diluted_shares_count": ...,       # may be null
        "form": "10-Q"|"10-K", "period_end_date": "..."
      },
      "recent_capital_raises": [                 # last 365 days, oldest→newest
        {"date_iso": "...", "form": "8-K|S-3|424B5",
         "raise_type": "pfw|equity|debt|shelf|unknown",
         "gross_proceeds_usd": ..., "shares_issued": ...,
         "price_per_share_usd": ..., "description": "..."}
      ],
      "recent_insider_transactions": [           # last 365 days, oldest→newest
        {"date_iso": "...", "insider_name": "...", "role": "...",
         "txn_type": "buy|sell|option_exercise|option_grant|gift|other",
         "shares": ..., "price_usd": ..., "total_value_usd": ...}
      ],
      "data_status": {                           # per-source freshness
        "companyfacts":   {"fetched_at": "...", "status": "ok"},
        "submissions":    {"fetched_at": "...", "status": "ok"},
        "form4":          {...},
        "capital_raises": {...}
      }
    }
    """
```

`build_context_pack` (existing, in `src/module_5/packs.py`) gains a
parameter `fundamentals: dict | None = None`. When non-None, the pack's
JSON gains a top-level `"fundamentals"` key carrying the dict above.
When None or `available=False`, the pack omits the key (pack remains
backward-compatible).

`_HASH_FIELDS` extends with one new hashable field — the per-ticker
`fundamentals_fetched_at` ISO string (a single content-fingerprint
proxy) — so a fundamentals refresh invalidates the M5 cache for that
ticker. Adding the full fundamentals dict to the hash would over-bust
on every TTL tick; the `fetched_at` proxy is sufficient and matches
the existing `snapshot_fetched_at` precedent.

`run_enrichment` (existing, in `src/module_5/enrichment.py`) — bulk-load
`fundamentals` for all to-enrich tickers BEFORE the per-ticker loop:

```python
fundamentals_db = config.paths.data_dir / "fundamentals.db"
if fundamentals_db.exists():
    fundamentals_by_ticker = load_fundamentals_for_tickers(
        fundamentals_db, tickers
    )
else:
    fundamentals_by_ticker = {}
```

Then in the per-ticker loop:
```python
fundamentals = fundamentals_by_ticker.get(ticker)
pack = build_context_pack(row, ..., fundamentals=fundamentals)
```

**M6 prompt is unchanged this session** (m6-v3 stays). A future m6-v4
revision will add HARD RULES like "use `pack.fundamentals.financials.*`
as authoritative; only `web_search` for fields that are null". Deferred
to a paid validation pass — out of scope for the M4c build itself.

**Clinical trials, results, failures, interim/final readouts remain
LLM web_search territory** (per scope decision).

---

## File map

```
2_Funds_parser/
├── data/
│   └── fundamentals.db                    ← NEW (gitignored)
├── config/
│   └── fundamentals.yaml                  ← NEW
├── spec/
│   ├── module_4c_spec.md                  ← THIS FILE
│   └── decisions.md                       ← gains D54
├── src/
│   ├── layer_1/
│   │   └── edgar_13f.py                   ← unchanged; M4c imports _EDGAR_LIMITER from here
│   ├── module_4c/                         ← NEW package (4 modules — no ctgov_client)
│   │   ├── __init__.py
│   │   ├── fundamentals_db.py             ← schema, init, additive migrations, upsert helpers
│   │   ├── edgar_client.py                ← companyfacts + submissions + form4 + capital_raises
│   │   ├── industries.py                  ← biotech_industries gate (reads YAML + ticker_snapshot)
│   │   └── enrich.py                      ← orchestrator: per-ticker × per-source × TTL gate
│   └── module_5/
│       ├── packs.py                       ← extended: fundamentals param + _HASH_FIELDS
│       ├── enrichment.py                  ← extended: bulk-load fundamentals before pack loop
│       └── fundamentals.py                ← NEW: load_fundamentals_for_tickers()
├── scripts/
│   └── 4c_enrich_fundamentals.py          ← NEW CLI
└── run_2_Funds_parser.bat                 ← gains M4c y/N gate after M4b
```

---

## Acceptance tests

| # | Test | Expected |
|---|---|---|
| 1 | `python scripts/4c_enrich_fundamentals.py --ticker NTLA --dry-run -v` | Logs work plan: 4 sources × 1 ticker. No HTTP. No DB write. |
| 2 | First-run for NTLA (cold cache): `--ticker NTLA --source companyfacts -v` | `financials` rows written for the latest 8 periods. `fetch_log.(NTLA, companyfacts)` set to `ok`. |
| 3 | Re-run within 30 days for NTLA without `--force-refresh` | Skips companyfacts (TTL); 0 HTTP calls for that source. |
| 4 | Re-run with `--force-refresh` | Re-fetches; row updated; `fetched_at` advances. |
| 5 | Non-biotech ticker (e.g. `MMM` or `GRAL`) — `--ticker GRAL` | All 4 sources logged as `skipped_non_biotech` in `fetch_log`. No `financials`/`capital_raises`/`insider_transactions` rows written. |
| 6 | Ticker absent from SEC ticker map | `fetch_log.last_status = 'failed'` with `'no CIK in SEC ticker map'`. M5 gracefully falls back to empty fundamentals block. |
| 7 | EDGAR rate-limit shared with M2 layer_1 (run M2 ingest + M4c concurrently) | Combined throughput stays ≤9.5 req/s (single `_EDGAR_LIMITER`, rate bumped per D56). |
| 8 | M5 reads fundamentals for NTLA after M4c populates | Rebuilt pack contains `fundamentals.financials.cash_total_usd > 0`, `fundamentals.recent_insider_transactions` non-empty when Form 4 rows exist. |
| 9 | M5 cache hash bust on fundamentals refresh | After `4c --force-refresh --ticker NTLA`, M5 rebuilds NTLA pack (cache miss); other 6 tickers stay `cache_hit`. |
| 10 | EDGAR transient 5xx | `fetch_log.last_status='failed'`, `last_error` populated; the next run retries. |
| 11 | Form 4 XML rendered-vs-raw URL fallback | Fetcher tries basename first; on 404 fetches `<accession>/index.json` once and picks the right `.xml` (D56). |
| 12 | Concurrent fundamentals.db writers | SQLite WAL mode (set in `init_fundamentals_db`); single-writer enforcement on inserts. |

---

## Open items before / during implementation

1. **EDGAR companyfacts is 10-Q centric — annual filers.** Some biotechs
   report 20-F (foreign private issuer; e.g. LEGN files 20-F as a Cayman
   Islands company). The `companyfacts.json` payload is form-agnostic
   (XBRL concepts only), but the period-end dates come from the underlying
   filings. v1 accepts whatever periods companyfacts returns; the
   `period` column matches what SEC publishes (e.g. `'FY2024'` for a 20-F
   filer). M5 takes "the latest period_end_date" regardless of form.

2. **PFW count from 10-Q footnote.** SEC XBRL doesn't have a standard
   tag for pre-funded warrants. v1 leaves `prefunded_warrants_count` and
   `fully_diluted_shares_count` NULL; M6 still web-searches when the
   prompt asks for them. v2 may add a 10-Q text-mining heuristic
   (parse exhibit for `"pre-funded warrant"` near integer matches);
   v3 may move to a paid structured-data provider.

3. **8-K Item 3.02 unstructured.** The Item 3.02 text varies wildly
   (`"...issued 5,000,000 shares at $1.50..."`). v1 regex extracts shares
   + price; falls back to `partial` and writes the raw URL. Failed
   parses persist for human triage via the `fetch_status='partial'`
   marker and the `raw_filing_url` link.

4. **Form 4 cold-start volume.** A 3-year insider history for a
   well-funded biotech can be 200+ filings × 5+ rows each. The first
   M4c run for the 7 dispatched tickers is bounded (~1,000 EDGAR calls
   at 9 req/s = ~2 min). Full feed (98 tickers) cold-start estimate:
   **~20 min**. Subsequent runs: minutes.

5. **Concurrent EDGAR limiter sharing.** Both `layer_1.edgar_13f`
   (M2) and `module_4c.edgar_client` will hold references to
   `_EDGAR_LIMITER`. Since it's a module-scope singleton in
   `edgar_13f.py`, importing it once is fine; importing it
   in two places gives both modules the SAME instance (Python module
   import semantics). No refactor needed unless we ever fork the rate
   per source.

6. **Companyfacts 404.** Some tickers map to a CIK but EDGAR has no
   companyfacts record (very-small or recently-IPO'd issuers). 404 is
   logged + treated as `partial` (`fetch_log.last_error = 'no companyfacts'`),
   submissions/form4 still proceed.

7. **Clinical data NOT ingested.** Per scope decision, M4c does not
   touch ClinicalTrials.gov, does not parse `past_failures`, does not
   collect interim/final readouts. M6 keeps web-searching for those.
   The cost-reduction win is therefore ~20% per ticker (financials
   searches saved), not the ~3× originally projected.

---

## Decisions log entry

> **D54 — Module 4c fundamentals enrichment (biotech, financials-only)**
> Spec: [module_4c_spec.md](module_4c_spec.md). New step between M4b
> and M5. Pre-fetches **financial** data only — SEC EDGAR companyfacts
> XBRL (cash, R&D, G&A, op CF, shares), submissions, Form 4 insider
> transactions, 8-K Items 1.01/3.02 capital raises, S-3 shelves — for
> tickers in `biotech_industries:`, into a new `data/fundamentals.db`
> with **four** tables (`financials`, `capital_raises`,
> `insider_transactions`, `fetch_log`). Per-row `fetched_at` +
> per-source TTL (mirrors M4a/M2 patterns); not per-quarter wholesale
> refresh. All raw responses stored as TEXT inside the DB (per D32; no
> `*.json` files on disk). EDGAR rate-limit: reuses the existing
> `_EDGAR_LIMITER` (9 req/s) from `src/layer_1/edgar_13f.py`.
>
> **Clinical-trial data is OUT of scope** — M4c does NOT call
> ClinicalTrials.gov; does NOT parse interim/final readouts; does NOT
> classify clinical_holds / endpoint_misses. Those continue to be
> populated by M6's `web_search`. Rationale: M6 is better positioned
> for clinical synthesis (multi-source weighting, recency checks
> against IR press releases, mechanism interpretation). The cost win
> is ~20% per ticker (financials searches only), not the ~3×
> originally projected.
>
> M5 reads from `data/fundamentals.db` and injects a `fundamentals`
> block into each pack (when biotech and present); `_HASH_FIELDS`
> extends with `fundamentals_fetched_at` so refreshes invalidate M5
> cache. M6 prompt unchanged this session — m6-v4 prompt revision
> deferred to a paid validation pass. Non-biotech tickers are
> logged-and-skipped; M5 emits packs without the fundamentals block;
> M6 falls back to `web_search` (existing m6-v3 behaviour).

---

## Cross-cutting rules (do not re-litigate)

1. **D32**: SQL-only storage; raw JSON lives in TEXT columns inside the DB.
2. **`*.db` is gitignored.** `fundamentals.db` is no exception.
3. **Schema evolves additively** via `_apply_additive_migrations`.
4. **EDGAR rate-limit is shared.** Reuse `_EDGAR_LIMITER` from `layer_1/edgar_13f.py`.
5. **Output folder convention.** `data/fundamentals.db` is durable cache;
   no user-facing artifact lives in `Outputs/` for M4c.
6. **No paid API calls** in M4c (SEC is free).
7. **Biotech-only scope.** Non-biotech tickers skip cleanly; pipeline
   still works end-to-end without their fundamentals.
8. **Fail-open semantics.** A source failure for one ticker does not
   block other tickers or other sources.
9. **Financials-only scope.** No clinical-trial parsing in M4c.

---

## Implementation order

1. `config/fundamentals.yaml` with v1 defaults (4 sources, no ctgov)
2. `src/module_4c/fundamentals_db.py` — schema (4 tables) + init + additive migrations + upsert helpers
3. `src/module_4c/industries.py` — biotech gate (read prices.db.ticker_snapshot.industry)
4. `src/module_4c/edgar_client.py` — ticker→CIK + companyfacts + submissions + form4 + capital_raises (no ctgov)
5. `src/module_4c/enrich.py` — orchestrator (per-ticker × per-source × TTL)
6. `src/module_4c/__init__.py` — exports `run_enrichment_4c`, `init_fundamentals_db`
7. `scripts/4c_enrich_fundamentals.py` — CLI
8. `src/module_5/fundamentals.py` — `load_fundamentals_for_tickers`
9. Extend `src/module_5/packs.py` — `fundamentals` param + `_HASH_FIELDS`
10. Extend `src/module_5/enrichment.py` — bulk-load fundamentals before pack loop
11. Extend `run_2_Funds_parser.bat` — M4c y/N gate after M4b
12. Add D54 to `decisions.md`
13. Backfill 2025Q4 for the 7 dispatched tickers; verify M5 cache busts and re-emits the 6 biotech packs with `fundamentals` block populated; the 1 non-biotech (GRAL) pack stays without.
