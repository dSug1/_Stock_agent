"""Leading / pre-move microstructure signals — pure, OHLCV-only (spec §4c, M20).

Forward SETUP indicators that anticipate a move BEFORE any catalyst or established trend — the leading
*inputs* the M19 forward-driver rubric needs to reason forward from (e.g. a tight coil + quiet accumulation
with no known catalyst = a classic pre-breakout driver). Self-contained per ticker: no benchmark, no new
provider (RS-vs-benchmark, short-interest and options are later tiers). All bounded, offline-testable.
"""

from __future__ import annotations

import math
from typing import Sequence

from .models import Bar


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _logrets(closes: Sequence[float]) -> list[float]:
    return [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]


def _stdev(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _slope_norm(series: Sequence[float], window: int) -> float:
    """OLS slope over the last ``window`` points, expressed as a fractional change across the window."""
    s = [float(x) for x in series][-window:]
    n = len(s)
    if n < 3:
        return 0.0
    xs = list(range(n))
    mx, my = (n - 1) / 2.0, sum(s) / n
    var = sum((x - mx) ** 2 for x in xs)
    if var == 0:
        return 0.0
    slope = sum((xs[i] - mx) * (s[i] - my) for i in range(n)) / var
    denom = abs(my) if my else 1.0
    return slope * n / denom


def coil(bars: Sequence[Bar], short: int, long: int) -> float:
    """Volatility COMPRESSION in [0,1]: 1 = current short-window realized vol is at the low end of its own
    recent range (tightly coiled → energy building for expansion); 0 = at the high end."""
    closes = [b.close for b in bars]
    rets = _logrets(closes)
    if len(rets) < short + 1:
        return 0.0
    rolling = [_stdev(rets[i - short:i]) for i in range(short, len(rets) + 1)]
    hist = rolling[-long:] if len(rolling) >= long else rolling
    cur, lo, hi = rolling[-1], min(hist), max(hist)
    if hi <= lo:
        return 0.0
    return round(1.0 - (cur - lo) / (hi - lo), 4)


def cmf(bars: Sequence[Bar], window: int) -> float:
    """Chaikin Money Flow over ``window`` in [-1,1]: volume-weighted buying(+)/selling(-) pressure
    (accumulation vs distribution). Bounded and self-normalizing."""
    w = list(bars)[-window:]
    mfv = vol = 0.0
    for b in w:
        rng = b.high - b.low
        mult = (((b.close - b.low) - (b.high - b.close)) / rng) if rng > 0 else 0.0
        mfv += mult * b.volume
        vol += b.volume
    return round(mfv / vol, 4) if vol > 0 else 0.0


def breakout_pressure(bars: Sequence[Bar], window: int) -> float:
    """Range position in [0,1] (1 = pressing the ``window`` high), lightly discounted when recent volume
    isn't confirming — a coil pressing the top on rising volume is a pre-breakout setup."""
    w = list(bars)[-window:]
    if len(w) < 2:
        return 0.5
    hi = max(b.high for b in w)
    lo = min(b.low for b in w)
    if hi <= lo:
        return 0.5
    pos = (w[-1].close - lo) / (hi - lo)
    recent = sum(b.volume for b in w[-3:]) / min(3, len(w))
    avg = sum(b.volume for b in w) / len(w)
    vconf = 1.0 if (avg > 0 and recent >= avg) else 0.7
    return round(pos * vconf, 4)


def relative_strength(closes: Sequence[float], bench_closes: Sequence[float], window: int):
    """RS of the ticker vs a benchmark (e.g. SPY): momentum of the RS line + a leading INFLECTION (its slope
    turning up). None if data is short. RS-momentum > 0 = outperforming; inflection anticipates a leadership
    turn before it shows in absolute price."""
    n = min(len(closes), len(bench_closes))
    if n < window + 2:
        return None
    c, b = list(closes)[-n:], list(bench_closes)[-n:]
    rs = [c[i] / b[i] for i in range(n) if b[i] > 0]
    if len(rs) < window + 2:
        return None
    base = rs[-window - 1]
    rs_mom = (rs[-1] / base - 1.0) if base else 0.0
    half = max(2, window // 2)
    recent = _slope_norm(rs[-half:], half)
    prior = _slope_norm(rs[-2 * half:-half], half) if len(rs) >= 2 * half else 0.0
    return {"rs_momentum": round(rs_mom, 4), "rs_slope": round(recent, 4),
            "rs_inflection": (recent > prior) and (recent > 0)}


def leading_features(bars: Sequence[Bar], cfg: dict, bench_closes: Sequence[float] | None = None) -> tuple[dict, float]:
    """Assemble the leading setup read → (features, leading_score∈[-1,1]). ``leading_score`` is a first-pass
    combination (accumulation, breakout pressure, coil energy signed by accumulation, + RS momentum when a
    benchmark is given); the rubric reasons over the components. Bullish DIVERGENCE = accumulating while
    price drifts down. RS inflection = the ticker's leadership vs the benchmark turning up (leading)."""
    if len(bars) < int(cfg.get("min_bars", 30)):
        return {"no_data": True}, 0.0
    closes = [b.close for b in bars]
    coil_v = coil(bars, int(cfg.get("coil_short", 5)), int(cfg.get("coil_long", 60)))
    acc = cmf(bars, int(cfg.get("cmf_window", 20)))
    px_slope = _slope_norm(closes, int(cfg.get("cmf_window", 20)))
    bull_div = (acc > float(cfg.get("divergence_min", 0.05))) and (px_slope < 0)
    brk = breakout_pressure(bars, int(cfg.get("breakout_window", 20)))
    leading_score = 0.5 * acc + 0.3 * (2 * brk - 1) + 0.2 * (coil_v if acc >= 0 else -coil_v)
    feats = {
        "coil": coil_v,                          # 0..1 compression (energy)
        "accumulation_cmf": acc,                 # -1..1 buying/selling pressure
        "bullish_divergence": bull_div,          # accumulating into price weakness
        "breakout_pressure": brk,                # 0..1 pressing the range high (vol-confirmed)
    }
    if bench_closes is not None:
        rs = relative_strength(closes, bench_closes, int(cfg.get("rs_window", 20)))
        if rs:
            feats.update(rs)                     # rs_momentum / rs_slope / rs_inflection
            leading_score += 0.25 * _clip(rs["rs_momentum"] / float(cfg.get("rs_scale", 0.05)))
    leading_score = _clip(leading_score)
    feats["leading_score"] = round(leading_score, 4)
    return feats, leading_score
