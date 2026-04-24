-- 2_Funds_parser — database schema
-- Layer 0: fund registry (source of truth: Input/list_of_funds.xlsx).
-- Layer 1: 13F-HR ingest (holdings, filings_log, cusip_ticker_map).

CREATE TABLE IF NOT EXISTS funds (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cik          TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    legal_name   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_funds_name ON funds(name);

-- One row per (fund, filing_date, cusip). Matches 1_not_used's
-- institution_holdings shape but keyed on funds.id.
CREATE TABLE IF NOT EXISTS holdings (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id           INTEGER NOT NULL,
    filing_date       TEXT NOT NULL,
    period_of_report  TEXT NOT NULL,
    name_of_issuer    TEXT,
    ticker            TEXT,
    ticker_source     TEXT,   -- 'openfigi' | 'sec_name' | 'manual' | NULL (unresolved)
    cusip             TEXT NOT NULL,
    shares            INTEGER,
    market_value      INTEGER,
    title_of_class    TEXT,   -- 13F <titleOfClass>: 'COM', 'PFD', 'WT', 'PRE-FUND WT', etc.
    put_call          TEXT,   -- 13F <putCall>: 'Put' | 'Call' | NULL (common case)
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (fund_id) REFERENCES funds(id),
    UNIQUE (fund_id, filing_date, cusip)
);

CREATE INDEX IF NOT EXISTS idx_holdings_fund ON holdings(fund_id);
CREATE INDEX IF NOT EXISTS idx_holdings_filing_date ON holdings(filing_date);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker ON holdings(ticker);
CREATE INDEX IF NOT EXISTS idx_holdings_cusip ON holdings(cusip);

-- One row per downloaded 13F-HR filing. Used for dedup: if the
-- accession_number is present, we don't re-download.
CREATE TABLE IF NOT EXISTS filings_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    fund_id           INTEGER NOT NULL,
    filing_date       TEXT NOT NULL,
    period_of_report  TEXT NOT NULL,
    accession_number  TEXT NOT NULL,
    document_url      TEXT,
    holdings_count    INTEGER NOT NULL DEFAULT 0,
    parse_status      TEXT NOT NULL DEFAULT 'success',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (fund_id) REFERENCES funds(id),
    UNIQUE (fund_id, accession_number)
);

CREATE INDEX IF NOT EXISTS idx_filings_log_fund ON filings_log(fund_id);
CREATE INDEX IF NOT EXISTS idx_filings_log_filing_date ON filings_log(filing_date);

-- CUSIP → ticker resolution cache (OpenFIGI). ticker may be NULL for
-- CUSIPs that resolved to non-equity instruments (ETF, preferred, etc.)
-- or that OpenFIGI couldn't match; NULL rows still count as "resolved"
-- and are not re-queried.
CREATE TABLE IF NOT EXISTS cusip_ticker_map (
    cusip          TEXT PRIMARY KEY,
    ticker         TEXT,
    exchange       TEXT,
    security_type  TEXT,
    ticker_source  TEXT,   -- 'openfigi' | 'manual' | NULL (NULL = legacy OpenFIGI insert)
    resolved_date  TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
