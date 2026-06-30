"""Forward-catalyst proximity — pure (spec Decision G: scheduled/upcoming dates only).

A *scheduled* catalyst (PDUFA decision, data-readout window, earnings date) is anticipatory — knowing one
lands inside the prediction week is a leading signal. The *result* of a catalyst is a-posteriori and out of
scope (§2.2). These functions only reason about forward dates; the dates themselves come from the catalyst
client + the `catalysts` table. ``asof`` is always passed in (never reads the wall clock).
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Sequence


def _parse(d: str) -> date:
    return date.fromisoformat(d)


def days_to_next(event_dates: Sequence[str], asof: str) -> Optional[int]:
    """Days from ``asof`` to the soonest event on/after ``asof``; ``None`` if none are forward."""
    a = _parse(asof)
    forward = [(_parse(d) - a).days for d in event_dates]
    forward = [x for x in forward if x >= 0]
    return min(forward) if forward else None


def proximity_score(days: Optional[int], cfg: dict) -> tuple[float, dict]:
    """Score a scheduled catalyst by how soon it lands: 1.0 at day 0, ramping to 0 at the horizon.

    A catalyst beyond the horizon (or none) scores 0 — it's not yet an anticipatory signal for *this*
    week. Returns ``(score, features)``.
    """
    if days is None:
        return 0.0, {"days_to_catalyst": None}
    horizon = int(cfg.get("catalyst_horizon_days", 21))
    if days < 0 or days > horizon:
        return 0.0, {"days_to_catalyst": days}
    score = max(0.0, 1.0 - days / horizon)
    return score, {"days_to_catalyst": days}
