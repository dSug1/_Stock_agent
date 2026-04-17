from __future__ import annotations

import sqlite3
from typing import Optional

from database.db import now_iso


# NOTE: point-in-time enforcement.
# Every SELECT in this module filters on filing_date only.
# period_of_report is stored for reference; it MUST NOT appear in any
# WHERE or ORDER BY clause. Violating this breaks the survivorship-bias
# and look-ahead guarantees of HISTORICAL mode.


def save_holdings(
    institution_id: int,
    filing_date: str,
    period_of_report: str,
    holdings: list[dict],
    conn: sqlite3.Connection,
) -> int:
    ts = now_iso()
    n = 0
    for h in holdings:
        cusip = h.get("cusip")
        if not cusip:
            continue
        conn.execute(
            "INSERT INTO institution_holdings "
            "(institution_id, filing_date, period_of_report, ticker, "
            " cusip, shares, market_value, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(institution_id, filing_date, cusip) DO UPDATE SET "
            " ticker = excluded.ticker, "
            " shares = excluded.shares, "
            " market_value = excluded.market_value, "
            " updated_at = excluded.updated_at",
            (
                institution_id,
                filing_date,
                period_of_report,
                h.get("ticker"),
                cusip,
                h.get("shares"),
                h.get("market_value"),
                ts,
                ts,
            ),
        )
        n += 1
    conn.commit()
    return n


def get_holdings_as_of(
    institution_id: int,
    as_of_date: str,
    conn: sqlite3.Connection,
) -> list[dict]:
    """Return holdings from the most recent filing with
    filing_date <= as_of_date for this institution.
    """
    row = conn.execute(
        "SELECT filing_date FROM institution_holdings "
        "WHERE institution_id = ? AND filing_date <= ? "
        "ORDER BY filing_date DESC LIMIT 1",
        (institution_id, as_of_date),
    ).fetchone()
    if row is None:
        return []
    target_filing_date = row["filing_date"]
    rows = conn.execute(
        "SELECT institution_id, filing_date, period_of_report, "
        "       ticker, cusip, shares, market_value "
        "FROM institution_holdings "
        "WHERE institution_id = ? AND filing_date = ?",
        (institution_id, target_filing_date),
    ).fetchall()
    return [dict(r) for r in rows]


def get_prior_quarter_holdings(
    institution_id: int,
    current_filing_date: str,
    conn: sqlite3.Connection,
) -> list[dict]:
    """Return holdings from the filing immediately before
    current_filing_date for this institution.
    """
    row = conn.execute(
        "SELECT filing_date FROM institution_holdings "
        "WHERE institution_id = ? AND filing_date < ? "
        "ORDER BY filing_date DESC LIMIT 1",
        (institution_id, current_filing_date),
    ).fetchone()
    if row is None:
        return []
    prior_filing_date = row["filing_date"]
    rows = conn.execute(
        "SELECT institution_id, filing_date, period_of_report, "
        "       ticker, cusip, shares, market_value "
        "FROM institution_holdings "
        "WHERE institution_id = ? AND filing_date = ?",
        (institution_id, prior_filing_date),
    ).fetchall()
    return [dict(r) for r in rows]


def get_latest_filing_date(
    institution_id: int,
    as_of_date: str,
    conn: sqlite3.Connection,
) -> Optional[str]:
    row = conn.execute(
        "SELECT MAX(filing_date) AS d FROM institution_holdings "
        "WHERE institution_id = ? AND filing_date <= ?",
        (institution_id, as_of_date),
    ).fetchone()
    return row["d"] if row and row["d"] is not None else None


def get_tickers_held_as_of(
    as_of_date: str,
    conn: sqlite3.Connection,
) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT ticker FROM institution_holdings "
        "WHERE filing_date <= ? AND ticker IS NOT NULL",
        (as_of_date,),
    ).fetchall()
    return [r["ticker"] for r in rows]
