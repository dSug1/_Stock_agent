"""Trade-signal computation — pure functions over price/volume series.

No network, no pandas: operates on plain lists of floats so it's fast and trivially unit-testable on
synthetic data. Each indicator returns a list aligned to the input (``None`` for the warm-up window).
``compute_signals`` rolls them into a list of :class:`Signal` plus a single bullish ``composite`` in
[-1, 1] that Stage 3 turns into a probability.

All indicators are standard and intentionally transparent; the spec (``spec/SPEC_momentum_parser.md``
§4) records why each is in the basket. Tune weights in ``config/config.yaml::signals``.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

from .models import Bar, Signal


# --------------------------------------------------------------------------- core series helpers


def sma(values: Sequence[float], window: int) -> list[Optional[float]]:
    """Simple moving average; ``None`` until ``window`` values are available."""
    out: list[Optional[float]] = []
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= window:
            acc -= values[i - window]
        out.append(acc / window if i >= window - 1 else None)
    return out


def ema(values: Sequence[float], window: int) -> list[Optional[float]]:
    """Exponential moving average seeded with the first SMA; ``None`` during warm-up."""
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < window:
        return out
    k = 2.0 / (window + 1.0)
    prev = sum(values[:window]) / window
    out[window - 1] = prev
    for i in range(window, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: Sequence[float], window: int = 14) -> list[Optional[float]]:
    """Wilder's RSI in [0, 100]; ``None`` until ``window`` deltas are available."""
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= window:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, window + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / window, losses / window
    out[window] = _rsi_from(avg_gain, avg_loss)
    for i in range(window + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (window - 1) + max(d, 0.0)) / window
        avg_loss = (avg_loss * (window - 1) + max(-d, 0.0)) / window
        out[i] = _rsi_from(avg_gain, avg_loss)
    return out


def _rsi_from(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def roc(closes: Sequence[float], window: int) -> Optional[float]:
    """Rate of change over ``window`` bars as a fraction (e.g. 0.05 = +5%)."""
    if len(closes) <= window or closes[-1 - window] == 0:
        return None
    return closes[-1] / closes[-1 - window] - 1.0


def macd_hist(closes: Sequence[float], fast: int = 12, slow: int = 26, sig: int = 9) -> Optional[float]:
    """Latest MACD histogram value (MACD line minus its signal EMA), or ``None`` if too short."""
    if len(closes) < slow + sig:
        return None
    ef, es = ema(closes, fast), ema(closes, slow)
    macd_line = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)]
    present = [m for m in macd_line if m is not None]
    sig_line = ema(present, sig)
    if not sig_line or sig_line[-1] is None:
        return None
    return present[-1] - sig_line[-1]


# --------------------------------------------------------------------------- aggregator


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def compute_signals(bars: Sequence[Bar], cfg: dict) -> tuple[list[Signal], float]:
    """Compute the configured signal basket for one ticker as of the last bar.

    Returns ``(signals, composite)`` where ``composite`` in [-1, 1] is the weighted bullishness used by
    Stage 3. Robust to short history: any indicator lacking enough warm-up is simply omitted, and the
    composite is weighted only over the signals that fired.
    """
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    weights: dict = (cfg or {}).get("weights", {})
    signals: list[Signal] = []

    # --- Trend: fast vs slow SMA (golden/death cross), scored by gap, fired on a fresh cross ----
    fast_w, slow_w = cfg.get("sma_fast", 20), cfg.get("sma_slow", 50)
    sf, ss = sma(closes, fast_w), sma(closes, slow_w)
    if sf[-1] is not None and ss[-1] is not None and ss[-1] != 0:
        gap = (sf[-1] - ss[-1]) / ss[-1]
        fired = (
            len(sf) >= 2 and sf[-2] is not None and ss[-2] is not None
            and (sf[-2] - ss[-2]) * (sf[-1] - ss[-1]) < 0          # sign flipped since yesterday
        )
        signals.append(Signal("sma_cross", gap, _clip(gap * 10), fired,
                              f"SMA{fast_w} vs SMA{slow_w} gap {gap:+.2%}"))

    # --- Momentum: rate of change over the lookback ---------------------------------------------
    roc_w = cfg.get("roc_window", 20)
    r = roc(closes, roc_w)
    if r is not None:
        signals.append(Signal("roc", r, _clip(r * 5), abs(r) > cfg.get("roc_fire", 0.10),
                              f"{roc_w}d ROC {r:+.2%}"))

    # --- RSI: distance from neutral 50, with overbought/oversold flag ---------------------------
    rsi_vals = rsi(closes, cfg.get("rsi_window", 14))
    if rsi_vals[-1] is not None:
        rv = rsi_vals[-1]
        signals.append(Signal("rsi", rv, _clip((rv - 50) / 50),
                              rv >= cfg.get("rsi_overbought", 70) or rv <= cfg.get("rsi_oversold", 30),
                              f"RSI {rv:.1f}"))

    # --- MACD histogram sign/strength -----------------------------------------------------------
    mh = macd_hist(closes)
    if mh is not None and closes[-1]:
        norm = mh / closes[-1]
        signals.append(Signal("macd_hist", mh, _clip(norm * 50), False, f"MACD hist {norm:+.3%}"))

    # --- Volume surge: today's volume vs its average (confirmation, unsigned -> scaled by ROC) --
    vol_w = cfg.get("volume_window", 20)
    va = sma(vols, vol_w)
    if va[-1] and va[-1] > 0:
        ratio = vols[-1] / va[-1]
        direction = 1.0 if (r or 0) >= 0 else -1.0
        signals.append(Signal("volume_surge", ratio, _clip((ratio - 1.0)) * direction,
                              ratio >= cfg.get("volume_fire", 2.0), f"vol {ratio:.2f}x avg"))

    # --- Breakout: new N-day high/low -----------------------------------------------------------
    bw = cfg.get("breakout_window", 55)
    if len(closes) > bw:
        window = closes[-bw - 1:-1]
        hi, lo = max(window), min(window)
        if closes[-1] > hi:
            signals.append(Signal("breakout", 1.0, 0.8, True, f"new {bw}d high"))
        elif closes[-1] < lo:
            signals.append(Signal("breakout", -1.0, -0.8, True, f"new {bw}d low"))

    composite = _weighted_composite(signals, weights)
    return signals, composite


def _weighted_composite(signals: list[Signal], weights: dict) -> float:
    """Weighted average of signal scores; defaults to equal weight when a signal isn't in config."""
    if not signals:
        return 0.0
    num = sum(s.score * float(weights.get(s.name, 1.0)) for s in signals)
    den = sum(abs(float(weights.get(s.name, 1.0))) for s in signals)
    return _clip(num / den) if den else 0.0
