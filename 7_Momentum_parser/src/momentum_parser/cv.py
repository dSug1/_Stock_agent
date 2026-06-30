"""Walk-forward cross-validation of the calibrator (pure; spec §9, M9).

M6 fit isotonic and scored it on the SAME data (in-sample) — the Brier improvement could be overfitting.
This evaluates calibration **out-of-sample, time-ordered**: split the backtest decisions chronologically,
fit the calibrator on each expanding train window, apply it to the held-out next block, and aggregate the
out-of-sample Brier (raw vs calibrated). If calibrated OOS Brier < raw, the correction genuinely
generalizes; if not, it was overfitting. No look-ahead — a fold is only calibrated on decisions that
preceded it.
"""

from __future__ import annotations

from typing import Sequence

from .calibration import fit_isotonic
from .metrics import UP


def walk_forward(rows: Sequence[dict], n_folds: int = 5, min_cal: int = 50) -> dict:
    """Expanding-window walk-forward OOS evaluation over backtest ``rows`` ({asof, p_up, realized_label})."""
    rows = sorted((r for r in rows if r.get("realized_label") is not None),
                  key=lambda r: r.get("asof", ""))
    n = len(rows)
    if n < n_folds * 2:
        return {"n": n, "insufficient": True}

    fold = n // n_folds
    oos: list[tuple] = []                       # (raw_p, calibrated_p, outcome01)
    for k in range(1, n_folds):
        train = rows[: k * fold]
        test = rows[k * fold:] if k == n_folds - 1 else rows[k * fold: (k + 1) * fold]
        cal = fit_isotonic([(r["p_up"], 1.0 if r["realized_label"] == UP else 0.0) for r in train],
                           min_n=min_cal)
        for r in test:
            o = 1.0 if r["realized_label"] == UP else 0.0
            oos.append((r["p_up"], cal.apply(r["p_up"]), o))

    if not oos:
        return {"n": n, "insufficient": True}
    braw = sum((p - o) ** 2 for p, _, o in oos) / len(oos)
    bcal = sum((c - o) ** 2 for _, c, o in oos) / len(oos)
    return {"n": n, "n_oos": len(oos), "folds": n_folds,
            "brier_raw_oos": round(braw, 4), "brier_cal_oos": round(bcal, 4),
            "improvement": round(braw - bcal, 4), "generalizes": bcal < braw}
