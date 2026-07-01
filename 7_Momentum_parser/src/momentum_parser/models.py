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


# --- top-down market-perturbation layer (v0.4 / Decision L / spec §4b) --------------------------
@dataclass
class MacroSignal:
    """The ANTICIPATED state of one taxonomy signal on an as-of date (before it prints — §2.3).

    ``surprise`` is the estimated signed impact (~[-1,1]); ``horizon_days`` is how far out a `dated`
    signal is expected to land (None for `continuous`). ``regime`` is the market read for `market`-scope
    signals. A signal is only ``active`` while anticipated; once it fires it becomes attribution-only.
    """

    asof: str
    signal_id: str
    active: bool
    surprise: Optional[float] = None
    regime: Optional[str] = None
    horizon_days: Optional[int] = None
    note: str = ""


@dataclass
class SignalWeight:
    """A LEARNED per-signal weight ``w_s`` (regime-conditional; regime='all' = fallback) + its prior."""

    signal_id: str
    regime: str
    w: float
    w_prior: float
    n: int = 0


@dataclass
class TickerLoading:
    """A ticker's LEARNED sensitivity ``beta_{t,s}`` to a signal (init from sector/factor class)."""

    ticker: str
    signal_id: str
    beta: float
    beta_prior: float = 0.0
    n: int = 0


@dataclass
class Attribution:
    """A settled 5-day move decomposed into market / factor / idiosyncratic — feeds weight learning (§4b.3)."""

    ticker: str
    asof: str
    run_id: str
    realized_return: Optional[float] = None
    market_comp: Optional[float] = None
    factor_comp: Optional[float] = None
    idio_comp: Optional[float] = None
