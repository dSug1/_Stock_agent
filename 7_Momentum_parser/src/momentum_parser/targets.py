"""Move-target labeling — the vol-normalized dead-band label (spec v0.3 Decision F).

The label everything is measured against: a forward 5-trading-day move is **`up`** when it exceeds
`+band_mult x sigma_week`, **`down`** when below `-band_mult x sigma_week`, else **`flat`**. Normalizing
by the stock's own weekly volatility means a 5% move counts as signal in a placid name and as noise in a
wild one. Long-only trading acts on `up` (spec §2 F, §8).

Pure functions over plain close series — no network, no pandas — so the label is identical in the live
path, the `p_model` leg (Stage 4), and the backtest/ledger (§9). Simple (not log) returns throughout so
`sigma_week` and the forward return share units.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

DEFAULT_HORIZON = 5            # trading days ~= one calendar week
UP, DOWN, FLAT = "up", "down", "flat"


def weekly_sigma(closes: Sequence[float], window_weeks: int = 12,
                 horizon: int = DEFAULT_HORIZON) -> Optional[float]:
    """Std dev of **non-overlapping** weekly simple returns over the trailing window.

    Non-overlapping blocks avoid the autocorrelation that overlapping windows inject. Returns ``None``
    when fewer than two complete weekly returns are available (caller falls back — see ``label_move``).
    """
    if len(closes) < 2 * horizon + 1:
        return None
    n_bars = window_weeks * horizon
    series = closes[-(n_bars + 1):] if len(closes) > n_bars + 1 else list(closes)
    rets: list[float] = []
    i = len(series) - 1
    while i - horizon >= 0:
        a, b = series[i - horizon], series[i]
        if a > 0:
            rets.append(b / a - 1.0)
        i -= horizon
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var)


def forward_return(closes: Sequence[float], i: int, horizon: int = DEFAULT_HORIZON) -> Optional[float]:
    """Simple fractional return from bar ``i`` to ``i + horizon`` (``None`` if out of range / zero base)."""
    if i < 0 or i + horizon >= len(closes):
        return None
    base = closes[i]
    return (closes[i + horizon] / base - 1.0) if base else None


def label_move(fwd_return: Optional[float], sigma_week: Optional[float],
               band_mult: float = 0.5) -> Optional[str]:
    """Classify a forward return into ``up`` / ``down`` / ``flat`` against the vol-scaled dead-band.

    ``None`` when the return is unknown or volatility can't be estimated (insufficient history) —
    *missing data is never silently labelled* (fail-open, spec §12).
    """
    if fwd_return is None or sigma_week is None or sigma_week <= 0:
        return None
    threshold = band_mult * sigma_week
    if fwd_return > threshold:
        return UP
    if fwd_return < -threshold:
        return DOWN
    return FLAT


def label_series(closes: Sequence[float], horizon: int = DEFAULT_HORIZON,
                 window_weeks: int = 12, band_mult: float = 0.5) -> list[Optional[str]]:
    """Per-bar realized label using volatility known **as of that bar** (point-in-time, no look-ahead).

    Aligned to ``closes``; ``None`` for bars lacking warm-up or a full forward window. This is the
    history the empirical ``p_model`` leg buckets and the backtest scores against.
    """
    out: list[Optional[str]] = []
    for i in range(len(closes)):
        sig = weekly_sigma(closes[: i + 1], window_weeks, horizon)   # only past data
        out.append(label_move(forward_return(closes, i, horizon), sig, band_mult))
    return out
