"""Web-search-statistics provider (Stage 2 search dimension): daily search-interest series.

The 5_Hype_parser idea — search-interest *surge vs baseline* as a retail-attention proxy (social media was
removed, Decision B). Provider DEFERRED (a Trends-style feed; PIT-archived history for the §9 backtest).
Isolated + **FAIL-OPEN**: empty when no provider is wired.
"""

from __future__ import annotations


def daily_interest(ticker: str, asof: str | None = None, lookback: int = 60) -> list[float]:
    """Daily search-interest index ending at ``asof``. ``[]`` when no provider is wired (fail-open)."""
    return []
