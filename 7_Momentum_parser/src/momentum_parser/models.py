"""Lightweight data structures shared across the pipeline.

Deliberately plain (``dataclass`` + ``NamedTuple``) so the signal/probability core has no heavy
dependency and unit-tests run offline on synthetic series.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple, Optional


class Bar(NamedTuple):
    """One daily OHLCV bar. ``date`` is an ISO ``YYYY-MM-DD`` string (calendar-day, exchange-local)."""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    """A named trade signal computed for a ticker on an as-of date.

    ``value`` is the raw indicator reading; ``score`` is normalized to roughly [-1, 1] where positive =
    bullish for the coming week. ``fired`` is the boolean trigger (e.g. a fresh SMA cross today).
    """

    name: str
    value: float
    score: float
    fired: bool = False
    note: str = ""


@dataclass
class Prediction:
    """The per-ticker output of a daily run: weekly-change probability + supporting signals."""

    ticker: str
    asof_date: str
    horizon_days: int
    p_up: float                       # P(close in `horizon_days` > close today)
    expected_return: float            # blended point estimate of the forward return (fraction)
    confidence: float                 # 0..1, shrinks when history is short / signals disagree
    composite: float                  # weighted bullishness in [-1, 1]
    signals: list[Signal] = field(default_factory=list)
    last_close: Optional[float] = None
    # hybrid-blend components (Stage 5 / M4); p_up holds the blended p_final
    p_claude: Optional[float] = None
    p_model: Optional[float] = None
    disagreement: Optional[float] = None
    review: bool = False
