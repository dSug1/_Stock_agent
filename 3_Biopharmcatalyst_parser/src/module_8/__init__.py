"""Module 8 — Catalyst rescue + re-dispatch.

Pulls back excluded catalysts that meet user-defined rescue criteria
(small-cap H1, undefined/imminent H3, non-standard H5) and runs Claude
deep-dive on them. For B/C rescues, Claude is asked to *first* resolve
the catalyst date from primary sources, then score.

Reuses M7's pure-compute layers (cache, cost_estimate, dispatch,
parsing, scoring, deep_dives_db, live_price) and adds:
  * rescue_filter — classify candidates into A / B / C / combos
  * config         — Module8Config (narrower than M7's; reuses M7 modifiers/clamps)
  * prompt         — load_rescue_prefix (M7 system prompt + rescue date-retrieval prefix + few-shots)
  * (context_pack)  fetch_rescue_candidates — lives in module_7.context_pack alongside fetch_hard_pass_candidates

Spec: spec/module_8_spec.md
Decisions: spec/decisions.md § D35
"""
from __future__ import annotations

from .config import Module8Config, default_config_path, load_module_8_config
from .prompt import load_rescue_prefix
from .rescue_filter import (
    RescueClass,
    RescueDecision,
    classify_catalyst,
    classify_rows,
)

__all__ = [
    "Module8Config", "default_config_path", "load_module_8_config",
    "load_rescue_prefix",
    "RescueClass", "RescueDecision", "classify_catalyst", "classify_rows",
]
