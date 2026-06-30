"""Mainstream-media provider (Stage 2 media dimension): daily article counts + tone.

Provider is a DEFERRED decision — GDELT is the free candidate (and PIT-archived, which the §9 backtest
needs); a licensed feed for production. Isolated here so the rest of the pipeline is provider-agnostic.
Default implementation is **FAIL-OPEN**: returns empty series when no provider is wired, so the pipeline
runs and Claude's `web_search` still reads media live at Stage 3 (data-coverage fairness — a signal you
can't find is not negative evidence). Untrusted text never enters a prompt from here (numbers only).
"""

from __future__ import annotations

from typing import Sequence


def daily_counts(ticker: str, asof: str | None = None, lookback: int = 60) -> list[float]:
    """Daily article/headline counts ending at ``asof``. ``[]`` when no provider is wired (fail-open)."""
    return []


def daily_tone(ticker: str, asof: str | None = None, lookback: int = 60) -> list[float]:
    """Daily mean article tone in [-1, 1] ending at ``asof``. ``[]`` when no provider is wired."""
    return []
