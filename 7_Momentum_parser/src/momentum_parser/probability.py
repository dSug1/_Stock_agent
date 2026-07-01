"""Weekly price-change probability — turn a daily signal composite into P(up over the next week).

Two estimators, both pure and offline-testable:

* :func:`logistic_probability` — the default *baseline*. Maps the signal ``composite`` (+ optional
  empirical drift) through a logistic to P(close_{t+H} > close_t). Deterministic, no training. The
  slope ``beta`` and ``bias`` are config knobs (``config.yaml::probability``).
* :func:`empirical_probability` — a *calibration* estimator. From a ticker's own history, bucket past
  days by their composite and measure the realized forward-H up-rate. Used by the (pending) backtest to
  fit ``beta``/``bias`` and to sanity-check the baseline. Falls back to the logistic when history is thin.

Calibration of the baseline against realized returns is the first real milestone after the scaffold
(see ``spec/decisions.md`` D1). Until then probabilities are *indicative*, mirroring how 5_Hype_parser
labelled its first panel.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence

DEFAULT_HORIZON = 5          # trading days ~= one calendar week
_EPS = 1e-9


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def logistic_probability(composite: float, cfg: dict, drift: float = 0.0) -> float:
    """Baseline P(up) = sigmoid(beta * composite + bias + drift_weight * drift).

    ``composite`` in [-1, 1] from :func:`signals.compute_signals`; ``drift`` is an optional standardized
    momentum term (e.g. recent mean return / vol). Clamped to [0.01, 0.99] so no signal is ever sold as
    a certainty.
    """
    beta = float(cfg.get("beta", 2.2))
    bias = float(cfg.get("bias", 0.0))
    dw = float(cfg.get("drift_weight", 0.5))
    p = _sigmoid(beta * composite + bias + dw * drift)
    return min(0.99, max(0.01, p))


def apply_logit_delta(p: float, delta: float) -> float:
    """Shift a probability by ``delta`` in log-odds space, then clamp to [0.01, 0.99].

    The top-down layer (spec §4b / M13) adds its contribution here so a regime/rotation read nudges
    ``p_model`` without ever forcing a certainty. ``delta`` = 0 is an exact no-op.
    """
    if not delta:
        return p
    q = min(1.0 - _EPS, max(_EPS, p))
    return min(0.99, max(0.01, _sigmoid(math.log(q / (1.0 - q)) + delta)))


def forward_returns(closes: Sequence[float], horizon: int) -> list[float]:
    """Realized fractional return from each bar to ``horizon`` bars later (length = len-horizon)."""
    out: list[float] = []
    for i in range(len(closes) - horizon):
        base = closes[i]
        if base:
            out.append(closes[i + horizon] / base - 1.0)
    return out


def empirical_probability(
    closes: Sequence[float],
    composites: Sequence[Optional[float]],
    current_composite: float,
    horizon: int = DEFAULT_HORIZON,
    band: float = 0.25,
    min_samples: int = 20,
) -> Optional[float]:
    """Realized up-rate over history for days whose composite was within ``band`` of the current one.

    Returns ``None`` when fewer than ``min_samples`` comparable days exist (caller should fall back to
    the logistic baseline). This is the honest, ticker-specific estimate the backtest calibrates against.
    """
    n = min(len(closes) - horizon, len(composites))
    hits = total = 0
    for i in range(n):
        c = composites[i]
        if c is None or abs(c - current_composite) > band:
            continue
        base = closes[i]
        if not base:
            continue
        total += 1
        if closes[i + horizon] > base:
            hits += 1
    if total < min_samples:
        return None
    return (hits + 0.5) / (total + 1.0)          # Laplace-smoothed


def realized_drift(closes: Sequence[float], window: int = 20) -> float:
    """Standardized momentum: mean daily log-return over ``window`` divided by its stdev (Sharpe-like)."""
    if len(closes) <= window:
        return 0.0
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - window, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var)
    return mean / sd if sd > _EPS else 0.0


def estimate(
    closes: Sequence[float],
    composite: float,
    cfg: dict,
    composites_hist: Optional[Sequence[Optional[float]]] = None,
) -> tuple[float, float, float]:
    """Blend the empirical and logistic estimates into ``(p_up, expected_return, confidence)``.

    When enough comparable history exists the empirical estimate is blended in (weight grows with sample
    support, capped by ``empirical_max_weight``); otherwise the logistic baseline stands alone and
    confidence is reduced.
    """
    horizon = int(cfg.get("horizon_days", DEFAULT_HORIZON))
    drift = realized_drift(closes, int(cfg.get("drift_window", 20)))
    p_log = logistic_probability(composite, cfg, drift)

    p = p_log
    confidence = 0.4
    if composites_hist is not None:
        p_emp = empirical_probability(closes, composites_hist, composite, horizon,
                                      float(cfg.get("empirical_band", 0.25)),
                                      int(cfg.get("empirical_min_samples", 20)))
        if p_emp is not None:
            w = float(cfg.get("empirical_max_weight", 0.5))
            p = (1 - w) * p_log + w * p_emp
            confidence = 0.7

    # Expected return: scale the historical move size by directional conviction (p - 0.5).
    fwd = forward_returns(closes, horizon)
    typical = (sum(abs(x) for x in fwd) / len(fwd)) if fwd else 0.0
    expected_return = typical * 2.0 * (p - 0.5)
    if len(closes) < horizon * 4:
        confidence *= 0.5
    return p, expected_return, min(1.0, confidence)
