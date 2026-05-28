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
-- PK includes final_shares per D4 (spec calibration): otherwise legitimate
-- same-day partial fills that end at different post-trade positions
-- collide and get silently coalesced.
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
    final_shares          INTEGER NOT NULL,
    no_of_shares          INTEGER,
    PRIMARY KEY (snapshot_date, ticker, insider_name, filing_date, buy_sell,
                 stock_or_option, shares, final_shares)
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

-- §2.9 catalyst_scores — Module 6 output. One row per catalyst_snapshots
-- row at the target snapshot_date. Rows that fail any hard filter still
-- get a row (hard_pass=0, fail_reasons populated, scoring columns NULL)
-- so the user can audit exclusions. Spec §12.5.
CREATE TABLE IF NOT EXISTS catalyst_scores (
    snapshot_date               DATE    NOT NULL,
    ticker                      TEXT    NOT NULL,
    drug                        TEXT    NOT NULL,
    nct_number                  TEXT    NOT NULL,
    next_catalyst_type          TEXT    NOT NULL,
    hard_pass                   BOOLEAN NOT NULL,
    fail_reasons                TEXT,
    timing_bucket               TEXT,
    insider_gross_weighted_usd  REAL,
    insider_score               REAL,
    return_30d_pct              REAL,
    momentum_score              REAL,
    fund_quarter_latest         TEXT,
    fund_quarter_previous       TEXT,
    funds_holding_latest        INTEGER,
    funds_holding_previous      INTEGER,
    fund_accumulation_usd       REAL,
    fund_accumulation_score     REAL,
    composite_score             REAL,
    computed_at                 TIMESTAMP NOT NULL,
    rules_version               TEXT    NOT NULL,
    PRIMARY KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type),
    FOREIGN KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        REFERENCES catalyst_snapshots(snapshot_date, ticker, drug, nct_number, next_catalyst_type)
);

