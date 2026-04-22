# 2_Funds_parser — Layer 1: 13F-HR holdings ingest

**Scope.** For every fund in the Layer 0 registry, download every
13F-HR quarterly filing in a date window from SEC EDGAR, parse the
information-table XML, resolve each CUSIP to a ticker via OpenFIGI,
and persist the positions (fund, filing_date, ticker, cusip, shares,
market_value) into SQLite. Running the script a second time with no
new SEC filings is a no-op.

**Design intent.** Port the proven logic from
`1_Stock_Picker/src/layer_minus1/edgar_13f_parser.py` and
`cusip_resolver.py` into this project with **no policy layer on top**.
This means: no tier/multiplier, no change-type classification, no
TWOS scoring, no primary-coverage weighting. Just the raw positions
in whole USD.

---

## Data flow

```
funds.cik
   │
   ▼
EDGAR /submissions/CIK{10-digit}.json        (filings list)
   │                                          filter form="13F-HR"
   │                                          filter filing_date ∈ [from, to]
   ▼
EDGAR /Archives/.../index.json               (locate info-table XML)
   │
   ▼
EDGAR /Archives/.../<name>.xml               (13F information table)
   │                                          parse <infoTable> rows
   │                                          SH only (skip PRN)
   ▼
OpenFIGI /v3/mapping   (cached in cusip_ticker_map)
   │                    ID_CUSIP → US equity ticker
   │                    reject ETF/Fund/Trust/Preferred/Warrant/...
   ▼
holdings (fund_id, filing_date, cusip, ticker, shares, market_value)
filings_log (fund_id, accession_number, parse_status, ...)
```

**filing_date only.** Matches 1_Stock_Picker critical constraint #1:
we filter on `filing_date` (when the filer actually filed), not on
`period_of_report` (which can be months earlier and gets amended).
`period_of_report` is captured and stored but never used as a filter.

**Market-value unit.** SEC Release 34-93978 (effective 2023-01-03)
changed the 13F `<value>` column from thousands-of-USD to whole USD.
Filings dated strictly before the cutoff are multiplied by 1000 so
`holdings.market_value` is always raw USD. Cutoff lives in
[`database.db.MARKET_VALUE_RAW_USD_CUTOFF`](../src/database/db.py).

---

## Schema (added in Layer 1)

File: [src/database/schema.sql](../src/database/schema.sql)

```sql
CREATE TABLE holdings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id           INTEGER NOT NULL REFERENCES funds(id),
    filing_date       TEXT NOT NULL,
    period_of_report  TEXT NOT NULL,
    name_of_issuer    TEXT,          -- added via additive migration
    ticker            TEXT,
    ticker_source     TEXT,          -- 'openfigi' | 'sec_name' | 'manual' | NULL
    cusip             TEXT NOT NULL,
    shares            INTEGER,
    market_value      INTEGER,       -- raw USD after normalisation
    title_of_class    TEXT,          -- 13F <titleOfClass>: 'COM', 'PFD', 'WT', etc.
    put_call          TEXT,          -- 13F <putCall>: 'Put' | 'Call' | NULL
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (fund_id, filing_date, cusip)
);

CREATE TABLE filings_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id           INTEGER NOT NULL REFERENCES funds(id),
    filing_date       TEXT NOT NULL,
    period_of_report  TEXT NOT NULL,
    accession_number  TEXT NOT NULL,
    document_url      TEXT,
    holdings_count    INTEGER NOT NULL DEFAULT 0,
    parse_status      TEXT NOT NULL DEFAULT 'success',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (fund_id, accession_number)
);

CREATE TABLE cusip_ticker_map (
    cusip          TEXT PRIMARY KEY,
    ticker         TEXT,          -- NULL for resolved-but-non-equity
    exchange       TEXT,
    security_type  TEXT,
    resolved_date  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
```

- `UNIQUE(fund_id, accession_number)` on `filings_log` is the dedup
  key: if a row exists for `(fund, accession)`, we do not re-download
  regardless of whether the parse succeeded, was empty, or errored.
  To force a re-parse, delete that row manually.
- `UNIQUE(fund_id, filing_date, cusip)` on `holdings` protects against
  duplicate XML-row artifacts and lets pre-seed use `INSERT OR IGNORE`.
