"""Module 7 — Outcome tracking + per-component calibration feedback loop.

Phase α (this build): snapshot hook + forward-price collection. NO reports.
Phase β (later, after 1q of α data): outcome classification + per-archetype/
                                     per-decile reports.
Phase γ (later, after 2-4q of α data): per-component calibration with
                                       co-firing-aware regression.

Spec: spec/module_7_spec.md
Decisions: spec/decisions.md § D53, D55
"""
from __future__ import annotations

from .outcomes_db import (
    init_outcomes_db,
    snapshot_count_for_run,
    delete_snapshots_for_run,
)
from .snapshot import snapshot_run
from .collect import collect_forward_prices, CollectResult

__all__ = [
    "init_outcomes_db",
    "snapshot_run",
    "snapshot_count_for_run",
    "delete_snapshots_for_run",
    "collect_forward_prices",
    "CollectResult",
]
