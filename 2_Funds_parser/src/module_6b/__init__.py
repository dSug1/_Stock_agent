"""Module 6b — post-scoring refinements + selective dispatch + composite modifier.

Part (a) — composite score modifier (D47, D50): see modifiers.py + apply.py.
Part (b) — selective dispatch + mandatory gate (D48):
   - sidecar JSON I/O   → selection_io.py    (D49 — primary; v2 schema adds modifier_weights for D50)
   - HTML legacy parser → selection.py       (D48 — back-compat fallback)

Spec: 2_Funds_parser/spec/module_6b_spec.md.
Decisions: 2_Funds_parser/spec/decisions.md § D47, D48, D49, D50.
"""
from __future__ import annotations

from .apply import (
    apply_modifiers_to_run,
    apply_modifiers_to_runs,
    collect_ticker_factors,
    compute_for_row,
)
from .modifiers import (
    COMPONENT_NAMES,
    apply_weights,
    compute_modifier,
    load_modifier_config,
    parse_iso_date,
)
from .selection import parse_all_tickers, parse_selected_tickers
from .selection_io import (
    DEFAULT_WEIGHT,
    SCHEMA_VERSION,
    default_modifier_weights,
    load_selection_json,
    merge_modifier_weights,
    merge_selection,
    read_modifier_weights,
    selection_json_path,
    stamp_update,
    write_selection_json,
)

__all__ = [
    # selection (D48 / D49)
    "parse_selected_tickers",
    "parse_all_tickers",
    "SCHEMA_VERSION",
    "load_selection_json",
    "merge_selection",
    "selection_json_path",
    "stamp_update",
    "write_selection_json",
    # modifiers (D47)
    "COMPONENT_NAMES",
    "apply_weights",
    "compute_modifier",
    "load_modifier_config",
    "parse_iso_date",
    # apply (D47)
    "apply_modifiers_to_run",
    "apply_modifiers_to_runs",
    "collect_ticker_factors",
    "compute_for_row",
    # modifier weight overrides (D50)
    "DEFAULT_WEIGHT",
    "default_modifier_weights",
    "merge_modifier_weights",
    "read_modifier_weights",
]
