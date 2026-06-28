"""Composite score + confidence (spec §10) — computed in CODE, not trusted from the model.

The model returns per-axis scores and the two adversarial calls (moat / substance); WE compute the
weighted composite and apply the penalties, so the ranking is deterministic and auditable. The
model's own "composite" field, if any, is advisory only.
"""

from __future__ import annotations

from typing import Any

from .rubric import AXES


def compute_composite(rubric: dict, weights: dict[str, float], penalties: dict[str, float]) -> float:
    """Weighted, normalized composite in [0,1] with moat/substance penalties (spec §10).

    composite = Σ wᵢ·(scoreᵢ/5) / Σ wᵢ over A–E, then:
      * ×architecture_moat_factor if the moat is a commoditizable architecture (KaiSR lesson)
      * ×marketing_verdict_factor  if substance_check says "marketing"
    """
    num = den = 0.0
    for axis in AXES:
        w = float(weights.get(axis, 0.0))
        score = max(0, min(5, (rubric.get(axis) or {}).get("score", 0)))
        num += w * (score / 5.0)
        den += w
    composite = num / den if den else 0.0

    if (rubric.get("moat_location") or {}).get("data_vs_architecture") == "architecture":
        composite *= float(penalties.get("architecture_moat_factor", 0.75))
    if (rubric.get("substance_check") or {}).get("verdict") == "marketing":
        composite *= float(penalties.get("marketing_verdict_factor", 0.50))
    return round(composite, 4)


def compute_confidence(rubric: dict, *, evidence_sources: int, finalized: bool) -> float:
    """Confidence in [0,1] from evidence completeness + tier (spec §10).

    More harvested sources → higher; an Opus-finalized score → higher; a 'mixed' substance verdict
    lowers it. Surfaced so low-confidence shortlist entries can be flagged distinctly.
    """
    base = 0.3 + 0.2 * min(3, evidence_sources)        # 0 sources → 0.3, 3+ → 0.9
    if finalized:
        base += 0.1
    if (rubric.get("substance_check") or {}).get("verdict") == "mixed":
        base -= 0.15
    return round(max(0.0, min(1.0, base)), 3)


def axis_scores(rubric: dict) -> dict[str, Any]:
    """Pull the five 0-5 axis scores out for persistence."""
    return {axis: (rubric.get(axis) or {}).get("score") for axis in AXES}
