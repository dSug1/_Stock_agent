"""The kill-switch test (Protocol section 4) — pure stats, no I/O.

The whole program rests on one unproven claim: **narrative legibility x thematic heat drives the
re-rating, beyond cheap + catalyst.** Test it on the labeled panel:

    forward_return  ~  (Legibility x ThematicHeat)  +  controls(free_float, time_to_catalyst,
                                                                drawdown, sector, era)

If the narrative composite's coefficient is indistinguishable from 0 (or wrong sign) after
controls, the premise is false and the program collapses to a value+momentum screen -> STOP.

The pass condition is PRE-REGISTERED (config/panel.yaml): positive coefficient, t-stat >= threshold,
and n >= min_n. OLS is done with numpy (no statsmodels in the venv). Below min_n the result is
flagged underpowered and is NOT a verdict.
"""

import numpy as np


def ols(X: np.ndarray, y: np.ndarray):
    """Ordinary least squares. X already includes an intercept column. Returns (beta, se, t)."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    n, k = X.shape
    dof = max(n - k, 1)
    sigma2 = float(resid @ resid) / dof
    xtx_inv = np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.maximum(np.diag(sigma2 * xtx_inv), 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, beta / se, np.nan)
    return beta, se, t


def run(rows, *, narrative_key="narrative", control_keys=(), min_n=100, t_threshold=2.0,
        require_sign="positive"):
    """rows: list of dicts each with 'fwd_return', narrative_key, and the control_keys.

    Returns a verdict dict. `passed` is only meaningful when not `underpowered`.
    """
    usable = [r for r in rows
              if r.get("fwd_return") is not None and r.get(narrative_key) is not None
              and all(r.get(c) is not None for c in control_keys)]
    n = len(usable)
    if n == 0:
        return {"n": 0, "underpowered": True, "passed": False,
                "reason": "no rows with fwd_return + narrative feature"}

    y = np.array([float(r["fwd_return"]) for r in usable])
    cols = [np.ones(n), np.array([float(r[narrative_key]) for r in usable])]
    for c in control_keys:
        cols.append(np.array([float(r[c]) for r in usable]))
    X = np.column_stack(cols)

    beta, se, t = ols(X, y)
    b_nar, se_nar, t_nar = float(beta[1]), float(se[1]), float(t[1])
    sign_ok = (b_nar > 0) if require_sign == "positive" else (b_nar < 0)
    underpowered = n < min_n
    passed = bool(sign_ok and abs(t_nar) >= t_threshold and not underpowered)

    return {
        "n": n,
        "narrative_coef": b_nar,
        "narrative_se": se_nar,
        "narrative_t": t_nar,
        "controls": list(control_keys),
        "min_n": min_n,
        "t_threshold": t_threshold,
        "sign_ok": sign_ok,
        "underpowered": underpowered,
        "passed": passed,
        "reason": ("underpowered: n=%d < min_n=%d (Protocol 2.3)" % (n, min_n)
                   if underpowered else
                   ("PASS: narrative coef positive and significant" if passed else
                    "FAIL: narrative coef not positive-significant after controls")),
    }