CREATE INDEX IF NOT EXISTS idx_scores_composite ON catalyst_scores (snapshot_date, composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_bucket    ON catalyst_scores (timing_bucket, hard_pass);

-- ============================================================
-- Views (spec §6.7) — created by Module 4's schema bootstrap, but
-- live in schema.sql so M0's init applies them on every connect.
-- ============================================================

-- v_latest_catalysts: most recent snapshot per (ticker, drug, nct, type).
-- Downstream modules read this when they want "current best truth" without
-- having to GROUP BY MAX(snapshot_date) themselves.
DROP VIEW IF EXISTS v_latest_catalysts;
CREATE VIEW v_latest_catalysts AS
SELECT s.*
FROM catalyst_snapshots s
JOIN (
    SELECT ticker, drug, nct_number, next_catalyst_type,
           MAX(snapshot_date) AS latest
    FROM catalyst_snapshots
    GROUP BY ticker, drug, nct_number, next_catalyst_type
) m
  ON s.ticker             = m.ticker
 AND s.drug               = m.drug
 AND s.nct_number         = m.nct_number
 AND s.next_catalyst_type = m.next_catalyst_type
 AND s.snapshot_date      = m.latest;

-- v_insider_signal_combined: union of EDGAR Form 4 (open-market only) +
-- BPC insider supplement (stock only), tagged by source so disagreements
-- between the two feeds remain auditable. Column harmonisation notes:
--   * EDGAR keeps both filed_date (the SEC-stamped legal record) AND
--     transaction_date (the trade day, usually 1-2 days earlier).
--   * BPC only captures the filing date; transaction_date is NULL.
--   * source_ref lets you trace back: EDGAR accession_number, or the
--     BPC snapshot_date that supplied the row.
-- v_executive_open_market_trades: same UNION as v_insider_signal_combined,
-- but adds an `executive_role` classifier column (CEO / CFO / COO / CMO /
-- CSO / President / Chair / 10% owner / Director / Other officer / Other)
-- derived from the freeform position text AND, for EDGAR rows, the
-- structured is_director / is_officer / is_ten_percent_owner flags. Also
-- adds a pre-computed `gross_usd = shares * trade_price` column for
-- ranking. Title-pattern detection comes first so a CEO who's also a
-- director shows up as "CEO" not "Director". See decisions.md D7.
DROP VIEW IF EXISTS v_executive_open_market_trades;
CREATE VIEW v_executive_open_market_trades AS
SELECT
    source,
    ticker,
    insider_name,
    insider_position,
    filing_date,
    transaction_date,
    buy_sell,
    shares,
    trade_price,
    (shares * trade_price)              AS gross_usd,
    source_ref,
    CASE
        WHEN UPPER(COALESCE(insider_position, '')) LIKE '%CHIEF EXECUTIVE%'
          OR UPPER(COALESCE(insider_position, '')) LIKE '%CEO%' THEN 'CEO'
        WHEN UPPER(COALESCE(insider_position, '')) LIKE '%CHIEF FINANCIAL%'
          OR UPPER(COALESCE(insider_position, '')) LIKE '%CFO%' THEN 'CFO'
        WHEN UPPER(COALESCE(insider_position, '')) LIKE '%CHIEF OPERATING%'
          OR UPPER(COALESCE(insider_position, '')) LIKE '%COO%' THEN 'COO'
        WHEN UPPER(COALESCE(insider_position, '')) LIKE '%CHIEF MEDICAL%'
          OR UPPER(COALESCE(insider_position, '')) LIKE '%CMO%' THEN 'CMO'
        WHEN UPPER(COALESCE(insider_position, '')) LIKE '%CHIEF SCIENTIFIC%'
          OR UPPER(COALESCE(insider_position, '')) LIKE '%CSO%' THEN 'CSO'
        WHEN UPPER(COALESCE(insider_position, '')) LIKE '%PRESIDENT%' THEN 'President'
        WHEN UPPER(COALESCE(insider_position, '')) LIKE '%CHAIR%' THEN 'Chair'
        WHEN is_ten_percent_owner_flag = 1 THEN '10% owner'
        WHEN UPPER(COALESCE(insider_position, '')) LIKE '%DIRECTOR%' THEN 'Director'
        WHEN is_director_flag = 1 THEN 'Director'
        WHEN is_officer_flag = 1 THEN 'Other officer'
        ELSE 'Other'
    END AS executive_role
FROM (
    SELECT
        'edgar' AS source,
        f.ticker,
        f.reporting_owner_name AS insider_name,
        f.officer_title         AS insider_position,
        f.filed_date            AS filing_date,
        t.transaction_date,
        CASE t.acquired_disposed
             WHEN 'A' THEN 'Buy' WHEN 'D' THEN 'Sell' ELSE NULL END AS buy_sell,
        t.shares,
        t.price_per_share       AS trade_price,
        f.accession_number      AS source_ref,
        f.is_director           AS is_director_flag,
        f.is_officer            AS is_officer_flag,
        f.is_ten_percent_owner  AS is_ten_percent_owner_flag
    FROM edgar_form4_transactions t
    JOIN edgar_form4_filings f USING (accession_number)
    WHERE t.is_open_market = 1

    UNION ALL

    SELECT
        'bpc' AS source,
        ticker,
        insider_name,
        insider_position,
        filing_date,
        NULL                    AS transaction_date,
        buy_sell,
        shares,
        trade_price,
        CAST(snapshot_date AS TEXT) AS source_ref,
        NULL                    AS is_director_flag,
        NULL                    AS is_officer_flag,
        NULL                    AS is_ten_percent_owner_flag
    FROM bpc_insider_supplement
    WHERE stock_or_option = 'Stock'
);

DROP VIEW IF EXISTS v_insider_signal_combined;
CREATE VIEW v_insider_signal_combined AS
SELECT
    'edgar' AS source,
    f.ticker,
    f.reporting_owner_name AS insider_name,
    f.officer_title         AS insider_position,
    f.filed_date            AS filing_date,
    t.transaction_date,
    CASE t.acquired_disposed
         WHEN 'A' THEN 'Buy'
         WHEN 'D' THEN 'Sell'
         ELSE NULL
    END                     AS buy_sell,
    'Stock'                 AS stock_or_option,
    t.shares,
    t.price_per_share       AS trade_price,
    f.accession_number      AS source_ref
FROM edgar_form4_transactions t
JOIN edgar_form4_filings f USING (accession_number)
WHERE t.is_open_market = 1

UNION ALL

SELECT
    'bpc' AS source,
    ticker,
    insider_name,
    insider_position,
    filing_date,
    NULL                    AS transaction_date,
    buy_sell,
    stock_or_option,
    shares,
    trade_price,
    CAST(snapshot_date AS TEXT) AS source_ref
FROM bpc_insider_supplement
WHERE stock_or_option = 'Stock';
