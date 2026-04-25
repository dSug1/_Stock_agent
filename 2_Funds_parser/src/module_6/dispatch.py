"""Anthropic dispatch — sync mode (parallel async) and batch mode.

Two entry points:
    dispatch_sync(...)   — bounded async fan-out via AsyncAnthropic.
                            Returns list of DispatchResult.
                            Best for calibration / 3-ticker tests.
    dispatch_batch(...)  — Message Batches API submission + polling + decode.
                            Returns same list shape as sync.
                            Best for production runs (50% discount, 24h SLA).

Both share the same prompt-building path (build_user_message + cacheable
system prefix + web_search tool definition with per-ticker allowed_domains).

This module makes API calls when invoked, but importing it is safe
(no side effects). Tests can use ``build_user_message`` directly without
touching the network.

Spec: spec/module_6_spec.md § Dispatch + § Cross-run caches.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import yaml

from .priors import (
    format_prior_research_block,
    format_prior_thesis_block,
    query_prior_research,
    query_prior_thesis,
)

_LOG = logging.getLogger(__name__)

# Tool versions — pinned. Anthropic increments these versions occasionally.
_WEB_SEARCH_TOOL_TYPE = "web_search_20250305"


# ---------------------------------------------------------------------------
# Per-ticker dispatch result
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    ticker: str
    success: bool
    raw_text: str = ""
    response_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    web_search_calls: int = 0
    stop_reason: str = ""                                          # 'end_turn' | 'max_tokens' | etc.
    server_tool_results: list[dict] = field(default_factory=list)  # parsed search results
    error_kind: str = ""
    error_detail: str = ""


# ---------------------------------------------------------------------------
# Whitelist + tool definition
# ---------------------------------------------------------------------------


def load_allowed_domains_for_industry(
    whitelists_path: str,
    industry: str,
) -> list[str]:
    """Build per-ticker allowed_domains = universal + research + by_industry[industry]."""
    data = yaml.safe_load(open(whitelists_path, "r", encoding="utf-8").read())
    universal = list(data.get("universal", []) or [])
    research = list(data.get("research", []) or [])
    industry_tier = list((data.get("by_industry", {}) or {}).get(industry, []) or [])
    # De-dupe while preserving order.
    seen, out = set(), []
    for d in universal + research + industry_tier:
        if d and d not in seen:
            out.append(d); seen.add(d)
    return out


def build_web_search_tool_def(allowed_domains: list[str], max_uses: int) -> dict:
    """Return the tool definition block for Anthropic's server-side web_search."""
    tool: dict = {
        "type": _WEB_SEARCH_TOOL_TYPE,
        "name": "web_search",
        "max_uses": int(max_uses),
    }
    if allowed_domains:
        tool["allowed_domains"] = allowed_domains
    return tool


# ---------------------------------------------------------------------------
# User-message construction (Tier C full + Tier B refresh)
# ---------------------------------------------------------------------------


def _strip_fund_accumulation(pack: dict) -> dict:
    """Remove the fund_accumulation section from the pack (HARD RULE #2 / D40)."""
    return {k: v for k, v in pack.items() if k != "fund_accumulation"}


def build_user_message_full(
    pack: dict,
    *,
    scores_conn: sqlite3.Connection,
    ticker: str,
    prompt_version: str,
    model: str,
    web_search_lookback_days: int,
    prior_thesis_max_per_horizon: int,
) -> str:
    """Build the per-ticker user message for a Tier C full-scoring call.

    Structure:
        # Context pack  <pack JSON, fund_accumulation stripped>
        ## Prior research  (if any cached web_search hits in lookback window)
        ## Prior thesis    (if any prior llm_scores rows for this ticker)
        Closing instruction.
    """
    import json as _json
    stripped = _strip_fund_accumulation(pack)
    pack_text = _json.dumps(stripped, indent=2, ensure_ascii=False, default=str)

    body = ["# Context pack", "", pack_text]

    prior_research = query_prior_research(scores_conn, ticker, web_search_lookback_days)
    pr_block = format_prior_research_block(prior_research)
    if pr_block:
        body.append(pr_block)

    prior_thesis = query_prior_thesis(
        scores_conn, ticker, prompt_version, model, prior_thesis_max_per_horizon,
    )
    pt_block = format_prior_thesis_block(prior_thesis)
    if pt_block:
        body.append(pt_block)

    body.append("")
    body.append("Score both horizons per the framework. Return one ```json``` block.")
    return "\n".join(body)