- `ticker` on `holdings` is nullable because a CUSIP may resolve to a
  non-equity OpenFIGI record (ETF, preferred, etc.) which we
  deliberately keep as a position but without a ticker — the shares
  and market_value are still useful for sleeve accounting.
- `cusip_ticker_map.ticker` is nullable for the same reason; a NULL
  row means "we asked OpenFIGI and it was not a tracked US-equity
  common stock". These NULL rows are important: without them we
  would re-query every non-equity CUSIP on every run.

---

## Modules

### [src/layer_1/edgar_13f.py](../src/layer_1/edgar_13f.py)

Public surface:
- `fetch_fund_filings(cik, from_date, to_date)` — return filings list
  from `/submissions/CIK{padded}.json`.
- `find_information_table_url(filing)` — walk the filing's
  `index.json` to find the info-table XML.
- `download_13f_document(url)` / `parse_13f_xml(xml_content)` —
  fetch + parse helpers; `parse_13f_xml` returns `[]` on malformed
  XML instead of raising. Each parsed row carries
  `{name_of_issuer, cusip, shares, market_value, title_of_class,
  put_call}` — the last two fields are extracted from the 13F
  `<titleOfClass>` and `<putCall>` elements (put_call is NULL for
  common stock, `"Put"` or `"Call"` for option positions). The
  existing `sshPrnamtType == "SH"` filter is preserved — principal-
  amount (PRN) rows are still dropped.
- `backfill_missing_issuer_names(conn)` — one-time backfill of the
  `name_of_issuer` column for rows ingested before the column existed.
- `backfill_share_type_fields(conn)` — one-time backfill of
  `title_of_class` + `put_call` for rows ingested before those
  columns existed. Re-fetches each filing's XML once and UPDATEs by
  CUSIP; idempotent (no-op once `title_of_class` is populated).
- `backfill_tickers_by_sec_name(conn)` — resolve unmatched CUSIPs
  via SEC `company_tickers.json`, stamps `ticker_source='sec_name'`.
- `ingest_all_funds(conn, from_date, to_date)` — orchestrator.
  Runs all three backfills in order, then iterates `funds` rows,
  deduplicates via `filings_log`, resolves CUSIPs, writes `holdings`
  and `filings_log`. Returns a per-fund summary dict plus
  `_backfill` / `_share_type_backfill` / `_ticker_backfill` stats.

Constants:
- `EDGAR_USER_AGENT = "StockPicker contact@stockpicker.local"`
- `EDGAR_RATE_LIMIT_SLEEP = 0.11` (SEC fair-use: ≤10 req/sec)
- `DEFAULT_FROM_DATE = "2025-01-01"` — see *Start-date default* below.

All network calls accept an `http_get` callable for test injection.

### [src/layer_1/cusip_resolver.py](../src/layer_1/cusip_resolver.py)

- `resolve_cusip(cusip, conn)` — cache-first single lookup.
- `resolve_cusip_batch(cusips, conn)` — batches uncached CUSIPs into
  10-item POSTs to OpenFIGI, 2.4s between batches (25 req/min free
  tier), and writes the full result to `cusip_ticker_map`.

**Fallback when OpenFIGI returns NULL** — ~40% of 13F CUSIPs have
no OpenFIGI match, mostly because the CUSIP refers to a non-equity
instrument (preferred, warrant, unit, ETF, trust), a delisted issuer,
or a foreign-only listing. For those holdings, a second pass
[`backfill_tickers_by_sec_name`](../src/layer_1/edgar_13f.py) looks
the `name_of_issuer` up in SEC's
[`company_tickers.json`](../Outputs/sec_company_tickers.json)
(cached ~weekly, fetched via [sec_ticker_resolver.py](../src/layer_1/sec_ticker_resolver.py)).
Exact match after lowercasing, punctuation strip, and entity-suffix
strip (`Inc`, `Corp`, `plc`, `Therapeutics`, etc.).

Rows populated this way get `ticker_source = 'sec_name'` (vs
`'openfigi'` for the primary path). The HTML report renders them in
orange italic with an asterisk and a hover tooltip, because when the
CUSIP is actually for a non-common-stock instrument the name match
will return the **common-stock** ticker of the same issuer — a
different security. Current recovery: ~9% of holdings move from
NULL ticker to `sec_name` ticker; the remaining ~30% are delisted
or acquired issuers SEC no longer lists.

