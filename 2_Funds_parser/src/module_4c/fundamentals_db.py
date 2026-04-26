"""Module 4c — SQLite primary store for biotech financials.

Single file (`data/fundamentals.db`) holding four tables across all quarters
and all tickers. WAL mode. Schema evolves via `_apply_additive_migrations`
(same pattern as M4 prices.py and M6 scores_db.py).

Per D32: raw EDGAR responses are stored in TEXT columns inside this DB
(queryable via `json_extract`). No `*.json` files on disk for M4c output.

Per D54: financials only — no clinical_trials table.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS financials (
    ticker                            TEXT NOT NULL,
    cik                               TEXT NOT NULL,
    period                            TEXT NOT NULL,
    period_end_date                   TEXT NOT NULL,
    form                              TEXT,

    cash_and_equivalents_usd          INTEGER,
    short_term_investments_usd        INTEGER,
    cash_total_usd                    INTEGER,
    total_assets_usd                  INTEGER,
    total_liabilities_usd             INTEGER,
    accounts_receivable_usd           INTEGER,
    ppe_net_usd                       INTEGER,

    rd_expense_ttm_usd                INTEGER,
    ga_expense_ttm_usd                INTEGER,
    quarterly_burn_usd                INTEGER,
    runway_months                     REAL,
    operating_cf_ttm_usd              INTEGER,

    basic_shares_count                INTEGER,
    diluted_shares_count              INTEGER,
    prefunded_warrants_count          INTEGER,
    fully_diluted_shares_count        INTEGER,
    shelf_registration_usd_capacity   INTEGER,

    companyfacts_raw_json             TEXT,

    fetched_at                        TEXT NOT NULL,
    fetch_status                      TEXT NOT NULL,
    fetch_error                       TEXT,
    PRIMARY KEY (ticker, period)
);

CREATE INDEX IF NOT EXISTS idx_fin_ticker_date  ON financials(ticker, period_end_date DESC);
CREATE INDEX IF NOT EXISTS idx_fin_cik          ON financials(cik);

CREATE TABLE IF NOT EXISTS capital_raises (
    ticker                  TEXT NOT NULL,
    cik                     TEXT NOT NULL,
    accession_number        TEXT NOT NULL,
    filing_date             TEXT NOT NULL,
    event_date              TEXT,
    form                    TEXT NOT NULL,
    raise_type              TEXT,
    gross_proceeds_usd      INTEGER,
    net_proceeds_usd        INTEGER,
    shares_issued           INTEGER,
    price_per_share_usd     REAL,
    discount_to_market_pct  REAL,
    description             TEXT,
    raw_filing_url          TEXT,
    fetched_at              TEXT NOT NULL,
    fetch_status            TEXT NOT NULL,
    PRIMARY KEY (cik, accession_number)
);

CREATE INDEX IF NOT EXISTS idx_raises_ticker_date ON capital_raises(ticker, filing_date DESC);

CREATE TABLE IF NOT EXISTS insider_transactions (
    ticker                  TEXT NOT NULL,
    cik                     TEXT NOT NULL,
    accession_number        TEXT NOT NULL,
    filing_date             TEXT NOT NULL,
    transaction_date        TEXT,
    insider_name            TEXT NOT NULL,
    insider_cik             TEXT,
    role                    TEXT,
    txn_type                TEXT NOT NULL,
    shares                  INTEGER,
    price_usd               REAL,
    total_value_usd         INTEGER,
    raw_form4_url           TEXT,
    fetched_at              TEXT NOT NULL,
    fetch_status            TEXT NOT NULL,
    PRIMARY KEY (cik, accession_number, insider_name, transaction_date, txn_type, shares)
);

CREATE INDEX IF NOT EXISTS idx_insider_ticker_date  ON insider_transactions(ticker, transaction_date DESC);
CREATE INDEX IF NOT EXISTS idx_insider_role         ON insider_transactions(role, txn_type);

CREATE TABLE IF NOT EXISTS fetch_log (
    ticker                  TEXT NOT NULL,
    source                  TEXT NOT NULL,
    last_fetched_at         TEXT NOT NULL,
    last_status             TEXT NOT NULL,
    last_error              TEXT,
    rows_written            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ticker, source)
);
"""


# Additive migrations — append (table, col, decl) tuples here. Each entry is
# idempotent: probe via PRAGMA table_info first, only ALTER when absent.
_ADDITIVE_MIGRATIONS: list[tuple[str, str, str]] = []


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    for table, col, decl in _ADDITIVE_MIGRATIONS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.OperationalError:
            continue
        if col not in cols:
            log.info("fundamentals.db: ADD COLUMN %s.%s %s", table, col, decl)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_fundamentals_db(db_path: Path) -> None:
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


# ─── fetch_log helpers (drives TTL gating) ───────────────────────────────────

def get_fetch_log(
    conn: sqlite3.Connection, tickers: Iterable[str]
) -> dict[tuple[str, str], dict]:
    """Bulk-read fetch_log rows. Returns {(ticker, source): row_dict}."""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT * FROM fetch_log WHERE ticker IN ({placeholders})",
        tickers,
    ).fetchall()
    return {(r["ticker"], r["source"]): dict(r) for r in rows}


