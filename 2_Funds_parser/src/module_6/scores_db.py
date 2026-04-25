"""Schema init + read helpers for ``llm_scores.db``.

Step B (cost estimator) only needs:
    - ``init_llm_scores_schema(conn)`` — idempotent CREATE TABLE IF NOT EXISTS
      so the cost estimator can probe an empty DB on the very first run.
    - ``query_priors_for_ticker(conn, ticker, prompt_version, model)`` —
      returns the latest llm_scores row per horizon for the tier classifier.

Write-side helpers (upserts on dispatch, light-refresh ``refreshed_from_row_id``
linking, etc.) ship in step C alongside scoring.py.

Schema reference: spec/module_6_spec.md § ``llm_scores.db`` — SQLite schema.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

LLM_SCORES_SCHEMA_VERSION = "m6-v2.0"

# ---------------------------------------------------------------------------
# CREATE TABLE statements. Kept as one string per table for readable diffs.
# All tables use IF NOT EXISTS so this is idempotent and survives schema bumps
# (additive migrations only — see _apply_additive_migrations below).
# ---------------------------------------------------------------------------

_CREATE_LLM_SCORES = """
CREATE TABLE IF NOT EXISTS llm_scores (
    ticker                              TEXT    NOT NULL,
    quarter                             TEXT    NOT NULL,
    horizon                             TEXT    NOT NULL,    -- '3mo' | '12mo'
    prompt_version                      TEXT    NOT NULL,
    model                               TEXT    NOT NULL,
    run_id                              INTEGER NOT NULL,

    -- per-horizon LLM outputs (Section C of m6-v2 schema)
    target_price_usd                    REAL,
    time_to_catalyst_weeks              INTEGER,
    probability                         REAL,
    catalyst_type                       TEXT,
    catalyst_detail                     TEXT,
    thesis_summary                      TEXT,
    key_risks_json                      TEXT,

    -- Python-computed deterministic scoring
    appreciation_from_fair_pct          REAL,
    appreciation_from_full_reward_pct   REAL,
    score_at_fair_pct_per_month         REAL,
    score_at_full_reward_pct_per_month  REAL,

    -- per-ticker fields (duplicated across horizon rows for query simplicity)
    fair_entry_low_usd                  REAL,
    fair_entry_high_usd                 REAL,
    fair_entry_rationale                TEXT,
    full_reward_low_usd                 REAL,
    full_reward_high_usd                REAL,
    full_reward_rationale               TEXT,
    fully_diluted_shares_count          REAL,
    prefunded_warrants_count            REAL,
    cash_and_equivalents_usd            REAL,
    runway_months                       REAL,
    rnpv_total_usd                      REAL,
    rnpv_per_share_usd                  REAL,
    moat_score                          REAL,
    technology_uniqueness_score         REAL,
    acquisition_target_score            REAL,
    mgmt_track_record_score             REAL,
    lead_indication                     TEXT,
    research_brief_json                 TEXT,

    -- raw LLM reply (D32) + Anthropic metadata
    raw_text                            TEXT,
    response_id                         TEXT,
    input_tokens                        INTEGER,
    output_tokens                       INTEGER,
    cache_read_tokens                   INTEGER,
    cache_creation_tokens               INTEGER,
    web_search_calls                    INTEGER,
    usd_cost                            REAL,

    -- tier routing (D39)
    source_tier                         TEXT,    -- 'A' | 'B' | 'C'
    pack_source_rank_hash               TEXT,
    refreshed_from_row_id               INTEGER,

    scored_at                           TEXT NOT NULL,

    PRIMARY KEY (ticker, quarter, horizon, prompt_version, model)
);
"""

_CREATE_LLM_RUNS = """
CREATE TABLE IF NOT EXISTS llm_runs (
    run_id                              INTEGER PRIMARY KEY AUTOINCREMENT,
    quarter                             TEXT,
    prompt_version                      TEXT,
    model                               TEXT,
    mode                                TEXT,    -- 'batch' | 'sync' | 'estimate'
    batch_id                            TEXT,
    gate_config_json                    TEXT,
    feed_size                           INTEGER,
    tier_a_count                        INTEGER,
    tier_b_count                        INTEGER,
    tier_c_count                        INTEGER,
    wall_time_s                         REAL,
    input_tokens_total                  INTEGER,
    output_tokens_total                 INTEGER,
    cache_read_tokens_total             INTEGER,
    cache_creation_tokens_total         INTEGER,
    web_search_calls_total              INTEGER,
    usd_cost_total                      REAL,
    usd_cost_list_price                 REAL,
    started_at                          TEXT,
    finished_at                         TEXT
);
"""

_CREATE_LLM_ERRORS = """
CREATE TABLE IF NOT EXISTS llm_errors (
    error_id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                              INTEGER,
    ticker                              TEXT,
    quarter                             TEXT,
    horizon                             TEXT,
    error_kind                          TEXT,
    error_detail                        TEXT,
    raw_text                            TEXT,
    occurred_at                         TEXT
);
"""

_CREATE_FINAL_RANKINGS = """
CREATE TABLE IF NOT EXISTS final_rankings (
    ticker                              TEXT,
    quarter                             TEXT,
    run_id                              INTEGER,
    final_rank                          INTEGER,
    final_horizon                       TEXT,
    final_score                         REAL,
    score_at_fair_3mo                   REAL,
    score_at_fair_12mo                  REAL,
    score_at_full_reward_3mo            REAL,
    score_at_full_reward_12mo           REAL,
    target_price_3mo_usd                REAL,
    target_price_12mo_usd               REAL,
    appreciation_from_fair_3mo_pct      REAL,
    appreciation_from_fair_12mo_pct     REAL,
    time_to_catalyst_3mo_weeks          INTEGER,
    time_to_catalyst_12mo_weeks         INTEGER,
    probability_3mo                     REAL,
    probability_12mo                    REAL,
    fair_entry_low_usd                  REAL,
    fair_entry_high_usd                 REAL,
    full_reward_low_usd                 REAL,
    full_reward_high_usd                REAL,
    fully_diluted_shares_count          REAL,
    rnpv_per_share_usd                  REAL,
    moat_score                          REAL,
    fda_pos_adjusted_lead               REAL,
    archetype                           TEXT,
    composite_best                      REAL,
    fund_count                          INTEGER,
    market_cap_usd                      REAL,
    industry                            TEXT,
    PRIMARY KEY (ticker, quarter, run_id)
);
"""

_CREATE_WEB_SEARCH_CACHE = """
CREATE TABLE IF NOT EXISTS web_search_cache (
    url                                 TEXT PRIMARY KEY,
    ticker                              TEXT,
    quarter                             TEXT,
    run_id                              INTEGER,
    search_query                        TEXT,
    title                               TEXT,
    content                             TEXT,
    content_length                      INTEGER,
    domain                              TEXT,
    published_date                      TEXT,
    first_seen_date                     TEXT,
    last_seen_date                      TEXT,
    seen_count                          INTEGER
);
"""

_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS idx_llm_scores_quarter_pv_model ON llm_scores(quarter, prompt_version, model);",
    "CREATE INDEX IF NOT EXISTS idx_llm_scores_run_id ON llm_scores(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_llm_scores_ticker_scored ON llm_scores(ticker, scored_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_llm_scores_ticker_quarter ON llm_scores(ticker, quarter);",
    "CREATE INDEX IF NOT EXISTS idx_llm_errors_run_id ON llm_errors(run_id);",
    "CREATE INDEX IF NOT EXISTS idx_final_rankings_quarter_score ON final_rankings(quarter, final_score DESC);",
    "CREATE INDEX IF NOT EXISTS idx_web_search_cache_ticker_seen ON web_search_cache(ticker, last_seen_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_web_search_cache_domain ON web_search_cache(domain);",
)


def init_llm_scores_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema init for llm_scores.db. Safe on existing databases."""
    cur = conn.cursor()
    cur.execute(_CREATE_LLM_SCORES)
    cur.execute(_CREATE_LLM_RUNS)
    cur.execute(_CREATE_LLM_ERRORS)
    cur.execute(_CREATE_FINAL_RANKINGS)
    cur.execute(_CREATE_WEB_SEARCH_CACHE)
    for stmt in _INDEX_STATEMENTS:
        cur.execute(stmt)
    conn.commit()


def query_priors_for_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    prompt_version: str,
    model: str,
) -> dict[str, Optional[sqlite3.Row]]:
    """Return the most recent llm_scores row per horizon for a ticker.

    Used by the tier classifier (D39). Looks across ALL quarters — a 12mo
    thesis from last quarter is still relevant for cross-quarter Tier B
    refresh decisions.

    Returns a dict ``{"3mo": Row|None, "12mo": Row|None}``.
    """
    conn.row_factory = sqlite3.Row
    out: dict[str, Optional[sqlite3.Row]] = {"3mo": None, "12mo": None}
    for horizon in ("3mo", "12mo"):
        row = conn.execute(
            """
            SELECT *
            FROM llm_scores
            WHERE ticker = ? AND horizon = ? AND prompt_version = ? AND model = ?
            ORDER BY scored_at DESC
            LIMIT 1
            """,
            (ticker, horizon, prompt_version, model),
        ).fetchone()
        out[horizon] = row
    return out