Filtering rules (preserved verbatim from 1_Stock_Picker):
- Accept only `exchCode ∈ {US, UN, UA, UW, UR}`.
- Accept only `securityType` or `securityType2` equal to
  `"Common Stock"` or `"Depositary Receipt"`.
- Reject if *either* `securityType` field contains any of:
  `ETF, ETP, Fund, Trust, Note, Bond, Preferred, Right, Warrant, Unit`.
  The reject list runs *before* the accept list so composite strings
  like `"ETF Common Stock"` cannot slip through.
- No match → cache `(cusip, NULL, NULL, NULL)` with today's
  `resolved_date`. A NULL cache entry is as authoritative as a hit
  and will not be re-queried.

---

## Start-date default

`DEFAULT_FROM_DATE = "2025-01-01"` — matches the default used by
1_Stock_Picker's `ingest_all_institutions`. The reasoning there was
that pre-2025 filings are stale for catalyst attribution purposes.
For 2_Funds_parser the scope is broader (fund-following, not
catalyst extraction), so this default is a compromise to keep the
first-run EDGAR traffic bounded (~5 quarterly filings per fund *
22 funds ≈ 110 filings + their XMLs).

Override with `--from-date YYYY-MM-DD` on the driver. Lower values
are safe but will pull more filings and more CUSIPs through
OpenFIGI's rate limit.

---

## Scripts

### [scripts/2_ingest_13f.py](../scripts/2_ingest_13f.py)

User-triggered driver. Opens a DB connection, calls
`ingest_all_funds`, prints a per-fund summary and a total line.

```
python 2_Funds_parser/scripts/2_ingest_13f.py
python 2_Funds_parser/scripts/2_ingest_13f.py --from-date 2024-01-01
python 2_Funds_parser/scripts/2_ingest_13f.py --verbose
```

Idempotent: runs after the first have nothing new to download unless
a fund has filed a fresh 13F-HR since the last run.

### [scripts/2_import_from_stockpicker.py](../scripts/2_import_from_stockpicker.py)

**One-shot** pre-seed from `1_Stock_Picker/stockpicker.db`. Intended
to be run *once*, before the first `2_ingest_13f.py` call, so the
first ingest doesn't re-hit EDGAR/OpenFIGI for data we already have.

Overlap at time of writing (2026-04-21): 12 of 22 funds share a CIK
with an institution in the Stock Picker DB. For those 12, this
script copies:
- All `filings_log` rows (remapping `institution_id` → `fund_id`).
- All `institution_holdings` rows (renamed to `holdings`, remapped).
- The entire `cusip_ticker_map` (fund-independent; every copied row
  saves an OpenFIGI round-trip regardless of which fund holds it).

Safe to re-run: every insert uses `INSERT OR IGNORE` against the
UNIQUE constraints.

```
python 2_Funds_parser/scripts/2_import_from_stockpicker.py
python 2_Funds_parser/scripts/2_import_from_stockpicker.py \
    --source ../1_Stock_Picker/stockpicker.db
```

After pre-seed, subsequent `2_ingest_13f.py` runs will consult EDGAR
only for the 10 non-overlapping funds (first run) and for any fresh
filings since the pre-seed snapshot.

---

## Recommended first-run order

```
1. python 2_Funds_parser/scripts/2_seed_funds.py               # Layer 0
2. python 2_Funds_parser/scripts/2_import_from_stockpicker.py  # copy
3. python 2_Funds_parser/scripts/2_ingest_13f.py               # top-up
```

Step 3 will process zero filings for the 12 overlapping funds
(blocked by `filings_log` dedup) and the full ~25 filings each for
the 10 non-overlapping funds.

---

## Differences from 1_Stock_Picker Layer −1

| Concern                   | 1_Stock_Picker                                                   | 2_Funds_parser                              |
|---------------------------|-------------------------------------------------------------------|---------------------------------------------|
| Ingest entry point        | `ingest_all_institutions`                                         | `ingest_all_funds`                          |
| Driver script             | Called by `daily_orchestrator.py`                                | Manual CLI only                             |
| Target table              | `institution_holdings`                                            | `holdings`                                  |
| Foreign key               | `institution_id`                                                  | `fund_id`                                   |
| Tier / multiplier         | `tier`, `multiplier`, `processing_tier` on `institutions`         | Not modelled                                |
| Downstream scoring        | Feeds `twos_scores`, Form 4 / 13G / 13D flow                      | Not in scope                                |
| Non-equity treatment      | Stored without ticker; filtered out of TWOS                       | Stored without ticker; no downstream filter |
| Pre-seed                  | n/a (origin data)                                                 | `2_import_from_stockpicker.py` (this layer) |