def upsert_fetch_log(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    source: str,
    last_fetched_at: str,
    last_status: str,
    last_error: Optional[str] = None,
    rows_written: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log(ticker, source, last_fetched_at, last_status,
                              last_error, rows_written)
        VALUES(?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, source) DO UPDATE SET
            last_fetched_at=excluded.last_fetched_at,
            last_status=excluded.last_status,
            last_error=excluded.last_error,
            rows_written=excluded.rows_written
        """,
        (ticker, source, last_fetched_at, last_status, last_error, rows_written),
    )


# ─── financials upsert ───────────────────────────────────────────────────────

_FINANCIALS_COLS = (
    "ticker", "cik", "period", "period_end_date", "form",
    "cash_and_equivalents_usd", "short_term_investments_usd", "cash_total_usd",
    "total_assets_usd", "total_liabilities_usd", "accounts_receivable_usd",
    "ppe_net_usd",
    "rd_expense_ttm_usd", "ga_expense_ttm_usd", "quarterly_burn_usd",
    "runway_months", "operating_cf_ttm_usd",
    "basic_shares_count", "diluted_shares_count", "prefunded_warrants_count",
    "fully_diluted_shares_count", "shelf_registration_usd_capacity",
    "companyfacts_raw_json",
    "fetched_at", "fetch_status", "fetch_error",
)


def upsert_financials_row(conn: sqlite3.Connection, row: dict) -> None:
    payload = {k: row.get(k) for k in _FINANCIALS_COLS}
    placeholders = ",".join(f":{c}" for c in _FINANCIALS_COLS)
    setters = ",\n            ".join(
        f"{c}=excluded.{c}" for c in _FINANCIALS_COLS
        if c not in ("ticker", "period")
    )
    conn.execute(
        f"""
        INSERT INTO financials({",".join(_FINANCIALS_COLS)})
        VALUES({placeholders})
        ON CONFLICT(ticker, period) DO UPDATE SET
            {setters}
        """,
        payload,
    )


# ─── capital_raises upsert ───────────────────────────────────────────────────

_RAISES_COLS = (
    "ticker", "cik", "accession_number", "filing_date", "event_date",
    "form", "raise_type", "gross_proceeds_usd", "net_proceeds_usd",
    "shares_issued", "price_per_share_usd", "discount_to_market_pct",
    "description", "raw_filing_url",
    "fetched_at", "fetch_status",
)


def upsert_capital_raise(conn: sqlite3.Connection, row: dict) -> None:
    payload = {k: row.get(k) for k in _RAISES_COLS}
    placeholders = ",".join(f":{c}" for c in _RAISES_COLS)
    setters = ",\n            ".join(
        f"{c}=excluded.{c}" for c in _RAISES_COLS
        if c not in ("cik", "accession_number")
    )
    conn.execute(
        f"""
        INSERT INTO capital_raises({",".join(_RAISES_COLS)})
        VALUES({placeholders})
        ON CONFLICT(cik, accession_number) DO UPDATE SET
            {setters}
        """,
        payload,
    )


# ─── insider_transactions upsert ─────────────────────────────────────────────

_INSIDER_COLS = (
    "ticker", "cik", "accession_number", "filing_date", "transaction_date",
    "insider_name", "insider_cik", "role", "txn_type", "shares",
    "price_usd", "total_value_usd", "raw_form4_url",
    "fetched_at", "fetch_status",
)


def upsert_insider_transaction(conn: sqlite3.Connection, row: dict) -> None:
    payload = {k: row.get(k) for k in _INSIDER_COLS}
    placeholders = ",".join(f":{c}" for c in _INSIDER_COLS)
    # Composite PK; insider txns are append-only (accession_number is the
    # natural unique key per filing). INSERT OR IGNORE avoids re-write churn.
    conn.execute(
        f"""
        INSERT OR IGNORE INTO insider_transactions({",".join(_INSIDER_COLS)})
        VALUES({placeholders})
        """,
        payload,
    )


# ─── Read helpers used by M5 + status reports ────────────────────────────────

def latest_financials_for_tickers(
    conn: sqlite3.Connection, tickers: list[str]
) -> dict[str, dict]:
    """Return {ticker: latest_financials_row_dict} keyed by max(period_end_date)."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT f.* FROM financials f
        JOIN (
            SELECT ticker, MAX(period_end_date) AS pmax
            FROM financials
            WHERE ticker IN ({placeholders})
            GROUP BY ticker
        ) m ON m.ticker = f.ticker AND m.pmax = f.period_end_date
        """,
        tickers,
    ).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def capital_raises_for_tickers_since(
    conn: sqlite3.Connection, tickers: list[str], since_date_iso: str
) -> dict[str, list[dict]]:
    """Return {ticker: [raises_row_dict, ...]} for filing_date >= since_date_iso,
    ordered oldest→newest."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT * FROM capital_raises
        WHERE ticker IN ({placeholders}) AND filing_date >= ?
        ORDER BY filing_date ASC
        """,
        list(tickers) + [since_date_iso],
    ).fetchall()
    out: dict[str, list[dict]] = {t: [] for t in tickers}
    for r in rows:
        out[r["ticker"]].append(dict(r))
    return out


def insider_txns_for_tickers_since(
    conn: sqlite3.Connection, tickers: list[str], since_date_iso: str
) -> dict[str, list[dict]]:
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT * FROM insider_transactions
        WHERE ticker IN ({placeholders}) AND COALESCE(transaction_date, filing_date) >= ?
        ORDER BY COALESCE(transaction_date, filing_date) ASC
        """,
        list(tickers) + [since_date_iso],
    ).fetchall()
    out: dict[str, list[dict]] = {t: [] for t in tickers}
    for r in rows:
        out[r["ticker"]].append(dict(r))
    return out


def fetch_log_for_tickers(
    conn: sqlite3.Connection, tickers: list[str]
) -> dict[str, dict[str, dict]]:
    """Return {ticker: {source: row_dict}}."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"SELECT * FROM fetch_log WHERE ticker IN ({placeholders})",
        tickers,
    ).fetchall()
    out: dict[str, dict[str, dict]] = {t: {} for t in tickers}
    for r in rows:
        out[r["ticker"]][r["source"]] = dict(r)
    return out
