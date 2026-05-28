"""Module 7 — Claude API deep-dive (per-catalyst).

This package owns the pure-compute layers (math, parsing, cost estimate,
DB schema). LLM-side wiring (context_pack, dispatch, system prompt,
renderer integration, selection HTTP server) lands in a follow-up.

Spec: spec/module_7_spec.md.
Decisions: spec/decisions.md § D16.
"""
from __future__ import annotations

from .cache import (
    CacheLookup,
    compute_catalyst_signature,
    lookup_cache,
    partition_feed_by_cache,
)
from .config import (
    Module7Config,
    default_config_path,
    load_module_7_config,
)
from .cost_estimate import (
    EstimateInputs,
    EstimateOutputs,
    ScenarioCost,
    estimate_cost,
    render_cost_html,
)
from .deep_dives_db import (
    LLM_SCORES_SCHEMA_VERSION,
    close_run,
    db_connect,
    init_deep_dives_db,
    latest_deep_dive_per_catalyst,
    open_run,
    update_run_batch_id,
    upsert_deep_dive_row,
    upsert_web_search_cache_row,
    write_error_row,
)
from .parsing import (
    ParseError,
    ParsedDeepDive,
    extract_json_block,
    parse_deep_dive,
)
from .prompt import load_cacheable_prefix
from .scoring import (
    ExpectancyResult,
    compute_expectancy,
    remap_signal_to_modifier,
    weeks_between,
)

__all__ = [
    # cache
    "CacheLookup", "compute_catalyst_signature",
    "lookup_cache", "partition_feed_by_cache",
    # config
    "Module7Config", "load_module_7_config", "default_config_path",
    # cost estimator
    "EstimateInputs", "EstimateOutputs", "ScenarioCost",
    "estimate_cost", "render_cost_html",
    # deep_dives DB
    "LLM_SCORES_SCHEMA_VERSION", "init_deep_dives_db", "db_connect",
    "open_run", "close_run", "update_run_batch_id",
    "upsert_deep_dive_row", "upsert_web_search_cache_row", "write_error_row",
    "latest_deep_dive_per_catalyst",
    # parsing
    "ParseError", "ParsedDeepDive", "extract_json_block", "parse_deep_dive",
    # prompt
    "load_cacheable_prefix",
    # scoring
    "ExpectancyResult", "compute_expectancy",
    "remap_signal_to_modifier", "weeks_between",
]
