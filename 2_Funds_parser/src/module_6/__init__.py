"""Module 6 — LLM scoring (Anthropic Claude API).

Spec: 2_Funds_parser/spec/module_6_spec.md (m6-v2).
Decisions: 2_Funds_parser/spec/decisions.md § D30–D43.
"""
from __future__ import annotations

from .cost_estimate import (
    EstimateInputs,
    EstimateOutputs,
    estimate_cost,
    render_cost_html,
)
from .dispatch import (
    DispatchResult,
    build_user_message_full,
    build_user_message_light_refresh,
    build_web_search_tool_def,
    dispatch_batch,
    dispatch_sync,
    load_allowed_domains_for_industry,
)
from .parsing import (
    ParseError,
    ParsedFullScore,
    ParsedLightRefresh,
    parse_full_score,
    parse_light_refresh,
)
from .priors import (
    format_prior_research_block,
    format_prior_thesis_block,
    query_prior_research,
    query_prior_thesis,
)
from .prompt import load_cacheable_prefix
from .reports import (
    render_final_ranking_html,
    render_final_ranking_xlsx,
    render_llm_responses_html,
)
from .scores_db import (
    LLM_SCORES_SCHEMA_VERSION,
    init_llm_scores_schema,
    query_priors_for_ticker,
)
from .scores_db_writes import (
    close_run,
    open_run,
    upsert_web_search_cache_row,
    write_error_row,
    write_final_rankings,
    write_full_score_rows,
)
from .scoring import (
    HorizonScore,
    TickerScore,
    compute_horizon_score,
    compute_ticker_score,
)
from .tier import Tier, TierClassification, classify_ticker_tier

__all__ = [
    # cost estimator
    "EstimateInputs", "EstimateOutputs", "estimate_cost", "render_cost_html",
    # dispatch
    "DispatchResult", "build_user_message_full", "build_user_message_light_refresh",
    "build_web_search_tool_def", "dispatch_batch", "dispatch_sync",
    "load_allowed_domains_for_industry",
    # parsing
    "ParseError", "ParsedFullScore", "ParsedLightRefresh",
    "parse_full_score", "parse_light_refresh",
    # priors
    "format_prior_research_block", "format_prior_thesis_block",
    "query_prior_research", "query_prior_thesis",
    # prompt
    "load_cacheable_prefix",
    # reports
    "render_final_ranking_html", "render_final_ranking_xlsx",
    "render_llm_responses_html",
    # scores_db
    "LLM_SCORES_SCHEMA_VERSION", "init_llm_scores_schema",
    "query_priors_for_ticker",
    # scores_db_writes
    "close_run", "open_run", "upsert_web_search_cache_row",
    "write_error_row", "write_final_rankings", "write_full_score_rows",
    # scoring
    "HorizonScore", "TickerScore",
    "compute_horizon_score", "compute_ticker_score",
    # tier
    "Tier", "TierClassification", "classify_ticker_tier",
]
