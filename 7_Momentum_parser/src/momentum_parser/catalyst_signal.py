"""Catalyst as a FORWARD FACT + a FALSIFIABLE HYPOTHESIS (spec §2.1-G revised / M15) — pure.

A scheduled catalyst is redefined from a bare calendar date into two anticipatory, forward-only reads:
  * **forward fact** — `pre_event_accumulation`: is price/volume MICRO-STRUCTURE accumulating INTO the known
    date right now? (rising price on rising volume = real positioning; this is happening pre-event, so it is
    anticipatory, not a recap of a result). For an earnings date this doubles as the expected-beat/miss lean
    (a proper estimate-revision feed is a later provider, like the geopolitical stub).
  * **falsifiable hypothesis** — `analog_drift`: a quantified predicted drift `expected_drift ± dispersion`
    from the ticker's own forward-return distribution, RECORDED so the ledger can settle it vs realized and
    the M16 loop can learn (a calculated guess with a track record, not a static fact).
Never reads the wall clock; ``asof`` is always passed. The *result* of the event stays out of scope (§2.2).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Optional


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def days_to(event_date: str, asof: str) -> Optional[int]:
    try:
        return (date.fromisoformat(event_date) - date.fromisoformat(asof[:10])).days
    except (ValueError, TypeError):
        return None


def pre_event_accumulation(bars, window: int, scale: float) -> float:
    """Forward fact: price drift into the event, amplified when volume is surging (real accumulation).

    Direction from the recent price drift; magnitude damped when volume isn't confirming. In [-1, 1].
    """
    if window < 1 or len(bars) < 2 * window:
        return 0.0
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    base = closes[-1 - window]
    pdrift = (closes[-1] / base - 1.0) if base else 0.0
    recent_v, prior_v = _mean(vols[-window:]), _mean(vols[-2 * window:-window])
    vsurge = (recent_v / prior_v - 1.0) if prior_v > 0 else 0.0
    price_term = math.tanh(pdrift / scale) if scale else 0.0
    vol_term = math.tanh(max(0.0, vsurge))                 # only rising volume confirms accumulation
    return round(_clip(price_term * (0.5 + 0.5 * vol_term)), 4)


def analog_drift(bars, horizon: int) -> tuple[float, float]:
    """Falsifiable hypothesis: (expected_drift, dispersion) from the ticker's forward-``horizon`` returns."""
    closes = [b.close for b in bars]
    fwd = [closes[i + horizon] / closes[i] - 1.0 for i in range(len(closes) - horizon) if closes[i]]
    if not fwd:
        return 0.0, 0.0
    m = _mean(fwd)
    return round(m, 4), round(math.sqrt(_mean([(x - m) ** 2 for x in fwd])), 4)


def hypothesis(bars, event_date: Optional[str], asof: str, cfg: dict, horizon: int) -> Optional[dict]:
    """The forward catalyst read for the soonest event, or None when none lands inside the horizon."""
    d = days_to(event_date, asof) if event_date else None
    hz = int(cfg.get("catalyst_horizon_days", 21))
    if d is None or d < 0 or d > hz:
        return None
    acc = pre_event_accumulation(bars, int(cfg.get("catalyst_accum_window", 5)),
                                 float(cfg.get("catalyst_accum_scale", 0.1)))
    drift, disp = analog_drift(bars, horizon)
    return {"days_to_catalyst": d, "accumulation": acc, "expected_drift": drift,
            "dispersion": disp, "horizon_days": horizon, "falsifiable": True}


def catalyst_score(hyp: Optional[dict], cfg: dict) -> float:
    """The enriched `catalyst` dimension score in [-1,1]: accumulation (proximity-weighted) + a drift lean."""
    if not hyp:
        return 0.0
    hz = int(cfg.get("catalyst_horizon_days", 21))
    scale = float(cfg.get("catalyst_accum_scale", 0.1))
    prox = max(0.0, 1.0 - hyp["days_to_catalyst"] / hz) if hz else 0.0
    drift_term = math.tanh(hyp["expected_drift"] / scale) if scale else 0.0
    return round(_clip(hyp["accumulation"] * (0.5 + 0.5 * prox) + drift_term * 0.25), 4)
