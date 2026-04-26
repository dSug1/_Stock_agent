"""Module 7 — forward-price collection.

For every predictions row missing forward_prices cells whose target window
has already elapsed, fill those cells from `data/prices.db`.

Idempotent — only writes missing cells. Walks targets per (ticker,
scoring_date, weeks_offset).

A target_asof = scoring_date + weeks_offset × 7d. If today < target_asof,
skip (not yet). If a price exists in `prices.db` at target_asof or within
the configured forward-search window, write the row. If the ticker is
otherwise active, leave the row unwritten (next run will retry). If the
ticker has no price newer than `delist_grace_days`, mark `delisted=1`.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .outcomes_db import db_connect as _outcomes_db_connect, init_outcomes_db

log = logging.getLogger(__name__)


DEFAULT_WINDOWS_WEEKS = (1, 4, 12, 26, 52)
DEFAULT_LOOKUP_MAX_FORWARD_DAYS = 5
DEFAULT_DELIST_GRACE_DAYS = 90


@dataclass
class CollectResult:
    asof_date: str
    n_predictions: int = 0
    cells_eligible: int = 0
    cells_filled: int = 0
    cells_already_present: int = 0
    cells_not_yet: int = 0
    cells_missing_in_prices: int = 0
    cells_delisted: int = 0
    failed: list[tuple[str, str, int, str]] = field(default_factory=list)


def _now_iso() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _date_iso(d: dt.date) -> str:
    return d.isoformat()


def _parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s).split("T")[0].split(" ")[0])


def _closest_forward_close(
    prices_conn: sqlite3.Connection,
    ticker: str,
    target_asof: dt.date,
    *,
    max_forward_days: int,
) -> Optional[tuple[str, float, float]]:
    """Find the closest trading-day close at or after target_asof, up to
    max_forward_days ahead. Also returns max(close) over [target_asof, found_date].
    Returns (asof_date_iso, close_usd, max_close_in_lookup_window) or None.
    """
    end = target_asof + dt.timedelta(days=max_forward_days)
    rows = prices_conn.execute(
        """
        SELECT date, adjusted_close FROM prices
        WHERE ticker = ? AND date >= ? AND date <= ?
        ORDER BY date ASC
        """,
        (ticker, _date_iso(target_asof), _date_iso(end)),
    ).fetchall()
    if not rows:
        return None
    asof_date, close_usd = rows[0]
    max_close = max(float(r[1]) for r in rows if r[1] is not None)
    return asof_date, float(close_usd), max_close


def _max_close_in_window(
    prices_conn: sqlite3.Connection,
    ticker: str,
    start_date: dt.date,
    end_date: dt.date,
) -> Optional[float]:
    """max(adjusted_close) over [start_date, end_date], inclusive. None if no rows."""
    row = prices_conn.execute(
        """
        SELECT MAX(adjusted_close) FROM prices
        WHERE ticker = ? AND date >= ? AND date <= ?
        """,
        (ticker, _date_iso(start_date), _date_iso(end_date)),
    ).fetchone()
    if not row:
        return None
    val = row[0]
    return float(val) if val is not None else None


def _last_price_date(
    prices_conn: sqlite3.Connection, ticker: str
) -> Optional[dt.date]:
    row = prices_conn.execute(
        "SELECT MAX(date) FROM prices WHERE ticker = ?", (ticker,),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return _parse_date(row[0])
    except ValueError:
        return None


def collect_forward_prices(
    outcomes_db_path: Path,
    prices_db_path: Path,
    *,
    asof_date: Optional[dt.date] = None,
    windows_weeks: tuple[int, ...] = DEFAULT_WINDOWS_WEEKS,
    max_forward_days: int = DEFAULT_LOOKUP_MAX_FORWARD_DAYS,
    delist_grace_days: int = DEFAULT_DELIST_GRACE_DAYS,
    quarter_filter: Optional[str] = None,
    dry_run: bool = False,
) -> CollectResult:
    """Walk every predictions row whose forward_prices cells are missing AND
    whose target asof has elapsed. Fill from `data/prices.db`. Idempotent.
    """
    init_outcomes_db(outcomes_db_path)

    today = asof_date or dt.date.today()
    result = CollectResult(asof_date=_date_iso(today))

    if not prices_db_path.exists():
        log.warning("collect_forward_prices: %s missing — nothing to do",
                    prices_db_path)
        return result

    with _outcomes_db_connect(outcomes_db_path) as oconn, \
            sqlite3.connect(prices_db_path) as pconn:
        # Distinct (ticker, scoring_date) pairs from predictions.
        sql = (
            "SELECT DISTINCT ticker, scoring_date FROM predictions"
            + (" WHERE quarter = ?" if quarter_filter else "")
        )
        params = (quarter_filter,) if quarter_filter else ()
        pred_rows = oconn.execute(sql, params).fetchall()
        result.n_predictions = len(pred_rows)
        if not pred_rows:
            return result

        # Existing forward_prices cells (so we don't re-fill).
        existing: set[tuple[str, str, int]] = set()
        sql_exist = (
            "SELECT ticker, scoring_date, weeks_offset "
            "FROM forward_prices"
        )
        for r in oconn.execute(sql_exist).fetchall():
            existing.add((r[0], r[1], int(r[2])))

        now_iso = _now_iso()
        new_rows: list[dict] = []

        for ticker, scoring_date_str in pred_rows:
            try:
                scoring_date = _parse_date(scoring_date_str)
            except ValueError:
                log.debug("bad scoring_date %r for %s", scoring_date_str, ticker)
                continue
            current_price_row = oconn.execute(
                """
                SELECT current_price_usd FROM predictions
                WHERE ticker = ? AND scoring_date = ?
                LIMIT 1
                """,
                (ticker, scoring_date_str),
            ).fetchone()
            if not current_price_row or current_price_row[0] is None:
                continue
            current_price = float(current_price_row[0])

            for w in windows_weeks:
                key = (ticker, scoring_date_str, int(w))
                if key in existing:
                    result.cells_already_present += 1
                    continue
                target_asof = scoring_date + dt.timedelta(days=int(w) * 7)
                if today < target_asof:
                    result.cells_not_yet += 1
                    continue

                result.cells_eligible += 1
                hit = _closest_forward_close(
                    pconn, ticker, target_asof,
                    max_forward_days=max_forward_days,
                )
                if hit is None:
                    # No price found in the lookup window. Was the ticker
                    # active recently?
                    last_seen = _last_price_date(pconn, ticker)
                    age_days = (today - last_seen).days if last_seen else None
                    if last_seen is None or (age_days is not None and age_days > delist_grace_days):
                        # Treat as delisted; write a placeholder so we don't
                        # retry endlessly.
                        new_rows.append({
                            "ticker": ticker,
                            "scoring_date": scoring_date_str,
                            "weeks_offset": int(w),
                            "asof_date": None,
                            "close_usd": None,
                            "high_usd_to_date": None,
                            "return_pct": None,
                            "return_per_month": None,
                            "delisted": 1,
                            "last_filled_at": now_iso,
                        })
                        result.cells_delisted += 1
                    else:
                        result.cells_missing_in_prices += 1
                    continue

                asof_iso, close_usd, _ = hit
                # high_usd_to_date — max over the FULL [scoring_date, asof] window.
                high_to_date = _max_close_in_window(
                    pconn, ticker, scoring_date, _parse_date(asof_iso),
                )

                return_pct = (close_usd - current_price) / current_price * 100.0
                weeks = int(w)
                months = weeks / 4.33
                return_per_month = return_pct / months if months > 0 else None

                new_rows.append({
                    "ticker": ticker,
                    "scoring_date": scoring_date_str,
                    "weeks_offset": weeks,
                    "asof_date": asof_iso,
                    "close_usd": close_usd,
                    "high_usd_to_date": high_to_date,
                    "return_pct": return_pct,
                    "return_per_month": return_per_month,
                    "delisted": 0,
                    "last_filled_at": now_iso,
                })

        if dry_run:
            log.info("DRY-RUN: would write %d forward_prices rows", len(new_rows))
            return result

        if new_rows:
            cols = (
                "ticker", "scoring_date", "weeks_offset", "asof_date",
                "close_usd", "high_usd_to_date", "return_pct",
                "return_per_month", "delisted", "last_filled_at",
            )
            placeholders = ",".join(f":{c}" for c in cols)
            oconn.executemany(
                f"INSERT INTO forward_prices({','.join(cols)}) VALUES({placeholders}) "
                "ON CONFLICT(ticker, scoring_date, weeks_offset) DO NOTHING",
                new_rows,
            )
            oconn.commit()
            result.cells_filled = len(new_rows)

    return result