---

## HTML report

Driven by [scripts/2_build_report.py](../scripts/2_build_report.py).
Renders [Outputs/2_funds_report.html](../Outputs/2_funds_report.html)
— a single self-contained HTML file with 22 tabs (one per fund).
Each tab shows:

- CIK, legal name.
- Latest filing date, period of report, accession, and a direct link
  to the EDGAR info-table XML.
- A table of positions sorted by descending market value:
  company (name_of_issuer), ticker, CUSIP, shares, market value (USD).
- If a fund has no filings yet (typical for the 10 non-overlapping
  funds on a fresh install), the tab renders an empty placeholder
  saying "Fields will populate after the next successful ingest."

**Cache.** A sidecar `Outputs/2_funds_report.cache.json` stores the
per-fund `(latest_filing_date, n_filings)` tuple. If the signature
matches on the next run, the HTML is not rewritten. Delete the cache
file or pass `--force` to rebuild.

**Column order mirrors the SEC 13F information table** — Name of
Issuer sits directly in front of CUSIP, matching how the filing
itself is laid out. The full column order is: Name of Issuer, CUSIP,
Ticker, Shares, Market value (USD). Rows are sorted ascending by
Name of Issuer (case-insensitive); holdings whose name has not yet
been retrieved fall to the bottom of the table with blank cells.

**Name-of-issuer backfill.** `name_of_issuer` was added to the
schema after the Layer-1 pre-seed from 1_Stock_Picker, so the
24,235 pre-seeded rows originally had NULL in that column.
[`ingest_all_funds`](../src/layer_1/edgar_13f.py) runs a
`backfill_missing_issuer_names` pass on every invocation: it finds
any filing whose holdings still have NULL `name_of_issuer`, re-fetches
its info-table XML from SEC EDGAR (one request per filing), and
UPDATEs the matching rows by CUSIP. The guard `WHERE name_of_issuer
IS NULL` makes the pass a no-op after the first successful run. At
bootstrap time this is ~296 EDGAR requests, ≈33 seconds at the 0.11s
rate-limit sleep.

---

## .bat integration

[run_2_Funds_parser.bat](../run_2_Funds_parser.bat) now drives the
full daily flow:

1. Activate `..\.venv\`.
2. `python scripts\2_ingest_13f.py` — idempotent EDGAR top-up.
3. `python scripts\2_build_report.py` — idempotent report rebuild.

The seed script (`2_seed_funds.py`) is deliberately **not** called by
the .bat — the Excel registry is a manual source of truth and should
only be re-seeded when column A changes. Same for
`2_import_from_stockpicker.py`, which is one-shot.

---

## Out of scope for Layer 1

- 13G / 13D / Form 4 flow — those are Stock Picker Step 1.2–1.3, not
  part of the funds parser.
- Per-filing change-type classification (`NEW_BUY` / `INCREASE` /
  `EXIT` / ...) — can be derived downstream from `holdings` joined
  to itself on prior filing_date.
- Price joins, valuation-at-today — no price feed in this project.
- Scheduling. The .bat stub still prints "not wired yet"; ingest is
  a manual operation until the user asks for a scheduled run.

---

## Known failure modes

- **OpenFIGI rate-limit exhaustion.** The free tier is 25 req/min; a
  large first run can saturate it. The resolver caches NULL on any
  batch failure, so re-running recovers gracefully.
- **SEC submissions-index pagination.** `recent` returns up to ~1000
  filings. For funds that file very frequently, older filings are in
  `files[]` with separate URLs. 1_Stock_Picker never needed this;
  if a fund in this registry exceeds it, add pagination before
  running with `--from-date` earlier than 2020.
- **Malformed XML.** `parse_13f_xml` catches `ET.ParseError` and
  returns `[]`; the filing is logged as `parse_status='empty'` and
  not retried. Unusual exceptions are logged `parse_status='parse_error'`.
- **Post-2023 market-value regression.** If a filer reports in
  thousands after the cutoff (non-compliant), values will be 1000×
  too small. No automatic detection; would surface as implausibly
  small positions in downstream reports.
