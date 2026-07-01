"""Top-down signal computation — PURE functions (spec §4b). Series in, anticipated *surprise* out.

No I/O, no provider imports → fully offline-testable. Each market/factor signal maps to a signed
``surprise`` in [-1, 1] = its anticipated tailwind(+)/headwind(-) on P(up) over the coming week. These
are FIRST-PASS heuristics: the sign/magnitude priors here are what the M16 feedback loop then *learns*
(the point of M12 is to POPULATE `macro_signals` with a real, forward, anticipated read — not to be the
final weighting). Continuous signals read the recent *shift* (not the level), per §2.3.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

Number = Optional[float]


def _clean(series: Sequence[Number]) -> list[float]:
    return [float(x) for x in (series or []) if x is not None]


def lookback_return(series: Sequence[Number], n: int) -> Number:
    """Fractional return over the last ``n`` steps; None if not enough data or a zero base."""
    s = _clean(series)
    if len(s) < n + 1 or s[-n - 1] == 0:
        return None
    return s[-1] / s[-n - 1] - 1.0


def squash(x: Number, scale: float) -> float:
    """Map an unbounded move to [-1, 1] via tanh; 0.0 for missing (absence ≠ signal)."""
    if x is None or not scale:
        return 0.0
    return math.tanh(x / scale)


def ratio(a: Sequence[Number], b: Sequence[Number]) -> list[Number]:
    return [(x / y if (x is not None and y not in (None, 0)) else None) for x, y in zip(a, b)]


def last(series: Sequence[Number]) -> Number:
    for x in reversed(list(series or [])):
        if x is not None:
            return float(x)
    return None


def relative_momentum(series: Sequence[Number], bench: Sequence[Number], n: int) -> Number:
    """Return of ``series`` minus return of ``bench`` over ``n`` (the rotation/factor primitive)."""
    a = lookback_return(series, n)
    if a is None:
        return None
    return a - (lookback_return(bench, n) or 0.0)


def classify_regime(vix: Sequence[Number], hy: Sequence[Number], ig: Sequence[Number], cfg: dict) -> str:
    """risk_on / neutral / risk_off from VIX level + HY/IG credit-spread trend (spec §4b)."""
    r = (cfg.get("regime") or {})
    vhi, vlo = float(r.get("vix_high", 22.0)), float(r.get("vix_low", 15.0))
    lb = int(r.get("credit_lookback", 20))
    widen = float(r.get("credit_widen", -0.01))
    v = last(vix)
    if v is None:
        return "neutral"                                   # fail-open: no read -> neutral, never a lean
    ctrend = lookback_return(ratio(hy, ig), lb)            # HY/IG rising = risk appetite up
    if v >= vhi or (ctrend is not None and ctrend < widen):
        return "risk_off"
    if v <= vlo and (ctrend is None or ctrend >= 0):
        return "risk_on"
    return "neutral"


def regime_surprise(regime: str) -> float:
    """The market-scope tailwind from the current regime lean (±0.4 lean, 0 neutral)."""
    return {"risk_on": 0.4, "neutral": 0.0, "risk_off": -0.4}.get(regime, 0.0)


def continuous_surprises(series_by_role: dict[str, list], cfg: dict) -> dict[str, float]:
    """Compute the surprise for each market/factor signal from the proxy series (first-pass signs).

    Keys returned: risk_regime, rates_usd, commodities (cross-asset), ai_crowding, style_factors,
    sector_flows (rotation). Missing proxies → 0.0 (fail-open, absence ≠ negative evidence).
    """
    lb = int(cfg.get("lookback_days", 20))
    scale = float(cfg.get("squash_scale", 0.05))
    g = series_by_role.get

    regime = classify_regime(g("vix", []), g("credit_hy", []), g("credit_ig", []), cfg)

    # rising rates / stronger USD = headwind for high-beta longs -> negative
    rates = -0.5 * squash(lookback_return(g("tnx", []), lb), scale) \
            - 0.5 * squash(lookback_return(g("dxy", []), lb), scale)
    # sharp oil/gold rallies read as risk-off for retail/hype longs -> negative (small weight anyway)
    commod = -0.5 * squash(lookback_return(g("oil", []), lb), scale) \
             - 0.5 * squash(lookback_return(g("gold", []), lb), scale)
    # AI-trade momentum vs market (the "rotation from AI" axis; unwind-risk nuance deferred to M16)
    ai = squash(relative_momentum(g("ai_basket", []), g("market", []), lb), scale)
    style = squash(relative_momentum(g("growth", []), g("value", []), lb), scale)
    sector = squash(relative_momentum(g("hibeta", []), g("lowvol", []), lb), scale)

    return {
        "risk_regime": round(regime_surprise(regime), 4),
        "rates_usd": round(rates, 4),
        "commodities": round(commod, 4),
        "ai_crowding": round(ai, 4),
        "style_factors": round(style, 4),
        "sector_flows": round(sector, 4),
        "_regime": regime,                                 # carried out for the store's regime column
    }
