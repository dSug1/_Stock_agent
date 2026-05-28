"""Cross-DB read of 2_Funds_parser/2_fundparser.db via ATTACH.

Attaches the funds DB (read-only path coupling) to an existing
biotech.db connection, identifies the two most recent quarters, and
computes per-ticker net positive accumulation USD across all tracked
funds.

Spec §12.4.3 + §12.7; decisions D9.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)


class FundsDBError(Exception):
    """Raised when the funds DB cannot be attached or is empty."""


@dataclass(frozen=True)
class FundAccumulationRow:
    ticker: str
    funds_holding_latest: int
    funds_holding_previous: int
    fund_accumulation_usd: float


@dataclass(frozen=True)
class FundsContext:
    quarter_latest: str
    quarter_previous: str
    stale: bool
    rows_by_ticker: dict[str, FundAccumulationRow]


def repo_root_from_project_root(project_root: Path) -> Path:
    """3_Biopharmcatalyst_parser/ -> .. (the _Stock_agent/ repo root)."""
    return project_root.parent


def resolve_funds_db_path(project_root: Path, db_path_relative_to_repo_root: str) -> Path:
    return repo_root_from_project_root(project_root) / db_path_relative_to_repo_root


def attach_funds_db(conn: sqlite3.Connection, funds_db_path: Path) -> None:
    """ATTACH the funds DB under the alias ``funds``. Raises FundsDBError
    if the path does not exist or the ATTACH fails.
    """
    if not funds_db_path.exists():
        raise FundsDBError(
            f"funds DB not found at {funds_db_path}. "
            "Run `run_2_Funds_parser.bat` to populate it, or pass --skip-funds."
        )
    try:
        conn.execute("ATTACH DATABASE ? AS funds", (str(funds_db_path),))
    except sqlite3.Error as e:
        raise FundsDBError(f"failed to ATTACH funds DB at {funds_db_path}: {e}") from e


def detach_funds_db(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("DETACH DATABASE funds")
    except sqlite3.Error:
        # Already detached, or never attached; harmless.
        pass


def _two_latest_quarters(conn: sqlite3.Connection) -> tuple[str, str] | None:
    rows = conn.execute(
        "SELECT DISTINCT period_of_report FROM funds.holdings "
        "WHERE period_of_report IS NOT NULL "
        "ORDER BY period_of_report DESC LIMIT 2"
    ).fetchall()
    if len(rows) < 2:
        return None
    return rows[0][0], rows[1][0]


def load_fund_accumulation(
    conn: sqlite3.Connection,
    *,
    tickers: list[str],
    snapshot_date: date,
    stale_warning_days: int,
) -> FundsContext:
    """Compute per-ticker accumulation for ``tickers`` between the two
    latest quarters in the attached funds DB.

    Per-fund contribution = MAX(0, shares_latest - shares_previous) ×
    (mv_latest / shares_latest) when shares_latest > 0, else 0.

    Returns a FundsContext with rows only for tickers present in the
    funds DB. Callers should default to ``fund_accumulation_score = 0``
    for tickers absent from the result.
    """
    quarters = _two_latest_quarters(conn)
    if quarters is None:
        raise FundsDBError(
            "funds DB has fewer than 2 distinct period_of_report values; "
            "cannot compute accumulation."
        )
    q_latest, q_previous = quarters

    # Stale check
    try:
        q_latest_date = datetime.strptime(q_latest, "%Y-%m-%d").date()
        days_old = (snapshot_date - q_latest_date).days
        stale = days_old > stale_warning_days
        if stale:
            log.warning(
                "funds DB latest quarter %s is %d days before snapshot %s (>%d-day threshold)",
                q_latest, days_old, snapshot_date.isoformat(), stale_warning_days,
            )
    except ValueError:
        # Non-ISO date format; skip stale check
        stale = False

    if not tickers:
        return FundsContext(
            quarter_latest=q_latest, quarter_previous=q_previous,
            stale=stale, rows_by_ticker={},
        )

    # SQLite has a default parameter limit (999) but we typically have <300 tickers.
    placeholders = ",".join("?" * len(tickers))
    sql = f"""
    WITH ticker_funds AS (
      SELECT DISTINCT ticker, fund_id
      FROM funds.holdings
      WHERE period_of_report IN (?, ?)
        AND ticker IS NOT NULL
        AND ticker IN ({placeholders})
    )
    SELECT tf.ticker,
           COUNT(DISTINCT CASE WHEN l.shares > 0 THEN tf.fund_id END) AS funds_latest,
           COUNT(DISTINCT CASE WHEN p.shares > 0 THEN tf.fund_id END) AS funds_prev,
           COALESCE(SUM(
             MAX(0, COALESCE(l.shares, 0) - COALESCE(p.shares, 0)) *
             COALESCE(CASE WHEN l.shares > 0 THEN l.market_value * 1.0 / l.shares END, 0)
           ), 0) AS accum_usd
    FROM ticker_funds tf
    LEFT JOIN funds.holdings l
      ON l.ticker = tf.ticker AND l.fund_id = tf.fund_id AND l.period_of_report = ?
    LEFT JOIN funds.holdings p
      ON p.ticker = tf.ticker AND p.fund_id = tf.fund_id AND p.period_of_report = ?
    GROUP BY tf.ticker
    """
    params: list = [q_latest, q_previous] + list(tickers) + [q_latest, q_previous]

    rows_by_ticker: dict[str, FundAccumulationRow] = {}
    for r in conn.execute(sql, params).fetchall():
        rows_by_ticker[r[0]] = FundAccumulationRow(
            ticker=r[0],
            funds_holding_latest=int(r[1] or 0),
            funds_holding_previous=int(r[2] or 0),
            fund_accumulation_usd=float(r[3] or 0.0),
        )

    return FundsContext(
        quarter_latest=q_latest,
        quarter_previous=q_previous,
        stale=stale,
        rows_by_ticker=rows_by_ticker,
    )
