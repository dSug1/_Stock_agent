"""Module 6.5 — SQLite store for FDSC enrichment.

Single file `data/fundamentals.db` holding three tables across all
tickers and all time:

  financials       — SEC XBRL per-period rows + live price + FDSC market cap
  capital_raises   — 8-K / S-3 / 424B5 filings parsed for proceeds + PFW issuance
  fetch_log        — per-(ticker, source) TTL + conditional-GET cache

Per spec §5.12 storage rule #1: machine-generated data lives in SQLite,
never as JSON files on disk. Raw EDGAR responses sit in
`financials.companyfacts_raw_json` (TEXT, queryable via `json_extract`).

Mirrors `2_Funds_parser/src/module_4c/fundamentals_db.py` schema so the
M5 fundamentals reader from 2_Funds_parser drops in unchanged. Two
3_Biopharm-specific additions:

  - `financials.last_price_usd / last_price_as_of`  (from yfinance)
  - `financials.market_cap_fdsc_usd`                 (basic + PFW × price)
  - `financials.pfw_source`                          ('capital_raises_sum_2yr' | 'xbrl_explicit' | NULL)
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
    pfw_source                        TEXT,
    pfw_share_dilution_warning        INTEGER,    -- 0/1 boolean (NULL = unknown)

    last_price_usd                    REAL,
    last_price_as_of                  TEXT,
    market_cap_fdsc_usd               REAL,

    companyfacts_raw_json             TEXT,

    fetched_at                        TEXT NOT NULL,
    fetch_status                      TEXT NOT NULL,
    fetch_error                       TEXT,
    PRIMARY KEY (ticker, period)
);

CREATE INDEX IF NOT EXISTS idx_fund_fin_ticker_date  ON financials(ticker, period_end_date DESC);
CREATE INDEX IF NOT EXISTS idx_fund_fin_cik          ON financials(cik);

CREATE TABLE IF NOT EXISTS capital_raises (
    ticker                  TEXT NOT NULL,
    cik                     TEXT NOT NULL,
    accession_number        TEXT NOT NULL,
    filing_date             TEXT NOT NULL,
    event_date              TEXT,
    form                    TEXT NOT NULL,
    raise_type              TEXT,                 -- 'pfw' | 'equity' | 'shelf' | 'debt' | 'unknown'
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

CREATE INDEX IF NOT EXISTS idx_fund_raises_ticker_date ON capital_raises(ticker, filing_date DESC);
CREATE INDEX IF NOT EXISTS idx_fund_raises_type        ON capital_raises(raise_type, filing_date DESC);

CREATE TABLE IF NOT EXISTS fetch_log (
    ticker                  TEXT NOT NULL,
    source                  TEXT NOT NULL,
    last_fetched_at         TEXT NOT NULL,
    last_status             TEXT NOT NULL,
    last_error              TEXT,
    rows_written            INTEGER NOT NULL DEFAULT 0,
    etag                    TEXT,
    last_modified           TEXT,
    PRIMARY KEY (ticker, source)
);
"""


# Additive migrations — append (table, col, decl) tuples. Each entry is
# idempotent: PRAGMA table_info first, only ALTER when absent.
_ADDITIVE_MIGRATIONS: list[tuple[str, str, str]] = [
    # Reserved for post-launch schema growth.
]


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    for table, col, decl in _ADDITIVE_MIGRATIONS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.OperationalError:
            continue
        if col not in cols:
            log.info("fundamentals.db: ADD COLUMN %s.%s %s", table, col, decl)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "fundamentals.db"


def init_fundamentals_db(db_path: Path | str | None = None) -> Path:
    """Create tables + indexes + WAL. Idempotent. Returns the resolved path."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.execute("PRAGMA journal_mode=WAL")
        _apply_additive_migrations(conn)
        conn.commit()
    return path


@contextmanager
def db_connect(db_path: Path | str | None = None):
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    init_fundamentals_db(path)
    cx = sqlite3.connect(path, timeout=30)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


# ─── fetch_log helpers ───────────────────────────────────────────────────────


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
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO fetch_log(ticker, source, last_fetched_at, last_status,
                              last_error, rows_written, etag, last_modified)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker, source) DO UPDATE SET
            last_fetched_at=excluded.last_fetched_at,
            last_status=excluded.last_status,
            last_error=excluded.last_error,
            rows_written=excluded.rows_written,
            etag=COALESCE(excluded.etag, fetch_log.etag),
            last_modified=COALESCE(excluded.last_modified, fetch_log.last_modified)
        """,
        (ticker, source, last_fetched_at, last_status, last_error, rows_written,
         etag, last_modified),
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
    "fully_diluted_shares_count",
    "pfw_source", "pfw_share_dilution_warning",
    "last_price_usd", "last_price_as_of", "market_cap_fdsc_usd",
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


# ─── read helpers (used by M7 context_pack) ─────────────────────────────────


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
    conn: sqlite3.Connection,
    tickers: list[str],
    since_date_iso: str,
    *,
    raise_type: Optional[str] = None,
) -> dict[str, list[dict]]:
    """Return {ticker: [raises_row_dict, ...]} filtered by date + optional type."""
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    sql = (
        f"SELECT * FROM capital_raises "
        f"WHERE ticker IN ({placeholders}) AND filing_date >= ?"
    )
    params: list = list(tickers) + [since_date_iso]
    if raise_type is not None:
        sql += " AND raise_type = ?"
        params.append(raise_type)
    sql += " ORDER BY filing_date ASC"
    rows = conn.execute(sql, params).fetchall()
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
