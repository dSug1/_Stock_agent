"""Module 5 — Market Data Enrichment (pre-LLM context packs).

Reads Module 4b's dual-horizon ranked candidates and builds one structured
context pack per ticker into `context_packs.db`. Deterministic, offline
(no yfinance, no network). Output feeds Module 6's LLM scoring.

See:
- spec/module_5_spec.md — full specification (D21, D23–D29)
- spec/decisions.md § Module 5 — implementation decisions
"""
from .enrichment import load_enrichment_config, run_enrichment
from .packs import (
    build_context_pack,
    compute_source_rank_hash,
    fetch_prices_extremes,
    load_narrative_templates,
)
from .packs_db import (
    count_packs,
    init_packs_db,
    probe_pack,
    query_packs_for_quarter,
    upsert_pack,
)
from .reports import generate_enrichment_report_html
from .selection import apply_selection

__all__ = [
    "apply_selection",
    "build_context_pack",
    "compute_source_rank_hash",
    "count_packs",
    "fetch_prices_extremes",
    "generate_enrichment_report_html",
    "init_packs_db",
    "load_enrichment_config",
    "load_narrative_templates",
    "probe_pack",
    "query_packs_for_quarter",
    "run_enrichment",
    "upsert_pack",
]