def build_user_message_light_refresh(
    pack: dict,
    prior_thesis: dict[str, list[dict]],
    *,
    ticker: str,
) -> str:
    """Tier B light-refresh user message — small prompt, prior thesis is mandatory.

    Caller pre-fetches the prior thesis since the tier classifier already had it.
    """
    import json as _json
    stripped = _strip_fund_accumulation(pack)

    # Condensed pack — only the deltas the model needs to see for "did anything change?"
    snapshot_keys = ("identity", "market_snapshot", "archetype_verdict",
                     "price_trajectory")
    condensed = {k: stripped[k] for k in snapshot_keys if k in stripped}
    body = ["# Context pack snapshot (key fields only)", "",
            _json.dumps(condensed, indent=2, ensure_ascii=False, default=str)]
    body.append(format_prior_thesis_block(prior_thesis))
    body.append("")
    body.append(
        "Has anything material changed since your prior thesis above that would "
        "revise either appreciation by > 5pp OR shift catalyst timing by > 2 weeks? "
        "Return one ```json``` block with shape: "
        "{ticker, material_change: bool, reason: string, "
        "updated_thesis_if_changed: {full m6-v2 schema} | null}."
    )
    return "\n".join(body)


# ---------------------------------------------------------------------------
# Anthropic call helpers (sync mode)
# ---------------------------------------------------------------------------


def _extract_text_and_tool_uses(message: Any) -> tuple[str, list[dict]]:
    """Walk the message.content blocks and return (text_concatenated, tool_results).

    Anthropic's response.content is a list of blocks: text, server_tool_use,
    web_search_tool_result, etc. We concatenate text blocks into raw_text and
    extract structured tool results for the web_search_cache.
    """
    text_parts: list[str] = []
    tool_results: list[dict] = []
    for block in (message.content or []):
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", ""))
        elif btype == "web_search_tool_result":
            content = getattr(block, "content", None) or []
            for item in content:
                tool_results.append({
                    "type": getattr(item, "type", None),
                    "url": getattr(item, "url", None),
                    "title": getattr(item, "title", None),
                    "page_age": getattr(item, "page_age", None),
                    "encrypted_content": getattr(item, "encrypted_content", None),
                    # the actual text snippet lives under `content` per current SDK
                    "text": getattr(item, "content", None) if hasattr(item, "content") else None,
                })
    return "".join(text_parts), tool_results


def _extract_search_query_from_tool_use(message: Any) -> dict[str, str]:
    """Map URL→query by walking adjacent server_tool_use + web_search_tool_result blocks."""
    out: dict[str, str] = {}
    blocks = list(message.content or [])
    last_query = ""
    for block in blocks:
        btype = getattr(block, "type", None)
        if btype == "server_tool_use":
            tool_input = getattr(block, "input", None) or {}
            last_query = (tool_input.get("query") if isinstance(tool_input, dict) else "") or ""
        elif btype == "web_search_tool_result":
            content = getattr(block, "content", None) or []
            for item in content:
                url = getattr(item, "url", None)
                if url:
                    out[url] = last_query
    return out


# ---------------------------------------------------------------------------
# Sync dispatch (AsyncAnthropic with bounded concurrency)
# ---------------------------------------------------------------------------


async def _dispatch_one_async(
    client,
    *,
    ticker: str,
    system_prompt: str,
    user_message: str,
    model: str,
    max_output_tokens: int,
    web_search_tool: dict,
    semaphore: asyncio.Semaphore,
) -> DispatchResult:
    async with semaphore:
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=max_output_tokens,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_message}],
                tools=[web_search_tool],
            )
        except Exception as e:                                                  # pragma: no cover
            return DispatchResult(
                ticker=ticker, success=False,
                error_kind="api_error", error_detail=f"{type(e).__name__}: {e}",
            )

        text, tool_results = _extract_text_and_tool_uses(resp)
        url_to_query = _extract_search_query_from_tool_use(resp)
        # Annotate tool_results with their query for cache writeback
        for r in tool_results:
            r["query"] = url_to_query.get(r.get("url"), "")
        usage = getattr(resp, "usage", None)
        ws_calls = sum(
            1 for b in (resp.content or [])
            if getattr(b, "type", None) == "server_tool_use"
            and getattr(b, "name", None) == "web_search"
        )
        return DispatchResult(
            ticker=ticker, success=True, raw_text=text,
            response_id=getattr(resp, "id", ""),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            web_search_calls=ws_calls,
            stop_reason=getattr(resp, "stop_reason", "") or "",
            server_tool_results=tool_results,
        )


