"""Historical PIT backtest of the `p_model` leg (spec §9.2, Decision E part a).

OHLCV is the only point-in-time-clean source available today (the media/search archives are deferred), so
this backtests the **code-side `p_model`** — the leg that *can* be reconstructed historically. For each
non-overlapping decision bar it recomputes `p_model` using **only past data** and scores it against the
realized vol-normalized label. Output feeds `metrics.summary` (Brier / reliability / hit-rate) and will
calibrate `p_model` once wired into the blend (the Claude leg stays forward-validated via the ledger).

Efficiency: composites and PIT labels are precomputed **once** per ticker; the empirical leg only uses
outcomes realized *before* the decision bar (honest PIT). The formula mirrors `model.p_up` — kept in sync.
"""

from __future__ import annotations

from . import probability, targets
from .stage2_signals import rolling_composites


def backtest_ticker(bars, cfg: dict) -> list[dict]:
    prob = cfg.get("probability", {})
    sig = cfg.get("signals", {})
    horizon = int(prob.get("horizon_days", 5))
    band = float(prob.get("target", {}).get("vol_band_mult", 0.5))
    win = int(prob.get("target", {}).get("vol_window_weeks", 12))
    min_bars = int(sig.get("min_bars", 60))
    emp_band = float(prob.get("empirical_band", 0.25))
    min_samples = int(prob.get("empirical_min_samples", 20))
    w = float(prob.get("empirical_max_weight", 0.5))
    use_emp = prob.get("use_empirical", True)
    drift_win = int(prob.get("drift_window", 20))

    closes = [b.close for b in bars]
    if len(bars) < min_bars + horizon:
        return []
    comps = rolling_composites(bars, cfg, min_bars)                 # precompute once
    labels = targets.label_series(closes, horizon, win, band)       # PIT labels, once

    rows = []
    for i in range(min_bars, len(bars) - horizon, horizon):         # non-overlapping
        composite = comps[i]
        if composite is None or labels[i] is None:
            continue
        drift = probability.realized_drift(closes[:i + 1], drift_win)
        p = probability.logistic_probability(composite, prob, drift)
        if use_emp:
            hits = tot = 0
            for j in range(0, i - horizon + 1):                     # outcome known by time i
                cj, lj = comps[j], labels[j]
                if cj is None or lj is None or abs(cj - composite) > emp_band:
                    continue
                tot += 1
                hits += (lj == targets.UP)
            if tot >= min_samples:
                p_emp = (hits + 0.5) / (tot + 1.0)
                p = (1 - w) * p + w * p_emp
        p = min(0.99, max(0.01, p))
        rows.append({"asof": bars[i].date, "p_up": p, "realized_label": labels[i],
                     "predicted_label": targets.UP if p >= 0.5 else targets.FLAT})
    return rows


def backtest_universe(store, tickers: list[str], cfg: dict) -> list[dict]:
    rows: list[dict] = []
    for t in tickers:
        rows.extend(backtest_ticker(store.get_bars(t), cfg))
    return rows
