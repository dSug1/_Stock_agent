"""Probability calibration (pure) — isotonic regression via Pool-Adjacent-Violators (spec §9, M6).

The backtest (M5) showed the raw `p_model` is overconfident — its reliability curve bends away from the
diagonal. A monotonic **isotonic** calibrator maps raw probabilities to the empirically-observed up-rate,
fixing that without assuming a shape. No sklearn dependency — PAV is ~15 lines. Stays the identity until
enough samples (don't calibrate on noise). This is the *static* seed of the operator's self-growing
feedback loop (roadmap v2): re-fit as the ledger fills and the loop accumulates.
"""

from __future__ import annotations

from typing import Optional, Sequence


def _pav(ys: Sequence[float]) -> list[float]:
    """Pool-Adjacent-Violators: nearest non-decreasing fit (unit weights) to ``ys`` in order."""
    blocks: list[list] = []                      # each: [value, weight]
    for y in ys:
        blocks.append([float(y), 1.0])
        while len(blocks) >= 2 and blocks[-2][0] > blocks[-1][0]:
            v2, w2 = blocks.pop()
            v1, w1 = blocks.pop()
            blocks.append([(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2])
    out: list[float] = []
    for v, w in blocks:
        out.extend([v] * int(w))
    return out


class Calibrator:
    """Step calibrator: ``breakpoints`` = sorted (x_threshold, y) with y non-decreasing. Empty = identity."""

    def __init__(self, breakpoints: Optional[list] = None):
        self.bp = breakpoints or []

    def apply(self, p: float) -> float:
        if not self.bp:
            return p                              # identity
        y = self.bp[0][1]
        for x, yy in self.bp:
            if x <= p:
                y = yy
            else:
                break
        return min(0.99, max(0.01, y))

    def to_json(self) -> dict:
        return {"breakpoints": self.bp}

    @classmethod
    def from_json(cls, d: Optional[dict]) -> "Calibrator":
        return cls((d or {}).get("breakpoints", []))


def identity() -> Calibrator:
    return Calibrator([])


def fit_isotonic(pairs: Sequence[tuple], min_n: int = 50) -> Calibrator:
    """Fit an isotonic calibrator from (p_raw, outcome01) pairs. Identity until ``min_n`` samples."""
    pairs = [(float(p), float(o)) for p, o in pairs]
    if len(pairs) < min_n:
        return identity()
    pairs.sort(key=lambda t: t[0])
    fitted = _pav([o for _, o in pairs])
    bp: list = []
    for (x, _), y in zip(pairs, fitted):
        if not bp or bp[-1][1] != y:
            bp.append((x, y))
    return Calibrator(bp)
