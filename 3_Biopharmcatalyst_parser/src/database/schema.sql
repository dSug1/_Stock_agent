-- 3_Biopharmcatalyst_parser — database schema
-- 8 tables per spec §2. Idempotent via IF NOT EXISTS.

-- §2.1 catalyst_snapshots — every row of every BPC catalyst CSV
-- download, tagged by snapshot_date. PK identifies a specific
-- expected readout for a specific drug as observed on a given day.
CREATE TABLE IF NOT EXISTS catalyst_snapshots (
    snapshot_date         DATE    NOT NULL,
    ticker                TEXT    NOT NULL,
    drug                  TEXT    NOT NULL,
    nct_number            TEXT    NOT NULL,  -- '' when blank in source (not NULL — PK requires it)
    next_catalyst_type    TEXT    NOT NULL,  -- 'Interim Data', 'Initial Data', 'PDUFA', etc.
    name                  TEXT,
    price                 REAL,
    price_history_30d     TEXT,              -- raw semicolon-separated string preserved
    indication            TEXT,
    stage                 TEXT,              -- 'phase1'..'phase5'
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

CREATE INDEX IF NOT EXISTS idx_catalyst_ticker  ON catalyst_snapshots (ticker);
CREATE INDEX IF NOT EXISTS idx_catalyst_date    ON catalyst_snapshots (catalyst_date);
CREATE INDEX IF NOT EXISTS idx_catalyst_latest  ON catalyst_snapshots (snapshot_date DESC, ticker);

-- §2.2 edgar_form4_filings — one row per Form 4 filing (parent).
CREATE TABLE IF NOT EXISTS edgar_form4_filings (
    accession_number      TEXT    PRIMARY KEY,  -- e.g. '0001127602-26-012345'
    cik_issuer            TEXT    NOT NULL,     -- zero-padded 10-digit
    ticker                TEXT,                 -- resolved at fetch time
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

CREATE INDEX IF NOT EXISTS idx_form4_ticker_date ON edgar_form4_filings (ticker, filed_date DESC);
CREATE INDEX IF NOT EXISTS idx_form4_cik         ON edgar_form4_filings (cik_issuer);

-- §2.3 edgar_form4_transactions — one row per non-derivative txn.
CREATE TABLE IF NOT EXISTS edgar_form4_transactions (
    transaction_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    accession_number          TEXT    NOT NULL,
    transaction_date          DATE    NOT NULL,
    transaction_code          TEXT    NOT NULL,  -- 'P','S','A','M','F','D',...
    transaction_code_meaning  TEXT,              -- denormalized human-readable
    acquired_disposed         TEXT,              -- 'A' or 'D'
    shares                    REAL,
    price_per_share           REAL,
    shares_owned_following    INTEGER,
    is_open_market            BOOLEAN,           -- TRUE iff code IN ('P','S')
    direct_or_indirect        TEXT,              -- 'D' or 'I'
    FOREIGN KEY (accession_number) REFERENCES edgar_form4_filings(accession_number)
);

CREATE INDEX IF NOT EXISTS idx_txn_accession ON edgar_form4_transactions (accession_number);
CREATE INDEX IF NOT EXISTS idx_txn_code_date ON edgar_form4_transactions (transaction_code, transaction_date DESC);

-- §2.4 edgar_ownership_filings — 13D/13G metadata. percent_of_class
-- NULL in v1 (HTML parsing deferred).
CREATE TABLE IF NOT EXISTS edgar_ownership_filings (
    accession_number      TEXT    PRIMARY KEY,
    cik_issuer            TEXT    NOT NULL,
    ticker                TEXT,
    issuer_name           TEXT,
    form_type             TEXT    NOT NULL,     -- 'SC 13D','SC 13G','SC 13D/A','SC 13G/A'
    filed_date            DATE    NOT NULL,
    filer_name            TEXT,                 -- best-effort from filing index
    filing_url            TEXT    NOT NULL,
    percent_of_class      REAL,                 -- NULL in v1
    fetched_at            TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ownership_ticker_date ON edgar_ownership_filings (ticker, filed_date DESC);

-- §2.5 bpc_insider_supplement — manually-extracted BPC insider CSV,
-- kept separate from EDGAR data so disagreements are auditable.
CREATE TABLE IF NOT EXISTS bpc_insider_supplement (
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
    final_shares          INTEGER,
    no_of_shares          INTEGER,
    PRIMARY KEY (snapshot_date, ticker, insider_name, filing_date, buy_sell, stock_or_option, shares)
);

-- §2.6 ingest_log — audit table written by every ingest run.
CREATE TABLE IF NOT EXISTS ingest_log (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    module          TEXT    NOT NULL,    -- 'catalysts','edgar_form4','compute_timing',...
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          TEXT    NOT NULL,    -- 'success','partial','failed'
    input_ref       TEXT,                -- CSV filename, or ticker list summary, or snapshot_date
    rows_in         INTEGER,
    rows_inserted   INTEGER,
    rows_updated    INTEGER,
    rows_rejected   INTEGER,
    error_message   TEXT
);

-- §2.7 ticker_cik_map — cached ticker→CIK mapping, refreshed weekly.
CREATE TABLE IF NOT EXISTS ticker_cik_map (
    ticker          TEXT    PRIMARY KEY,
    cik             TEXT    NOT NULL,    -- zero-padded 10-digit
    name            TEXT,
    last_refreshed  TIMESTAMP NOT NULL
);

-- §2.8 catalyst_timing — Module 5 output; one row per catalyst_snapshots row.
-- date_min/date_max NULL only when precision_tier='unknown'.
CREATE TABLE IF NOT EXISTS catalyst_timing (
    snapshot_date         DATE    NOT NULL,
    ticker                TEXT    NOT NULL,
    drug                  TEXT    NOT NULL,
    nct_number            TEXT    NOT NULL,
    next_catalyst_type    TEXT    NOT NULL,
    date_min              DATE,
    date_max              DATE,
    precision_tier        TEXT    NOT NULL,   -- 'specific'|'conference'|'month'|'quarter'|'half'|'year'|'unknown'
    source_lane           TEXT    NOT NULL,   -- 'conference'|'catalyst_date_specific'|'text_parse'|'catalyst_date_bucket'|'unknown'
    matched_phrase        TEXT,
    computed_at           TIMESTAMP NOT NULL,
    rules_version         TEXT    NOT NULL,   -- 'v1.0' etc., bumped when rules change
    PRIMARY KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type),
    FOREIGN KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        REFERENCES catalyst_snapshots(snapshot_date, ticker, drug, nct_number, next_catalyst_type)
);

CREATE INDEX IF NOT EXISTS idx_timing_dates ON catalyst_timing (date_min, date_max);
CREATE INDEX IF NOT EXISTS idx_timing_tier  ON catalyst_timing (precision_tier);
