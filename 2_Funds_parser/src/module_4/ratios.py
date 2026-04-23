"""Ratio matrix computation for Module 4b.

For each survivor ticker compute 10 features:
  - 4 raw ratios:     R_4, R_12, R_26, R_52
  - 6 inter-window:   R_4_over_R_12, R_4_over_R_26, R_4_over_R_52,
                      R_12_over_R_26, R_12_over_R_52, R_26_over_R_52

All math uses adjusted_close (decision D8). Lookups use ±tolerance trading
days; missing windows trigger flat-fill (R=1.0) with young_ticker_flag=True
(decision D9).
"""
from __future__ import annotations

import sqlite3
from typing import Optional

import pandas as pd

from .prices import get_price_on_date


WINDOWS_WEEKS: dict[str, int] = {"R_4": 4, "R_12": 12, "R_26": 26, "R_52": 52}

INTER_WINDOW: list[tuple[str, str, str]] = [
    ("R_4_over_R_12", "R_4", "R_12"),
    ("R_4_over_R_26", "R_4", "R_26"),
    ("R_4_over_R_52", "R_4", "R_52"),
    ("R_12_over_R_26", "R_12", "R_26"),
    ("R_12_over_R_52", "R_12", "R_52"),
    ("R_26_over_R_52", "R_26", "R_52"),
]


def _get_today_price(conn: sqlite3.Connection, ticker: str,
                     reference_date: pd.Timestamp,
                     tolerance_trading_days: int) -> tuple[Optional[float], Optional[pd.Timestamp]]:
    """Latest available price at or before reference_date, within tolerance."""
    return get_price_on_date(conn, ticker, reference_date, tolerance_trading_days)


def _ticker_history_bounds(conn: sqlite3.Connection, ticker: str
                           ) -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp]]:
    row = conn.execute(
        "SELECT MIN(date), MAX(date) FROM prices WHERE ticker=?", (ticker,),
    ).fetchone()
    if not row or not row[0]:
        return None, None
    return pd.Timestamp(row[0]), pd.Timestamp(row[1])


def compute_ratios_for_ticker(
    conn: sqlite3.Connection,
    ticker: str,
    reference_date: pd.Timestamp,
    tolerance_trading_days: int = 3,
    flat_fill: bool = True,
) -> dict:
    """Compute the 10-feature ratio matrix for one ticker.

    Returns:
      dict with R_4/12/26/52, all 6 inter-window ratios, source dates per
      window, weeks_of_history_used, young_ticker_flag, price_today.

    On insufficient history:
      - if flat_fill=True: missing R_Xw set to 1.0, young_ticker_flag=True
      - if flat_fill=False: missing values set to None (caller decides exclusion)
    """
    out: dict = {
        "ticker": ticker,
        "price_today": None,
        "weeks_of_history_used": None,
        "young_ticker_flag": False,
    }
    for k in WINDOWS_WEEKS:
        out[k] = None
    for (k, _, _) in INTER_WINDOW:
        out[k] = None
    for k in WINDOWS_WEEKS:
        out[f"price_source_date_{k[2:]}w"] = None

    first_date, last_date = _ticker_history_bounds(conn, ticker)
    if first_date is None:
        # No data at all
        if flat_fill:
            out["young_ticker_flag"] = True
            for k in WINDOWS_WEEKS:
                out[k] = 1.0
            for (k, _, _) in INTER_WINDOW:
                out[k] = 1.0
        return out

    weeks_of_history = max(int((last_date - first_date).days / 7.0), 0)
    out["weeks_of_history_used"] = weeks_of_history

    price_today, today_actual = _get_today_price(
        conn, ticker, reference_date, tolerance_trading_days
    )
    if price_today is None or price_today <= 0:
        if flat_fill:
            out["young_ticker_flag"] = True
            for k in WINDOWS_WEEKS:
                out[k] = 1.0
            for (k, _, _) in INTER_WINDOW:
                out[k] = 1.0
        return out
    out["price_today"] = price_today

    for k, weeks in WINDOWS_WEEKS.items():
        target = reference_date - pd.Timedelta(weeks=weeks)
        if target < first_date:
            if flat_fill:
                out[k] = 1.0
                out["young_ticker_flag"] = True
            continue
        past_price, actual = get_price_on_date(
            conn, ticker, target, tolerance_trading_days
        )
        if past_price is None or past_price <= 0:
            if flat_fill:
                out[k] = 1.0
                out["young_ticker_flag"] = True
            continue
        out[k] = price_today / past_price
        out[f"price_source_date_{k[2:]}w"] = actual.strftime("%Y-%m-%d") if actual is not None else None

    # Inter-window ratios — only computable when both numerator and
    # denominator are present (and denominator non-zero).
    for name, num_key, den_key in INTER_WINDOW:
        num = out.get(num_key)
        den = out.get(den_key)
        if num is None or den is None or den == 0:
            if flat_fill:
                out[name] = 1.0
            continue
        out[name] = num / den

    return out
