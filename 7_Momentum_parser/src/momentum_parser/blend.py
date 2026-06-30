"""Hybrid blend + confidence (spec Decision A, H, J) — pure.

`p_final = w·p_claude + (1−w)·p_model`; when the two legs diverge beyond a threshold the prediction is
flagged for review and its confidence is docked. Calibration of each leg (isotonic/Platt) plugs in via the
optional `calibrate` hook — the identity default is used until the §9 backtest fits it, so blended
probabilities are **INDICATIVE** until then.
"""

from __future__ import annotations

from typing import Callable, Optional


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def blend(p_claude: Optional[float], p_model: float, w: float, disagree_threshold: float,
          calibrate: Optional[Callable[[float, str], float]] = None) -> tuple[float, Optional[float], bool]:
    """Return ``(p_final, disagreement, review)``.

    With no Claude leg (`p_claude is None`) the model stands alone (disagreement ``None``, no review flag).
    """
    cal = calibrate or (lambda p, leg: p)
    pm = cal(p_model, "model")
    if p_claude is None:
        return min(0.99, max(0.01, pm)), None, False
    pc = cal(p_claude, "claude")
    p_final = w * pc + (1 - w) * pm
    d = abs(pc - pm)
    return min(0.99, max(0.01, p_final)), d, bool(d > disagree_threshold)


def confidence(conviction: float, coverage: float, disagreement: Optional[float],
               history_factor: float) -> float:
    """conviction × data_coverage × (1 − disagreement) × history_depth, clamped [0,1] (Decision J).

    ``disagreement`` ``None`` (model-only) is treated as 0 but the caller typically passes a lower
    ``conviction`` in that case.
    """
    d = disagreement if disagreement is not None else 0.0
    return _clip01(conviction * coverage * (1.0 - d) * history_factor)
