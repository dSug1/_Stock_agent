"""Probability-calibration metrics (pure) — spec §9.

Brier score, base rate, the long-only up-call hit-rate (precision of acting on 'up'), and a reliability
table. Used by both the forward ledger settle (`validation.py`) and the historical backtest (`backtest.py`).
A small sample is flagged `underpowered` (the 5_Hype lesson: n≥100 before trusting calibration).
"""

from __future__ import annotations

from typing import Optional, Sequence

UP = "up"


def brier(pairs: Sequence[tuple]) -> Optional[float]:
    """Mean (p_up − outcome)² over (p_up, outcome01) pairs — lower is better; 0.25 = always-0.5."""
    if not pairs:
        return None
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs)


def reliability(pairs: Sequence[tuple], n_bins: int = 5) -> list[dict]:
    """Per predicted-probability bin: mean predicted vs observed up-rate (calibration curve)."""
    bins: list[list] = [[] for _ in range(n_bins)]
    for p, o in pairs:
        bins[min(n_bins - 1, int(p * n_bins))].append((p, o))
    out = []
    for k, bk in enumerate(bins):
        if not bk:
            continue
        out.append({"bin": f"{k / n_bins:.1f}-{(k + 1) / n_bins:.1f}", "n": len(bk),
                    "mean_pred": round(sum(p for p, _ in bk) / len(bk), 3),
                    "obs_rate": round(sum(o for _, o in bk) / len(bk), 3)})
    return out


def summary(rows: Sequence[dict], min_n: int = 100, n_bins: int = 5) -> dict:
    """Aggregate metrics over rows carrying ``p_up``, ``predicted_label``, ``realized_label``."""
    if not rows:
        return {"n": 0}
    outcomes = [1 if r["realized_label"] == UP else 0 for r in rows]
    pairs = [(float(r["p_up"]), o) for r, o in zip(rows, outcomes)]
    n = len(rows)
    base = sum(outcomes) / n
    acted = [o for r, o in zip(rows, outcomes) if r["predicted_label"] == UP]
    hit = (sum(acted) / len(acted)) if acted else None
    return {
        "n": n,
        "brier": round(brier(pairs), 4),
        "base_rate": round(base, 4),
        "up_calls": len(acted),
        "up_call_hit_rate": round(hit, 4) if hit is not None else None,
        "beats_base_rate": (hit is not None and hit > base),
        "reliability": reliability(pairs, n_bins),
        "underpowered": n < min_n,
    }
