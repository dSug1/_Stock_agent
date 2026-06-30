"""Daily OHLCV provider.

yfinance is the scaffold provider — LOCAL-ONLY per its ToS. Swap for a licensed feed (FMP / EODHD /
Twelve Data) before any public deploy; this is repo-wide policy, not optional (see project memory
``data_provider_switch``). The provider is isolated behind :func:`fetch_daily_bars` so the swap is a
one-file change and the rest of the pipeline never imports yfinance directly.
"""

from __future__ import annotations

import math
from typing import Optional

from ..models import Bar


def fetch_daily_bars(ticker: str, period: str = "2y", interval: str = "1d") -> list[Bar]:
    """Fetch daily bars for ``ticker``. Returns ``[]`` on any failure (fail-open: a missing ticker
    never aborts the run). Imports yfinance lazily so the package imports without it installed."""
    try:
        import yfinance as yf
    except ImportError:  # pragma: no cover - provider optional at import time
        return []
    try:
        df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    except Exception:  # pragma: no cover - network/provider error -> fail open
        return []
    if df is None or df.empty:
        return []
    bars: list[Bar] = []
    for idx, row in df.iterrows():
        close = _f(row.get("Close"))
        if close is None or math.isnan(close):
            continue
        bars.append(Bar(
            date=idx.strftime("%Y-%m-%d"),
            open=_f(row.get("Open")) or close,
            high=_f(row.get("High")) or close,
            low=_f(row.get("Low")) or close,
            close=close,
            volume=_f(row.get("Volume")) or 0.0,
        ))
    return bars


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
