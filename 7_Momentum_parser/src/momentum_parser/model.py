"""The code-side `p_model` leg of the hybrid (spec §8) — re-targeted to the vol-normalized label (M1).

`probability.py` holds the primitives; this wires them to `targets.label_series` so `p_model` estimates
**P(up per the dead-band)** — the same label `p_claude` and the backtest use, so the two legs are
comparable. Logistic baseline over the signal composite + standardized momentum, blended with the ticker's
own historical **label-up-rate** among comparable-composite days. Pure / offline.
"""

from __future__ import annotations

from typing import Optional, Sequence

from . import probability, targets
from .signals import compute_signals
from .stage2_signals import rolling_composites


def _label_up_rate(comps: Sequence[Optional[float]], labels: Sequence[Optional[str]],
                   current: float, band: float, min_samples: int) -> Optional[float]:
    """Historical up-rate (vol-normalized label) among days whose composite was within ``band`` of now.

    Laplace-smoothed; ``None`` when too few comparable labelled days (caller falls back to the logistic).
    """
    hits = total = 0
    for c, lab in zip(comps, labels):
        if c is None or lab is None or abs(c - current) > band:
            continue
        total += 1
        if lab == targets.UP:
            hits += 1
    if total < min_samples:
        return None
    return (hits + 0.5) / (total + 1.0)


def p_up(bars, cfg: dict, topdown_logit: float = 0.0) -> tuple[float, float, dict]:
    """Return ``(p_model, expected_return, components)`` for one ticker. ``p_model`` = P(up) in [0.01,0.99].

    ``topdown_logit`` (spec §4b / M13) is a log-odds shift from the top-down layer
    (regime + Σ w_s·β_{t,s}·surprise_s). Default 0.0 = no-op, so the historical PIT backtest — which has
    no macro archive — is unchanged and the two legs stay comparable.
    """
    prob_cfg = cfg.get("probability", {})
    sig_cfg = cfg.get("signals", {})
    closes = [b.close for b in bars]
    horizon = int(prob_cfg.get("horizon_days", 5))
    band_mult = float(prob_cfg.get("target", {}).get("vol_band_mult", 0.5))
    win_weeks = int(prob_cfg.get("target", {}).get("vol_window_weeks", 12))
    min_bars = int(sig_cfg.get("min_bars", 60))

    _signals, composite = compute_signals(bars, sig_cfg)
    drift = probability.realized_drift(closes, int(prob_cfg.get("drift_window", 20)))
    p_log = probability.logistic_probability(composite, prob_cfg, drift)
    sigma = targets.weekly_sigma(closes, win_weeks, horizon)

    p = p_log
    if prob_cfg.get("use_empirical", True) and len(bars) >= min_bars:
        comps = rolling_composites(bars, cfg, min_bars)
        labels = targets.label_series(closes, horizon, win_weeks, band_mult)
        p_emp = _label_up_rate(comps, labels, composite,
                               float(prob_cfg.get("empirical_band", 0.25)),
                               int(prob_cfg.get("empirical_min_samples", 20)))
        if p_emp is not None:
            w = float(prob_cfg.get("empirical_max_weight", 0.5))
            p = (1 - w) * p_log + w * p_emp

    p = probability.apply_logit_delta(p, topdown_logit)    # top-down layer (§4b / M13); 0.0 = no-op

    fwd = probability.forward_returns(closes, horizon)
    typical = (sum(abs(x) for x in fwd) / len(fwd)) if fwd else 0.0
    expected_return = typical * 2.0 * (p - 0.5)
    p = min(0.99, max(0.01, p))
    # `typical` is exported so the blend can re-derive expected_return from p_final (not just p_model),
    # keeping the reported return sign-consistent with the reported probability.
    return p, expected_return, {"composite": composite, "sigma_week": sigma, "p_log": p_log,
                                "topdown_logit": topdown_logit, "typical": typical}
