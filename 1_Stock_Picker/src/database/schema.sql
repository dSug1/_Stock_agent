-- =====================================================================
-- Stock Picker — master SQLite schema (version 1)
--
-- Conventions:
--   * All dates stored as ISO8601 TEXT (e.g. 2026-04-17T12:00:00+00:00)
--   * Every table carries created_at and updated_at (ISO8601 TEXT)
--   * Foreign keys MUST be enabled per connection: PRAGMA foreign_keys = ON
--   * probability_sum_check enforced on open_catalysts
--   * deduplication_fingerprint UNIQUE per (ticker, fingerprint)
-- =====================================================================


-- ---------------------------------------------------------------------
-- Migration version marker. One row per applied migration.
-- Latest applied version = MAX(version).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);


-- ---------------------------------------------------------------------
-- Layer 3 — companies registry (one row per ticker).
-- Embeds financials_snapshot fields inline per spec/layer_3.md §3.1.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    ticker                              TEXT PRIMARY KEY,
    name                                TEXT,
    sector                              TEXT,
    sub_industry                        TEXT,
    processing_tier                     TEXT NOT NULL
        CHECK (processing_tier IN ('active','passive','watchlist')),
    twos                                REAL,
    qoq_change_signal                   TEXT,
    crowding_flag                       INTEGER NOT NULL DEFAULT 0
        CHECK (crowding_flag IN (0,1)),
    regime_at_entry                     TEXT,

    cash_and_equivalents                REAL,
    quarterly_burn_rate                 REAL,
    cash_runway_quarters                REAL,
    last_equity_raise_date              TEXT,
    last_equity_raise_dilution_pct      REAL,
    survival_probability_to_catalyst    REAL,
    financials_as_of_filing_date        TEXT,

    composite_catalyst_score            REAL,
    watchlist_status                    TEXT
        CHECK (watchlist_status IS NULL
               OR watchlist_status IN
                  ('not_eligible','watchlist',
                   'position_recommended','in_position')),

    created_at                          TEXT NOT NULL,
    updated_at                          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_companies_tier
    ON companies(processing_tier);