def dispatch_sync(
    *,
    api_key: str,
    model: str,
    max_output_tokens: int,
    system_prompt: str,
    per_ticker: list[dict],   # each dict: {ticker, user_message, web_search_tool}
    sync_concurrency: int,
) -> list[DispatchResult]:
    """Run all per-ticker calls via AsyncAnthropic with bounded concurrency."""
    from anthropic import AsyncAnthropic

    async def _run() -> list[DispatchResult]:
        client = AsyncAnthropic(api_key=api_key)
        sem = asyncio.Semaphore(sync_concurrency)
        tasks = [
            _dispatch_one_async(
                client,
                ticker=p["ticker"],
                system_prompt=system_prompt,
                user_message=p["user_message"],
                model=model,
                max_output_tokens=max_output_tokens,
                web_search_tool=p["web_search_tool"],
                semaphore=sem,
            )
            for p in per_ticker
        ]
        return await asyncio.gather(*tasks)

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Batch dispatch
# ---------------------------------------------------------------------------


def submit_batch(
    *,
    api_key: str,
    model: str,
    max_output_tokens: int,
    system_prompt: str,
    per_ticker: list[dict],
) -> str:
    """Submit a Message Batch and return the ``batch_id`` *immediately*.

    Does NOT poll — caller is responsible for `poll_and_collect_batch`.
    Splitting submit + poll lets the script persist the batch_id to the
    DB right after submission so it remains recoverable if the script
    crashes mid-poll (D51 — batch resume).
    """
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    requests = []
    for p in per_ticker:
        requests.append({
            "custom_id": p["ticker"],
            "params": {
                "model": model,
                "max_tokens": max_output_tokens,
                "system": [{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                "messages": [{"role": "user", "content": p["user_message"]}],
                "tools": [p["web_search_tool"]],
            },
        })

    batch = client.messages.batches.create(requests=requests)
    _LOG.info("Submitted batch %s with %d requests", batch.id, len(requests))
    return batch.id


def poll_and_collect_batch(
    *,
    api_key: str,
    batch_id: str,
    poll_interval_s: int = 30,
    timeout_s: int = 86_400,
) -> list[DispatchResult]:
    """Poll a previously-submitted batch and decode results when complete.

    Idempotent: safe to call multiple times against the same `batch_id`
    (Anthropic stores results for ~29 days). Used both by the normal
    batch flow AND by `scripts/6_score.py --resume-run N` to recover
    from a crashed mid-poll session.
    """
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    started = time.time()
    while True:
        elapsed = time.time() - started
        if elapsed > timeout_s:
            raise TimeoutError(f"Batch {batch_id} exceeded {timeout_s}s timeout")
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        _LOG.info("Batch %s status=%s elapsed=%.0fs", batch_id, b.processing_status, elapsed)
        time.sleep(poll_interval_s)

    # Decode results
    results: list[DispatchResult] = []
    for entry in client.messages.batches.results(batch_id):
        ticker = entry.custom_id
        if entry.result.type != "succeeded":
            results.append(DispatchResult(
                ticker=ticker, success=False,
                error_kind="api_error",
                error_detail=f"batch result type={entry.result.type}",
            ))
            continue
        msg = entry.result.message
        text, tool_results = _extract_text_and_tool_uses(msg)
        url_to_query = _extract_search_query_from_tool_use(msg)
        for r in tool_results:
            r["query"] = url_to_query.get(r.get("url"), "")
        usage = getattr(msg, "usage", None)
        ws_calls = sum(
            1 for blk in (msg.content or [])
            if getattr(blk, "type", None) == "server_tool_use"
            and getattr(blk, "name", None) == "web_search"
        )
        results.append(DispatchResult(
            ticker=ticker, success=True, raw_text=text,
            response_id=getattr(msg, "id", ""),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            web_search_calls=ws_calls,
            stop_reason=getattr(msg, "stop_reason", "") or "",
            server_tool_results=tool_results,
        ))
    return results


def dispatch_batch(
    *,
    api_key: str,
    model: str,
    max_output_tokens: int,
    system_prompt: str,
    per_ticker: list[dict],
    poll_interval_s: int = 30,
    timeout_s: int = 86_400,
    on_submit=None,
) -> tuple[str, list[DispatchResult]]:
    """Submit + poll a Message Batch end-to-end. Back-compat wrapper.

    `on_submit(batch_id)` fires immediately after submission so the
    caller can persist `batch_id` to the DB before the long poll starts
    — important for crash recovery (D51).

    Returns ``(batch_id, results)``.
    """
    batch_id = submit_batch(
        api_key=api_key, model=model,
        max_output_tokens=max_output_tokens,
        system_prompt=system_prompt, per_ticker=per_ticker,
    )
    if on_submit is not None:
        try:
            on_submit(batch_id)
        except Exception as e:                                # never let the cb kill the batch
            _LOG.warning("on_submit callback raised %s; continuing to poll", e)
    results = poll_and_collect_batch(
        api_key=api_key, batch_id=batch_id,
        poll_interval_s=poll_interval_s, timeout_s=timeout_s,
    )
    return batch_id, results
