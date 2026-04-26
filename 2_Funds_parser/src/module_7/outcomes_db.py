"""Module 7 — SQLite primary store for outcome tracking.

Three tables, all in `data/outcomes.db`:
  - predictions    — one row per (ticker, scoring_date, horizon)
  - forward_prices — one row per (ticker, scoring_date, weeks_offset)
  - outcomes       — one row per (ticker, scoring_date, horizon)
                     (M7-β fills this; M7-α only writes predictions + forward_prices)

Schema evolves additively via `_apply_additive_migrations` (same pattern as
M4 prices.py and M6 scores_db.py).
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    ticker                                  TEXT NOT NULL,
    scoring_date                            TEXT NOT NULL,        -- ISO date from llm_runs.started_at
    quarter                                 TEXT NOT NULL,
    horizon                                 TEXT NOT NULL,        -- '3mo' | '12mo'
    run_id                                  INTEGER NOT NULL,
    prompt_version                          TEXT NOT NULL,
    model                                   TEXT NOT NULL,

    current_price_usd                       REAL NOT NULL,
    target_price_usd                        REAL,
    time_to_catalyst_weeks                  INTEGER,
    probability                             REAL,
    catalyst_type                           TEXT,
    catalyst_detail                         TEXT,

    score_at_current_pct_per_month          REAL,
    score_modifier                          REAL,
    score_at_current_adjusted_pct_per_month REAL,
    score_modifier_json                     TEXT,

    archetype                               TEXT,
    sector                                  TEXT,
    industry                                TEXT,
    fund_count                              INTEGER,
    market_cap_usd                          INTEGER,

    snapshot_at                             TEXT NOT NULL,
    PRIMARY KEY (ticker, scoring_date, horizon)
);

CREATE INDEX IF NOT EXISTS idx_predictions_quarter_archetype
    ON predictions(quarter, archetype);
CREATE INDEX IF NOT EXISTS idx_predictions_horizon_date
    ON predictions(horizon, scoring_date);
CREATE INDEX IF NOT EXISTS idx_predictions_run
    ON predictions(run_id);

CREATE TABLE IF NOT EXISTS forward_prices (
    ticker                  TEXT NOT NULL,
    scoring_date            TEXT NOT NULL,
    weeks_offset            INTEGER NOT NULL,
    asof_date               TEXT,
    close_usd               REAL,
    high_usd_to_date        REAL,
    return_pct              REAL,
    return_per_month        REAL,
    delisted                INTEGER NOT NULL DEFAULT 0,
    last_filled_at          TEXT NOT NULL,
    PRIMARY KEY (ticker, scoring_date, weeks_offset)
);

CREATE INDEX IF NOT EXISTS idx_fp_offset_date ON forward_prices(weeks_offset, asof_date);

CREATE TABLE IF NOT EXISTS outcomes (
    ticker                            TEXT NOT NULL,
    scoring_date                      TEXT NOT NULL,
    horizon                           TEXT NOT NULL,
    horizon_asof_date                 TEXT NOT NULL,

    actual_close_usd                  REAL,
    actual_return_pct                 REAL,
    actual_return_per_month           REAL,
    actual_max_close_usd_in_window    REAL,
    target_appreciation_pct           REAL,
    realised_vs_target_ratio          REAL,

    outcome_class                     TEXT,
    classified_at                     TEXT NOT NULL,
    PRIMARY KEY (ticker, scoring_date, horizon)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_class   ON outcomes(outcome_class);
CREATE INDEX IF NOT EXISTS idx_outcomes_horizon ON outcomes(horizon);
"""


_ADDITIVE_MIGRATIONS: list[tuple[str, str, str]] = []


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    for table, col, decl in _ADDITIVE_MIGRATIONS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.OperationalError:
            continue
        if col not in cols:
            log.info("outcomes.db: ADD COLUMN %s.%s %s", table, col, decl)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_outcomes_db(db_path: Path) -> None:
    """Create tables + indexes + WAL. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.execute("PRAGMA journal_mode=WAL")
        _apply_additive_migrations(conn)
        conn.commit()


@contextmanager
def db_connect(db_path: Path):
    cx = sqlite3.connect(db_path, timeout=30)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


def snapshot_count_for_run(conn: sqlite3.Connection, run_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM predictions WHERE run_id = ?", (run_id,),
    ).fetchone()
    return int(row[0]) if row else 0


def delete_snapshots_for_run(conn: sqlite3.Connection, run_id: int) -> int:
    """Best-effort wipe of snapshots for a run (used by re-snapshot path)."""
    cur = conn.execute("DELETE FROM predictions WHERE run_id = ?", (run_id,))
    return cur.rowcount or 0
