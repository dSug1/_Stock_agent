"""Module 6b — post-scoring refinements + selective dispatch.

Part (a) — composite score modifier (D47): not yet implemented.
Part (b) — selective dispatch + mandatory gate (D48):
   - sidecar JSON I/O   → selection_io.py    (D49 — primary)
   - HTML legacy parser → selection.py       (D48 — back-compat fallback)

Spec: 2_Funds_parser/spec/module_6b_spec.md.
Decisions: 2_Funds_parser/spec/decisions.md § D47, D48, D49.
"""
from __future__ import annotations

from .selection import parse_selected_tickers, parse_all_tickers
from .selection_io import (
    SCHEMA_VERSION,
    load_selection_json,
    merge_selection,
    selection_json_path,
    stamp_update,
    write_selection_json,
)

__all__ = [
    "parse_selected_tickers",
    "parse_all_tickers",
    "SCHEMA_VERSION",
    "load_selection_json",
    "merge_selection",
    "selection_json_path",
    "stamp_update",
    "write_selection_json",
]