-- ---------------------------------------------------------------------
-- Layer 3 — open_catalysts (one row per catalyst per company).
-- probability_sum CHECK and dedup UNIQUE live here.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS open_catalysts (
    catalyst_id                 TEXT PRIMARY KEY,
    ticker                      TEXT NOT NULL,
    catalyst_data               TEXT,   -- Layer 2 extraction JSON
    current_bull_probability    REAL NOT NULL,
    current_base_probability    REAL NOT NULL,
    current_bear_probability    REAL NOT NULL,
    current_expected_value_pct  REAL,
    status                      TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','resolved_positive',
                          'resolved_negative','expired')),
    deduplication_fingerprint   TEXT NOT NULL,

    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,

    FOREIGN KEY (ticker) REFERENCES companies(ticker)
        ON UPDATE CASCADE ON DELETE RESTRICT,

    -- probability_sum_check: triple must sum to 1.0 within float tolerance
    CHECK (
        abs(current_bull_probability
          + current_base_probability
          + current_bear_probability
          - 1.0) < 0.001
    ),
    CHECK (current_bull_probability BETWEEN 0 AND 1),
    CHECK (current_base_probability BETWEEN 0 AND 1),
    CHECK (current_bear_probability BETWEEN 0 AND 1),

    UNIQUE (ticker, deduplication_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_open_catalysts_ticker
    ON open_catalysts(ticker);
CREATE INDEX IF NOT EXISTS idx_open_catalysts_status
    ON open_catalysts(status);


-- ---------------------------------------------------------------------
-- Layer 3 — probability_history (immutable Bayesian audit trail).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS probability_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    catalyst_id         TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    bull                REAL NOT NULL,
    base                REAL NOT NULL,
    bear                REAL NOT NULL,
    ev                  REAL,
    trigger_document    TEXT,
    change_rationale    TEXT,
    update_magnitude    REAL,
    corroborating_flag  INTEGER NOT NULL DEFAULT 0
        CHECK (corroborating_flag IN (0,1)),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,

    FOREIGN KEY (catalyst_id) REFERENCES open_catalysts(catalyst_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prob_history_catalyst
    ON probability_history(catalyst_id);
CREATE INDEX IF NOT EXISTS idx_prob_history_timestamp
    ON probability_history(timestamp);


-- ---------------------------------------------------------------------
-- Layer 3 — active_contradictions per catalyst.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS active_contradictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    catalyst_id         TEXT NOT NULL,
    contradiction_type  TEXT NOT NULL,
    source_document     TEXT,
    date                TEXT NOT NULL,
    resolution_status   TEXT NOT NULL DEFAULT 'open'
        CHECK (resolution_status IN ('open','resolved')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,

    FOREIGN KEY (catalyst_id) REFERENCES open_catalysts(catalyst_id)
        ON UPDATE CASCADE ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_contradictions_catalyst
    ON active_contradictions(catalyst_id);
CREATE INDEX IF NOT EXISTS idx_contradictions_status
    ON active_contradictions(resolution_status);


-- ---------------------------------------------------------------------
-- Layer -1 / Layer 3 — institutional_accumulation block per company.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS institutional_accumulation (
    ticker                      TEXT PRIMARY KEY,
    twos                        REAL,
    twos_qoq_change             REAL,
    qoq_change_signal           TEXT,
    up_down_volume_ratio_20d    REAL,
    up_down_volume_ratio_60d    REAL,
    accumulation_signal         TEXT
        CHECK (accumulation_signal IS NULL
               OR accumulation_signal IN
                  ('strong','moderate','neutral','distribution')),
    superinvestor_positions     TEXT,   -- JSON array
    insider_purchases_90d       TEXT,   -- JSON array
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,

    FOREIGN KEY (ticker) REFERENCES companies(ticker)
        ON UPDATE CASCADE ON DELETE CASCADE
);


-- ---------------------------------------------------------------------
-- Layer 5 — outcome_records (resolved positions).
-- Large blocks stored as JSON TEXT; retrievable for postmortem.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outcome_records (
    outcome_id              TEXT PRIMARY KEY,
    ticker                  TEXT NOT NULL,
    catalyst_id             TEXT,
    operating_mode          TEXT NOT NULL
        CHECK (operating_mode IN ('LIVE','SHADOW','HISTORICAL')),
    prediction_at_entry     TEXT,   -- JSON
    execution               TEXT,   -- JSON
    resolution              TEXT,   -- JSON
    exit                    TEXT,   -- JSON
    measurement_outputs     TEXT,   -- JSON
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,

    FOREIGN KEY (ticker) REFERENCES companies(ticker)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (catalyst_id) REFERENCES open_catalysts(catalyst_id)
        ON UPDATE CASCADE ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_outcome_ticker
    ON outcome_records(ticker);
CREATE INDEX IF NOT EXISTS idx_outcome_mode
    ON outcome_records(operating_mode);
CREATE INDEX IF NOT EXISTS idx_outcome_catalyst
    ON outcome_records(catalyst_id);


-- ---------------------------------------------------------------------
-- Module 12 — action_records.
-- Recurring and one-shot actions tracked via status + due_date.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS action_records (
    action_id           TEXT PRIMARY KEY,
    action_type         TEXT NOT NULL,
    category            TEXT NOT NULL
        CHECK (category IN
               ('position_level','system_recurring','alert_triggered')),
    priority            TEXT NOT NULL
        CHECK (priority IN ('urgent','high','medium','low')),
    status              TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','completed','overdue','cancelled')),
    ticker              TEXT,
    due_date            TEXT,
    scheduling          TEXT,   -- JSON
    context             TEXT,   -- JSON
    content             TEXT,   -- JSON
    recurrence          TEXT,   -- JSON
    completion          TEXT,   -- JSON
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_status_due
    ON action_records(status, due_date);
CREATE INDEX IF NOT EXISTS idx_action_ticker
    ON action_records(ticker);
CREATE INDEX IF NOT EXISTS idx_action_type
    ON action_records(action_type);


-- ---------------------------------------------------------------------
-- Layer 6 / system — parameters key-value store.
-- Used for calibration values and runtime configuration.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parameters (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,      -- JSON-encoded
    description     TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    updated_by      TEXT,               -- 'Layer_6' | 'Manual' | ...
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);


-- ---------------------------------------------------------------------
-- Layer 1 — document_queue (documents that passed keyword filter).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_queue (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    document_url        TEXT NOT NULL UNIQUE,
    document_type       TEXT,
    ticker              TEXT,
    source              TEXT,
    filing_date         TEXT,
    raw_text            TEXT,
    keyword_tier_hit    TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','extracted','failed')),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_docqueue_status
    ON document_queue(status);
CREATE INDEX IF NOT EXISTS idx_docqueue_ticker
    ON document_queue(ticker);
CREATE INDEX IF NOT EXISTS idx_docqueue_filing_date
    ON document_queue(filing_date);


-- ---------------------------------------------------------------------
-- Module 10 — alert_queue (alerts waiting for dispatch).
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_type      TEXT NOT NULL,
    ticker          TEXT,
    priority        TEXT NOT NULL
        CHECK (priority IN ('urgent','high','medium','low')),
    channel         TEXT NOT NULL,
    payload         TEXT,               -- JSON
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','sent','failed')),
    sent_at         TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_alert_status
    ON alert_queue(status);
CREATE INDEX IF NOT EXISTS idx_alert_priority
    ON alert_queue(priority);


-- ---------------------------------------------------------------------
-- Layer 1 — filtered_out_log (monthly keyword audit).
-- ticker / date / source_url only — no raw text by spec.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS filtered_out_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT,
    date        TEXT NOT NULL,
    source_url  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_filtered_date
    ON filtered_out_log(date);
CREATE INDEX IF NOT EXISTS idx_filtered_ticker
    ON filtered_out_log(ticker);


-- ---------------------------------------------------------------------
-- Layer -1 — tracked institution registry.
-- Rows seeded idempotently by layer_minus1.institution_registry.
-- Hardcoded spec values; multiplier must not be mutated at runtime.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS institutions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,
    tier                TEXT NOT NULL
        CHECK (tier IN ('1A','1B','2A','2B','3','4')),
    tier_label          TEXT NOT NULL,
    multiplier          REAL NOT NULL,
    primary_coverage    TEXT,
    cik                 TEXT,
    edgar_name          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_institutions_tier
    ON institutions(tier);
CREATE INDEX IF NOT EXISTS idx_institutions_cik
    ON institutions(cik);


-- ---------------------------------------------------------------------
-- Layer -1 — 13F holdings per institution per filing.
-- filing_date is the ONLY date used for point-in-time queries.
-- period_of_report is stored for reference but never filtered on.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS institution_holdings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    institution_id      INTEGER NOT NULL,
    filing_date         TEXT NOT NULL,
    period_of_report    TEXT NOT NULL,
    ticker              TEXT,
    cusip               TEXT NOT NULL,
    shares              INTEGER,
    market_value        INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (institution_id) REFERENCES institutions(id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    UNIQUE (institution_id, filing_date, cusip)
);

CREATE INDEX IF NOT EXISTS idx_holdings_filing_date
    ON institution_holdings(filing_date);
CREATE INDEX IF NOT EXISTS idx_holdings_institution_filing
    ON institution_holdings(institution_id, filing_date);
CREATE INDEX IF NOT EXISTS idx_holdings_ticker
    ON institution_holdings(ticker);
CREATE INDEX IF NOT EXISTS idx_holdings_cusip
    ON institution_holdings(cusip);


-- ---------------------------------------------------------------------
-- Layer -1 — per-ticker TWOS score per run_date.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS twos_scores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    run_date            TEXT NOT NULL,
    twos_score          REAL NOT NULL,
    processing_tier     TEXT NOT NULL
        CHECK (processing_tier IN
               ('active','passive','watchlist','not_tracked')),
    crowding_flag       INTEGER NOT NULL DEFAULT 0
        CHECK (crowding_flag IN (0,1)),
    institution_count   INTEGER NOT NULL,
    qoq_change_signal   TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    UNIQUE (ticker, run_date)
);

CREATE INDEX IF NOT EXISTS idx_twos_run_date
    ON twos_scores(run_date);
CREATE INDEX IF NOT EXISTS idx_twos_ticker
    ON twos_scores(ticker);


-- ---------------------------------------------------------------------
-- Layer -1 — CUSIP → ticker cache (OpenFIGI resolutions).
-- NULL ticker = OpenFIGI returned no match; cached to avoid retries.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cusip_ticker_map (
    cusip           TEXT PRIMARY KEY,
    ticker          TEXT,
    exchange        TEXT,
    resolved_date   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cusip_ticker
    ON cusip_ticker_map(ticker);
