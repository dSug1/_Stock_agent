"""Module 7 — Anthropic dispatch (sync + batch).

Copy-adapted from `2_Funds_parser/src/module_6/dispatch.py`. Two entry
points:

    dispatch_sync(...)   — bounded async fan-out via AsyncAnthropic.
                            Best for 1-3 ticker calibration runs.
    dispatch_batch(...)  — Message Batches API submission + polling.
                            50% discount, 24h SLA. Default for production.

Both share the same prompt-building path (cacheable system prefix with
the ephemeral `cache_control` breakpoint + per-ticker user message +
web_search tool definition).

Importing this module is safe — no network side effects at import time.

Spec: spec/module_7_spec.md §5.8.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

_LOG = logging.getLogger(__name__)

# Tool versions — pinned. Anthropic increments these versions occasionally.
_WEB_SEARCH_TOOL_TYPE = "web_search_20250305"


# ─────────────────────────── DispatchResult ────────────────────────


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


# ───────────────────── web_search tool definition ──────────────────


def load_allowed_domains(domains_path: str) -> list[str]:
    """Load the flat YAML domain list shipped with M7.

    Unlike 2_Funds_parser's industry-keyed routing, M7's universe is all
    biotech, so the whitelist collapses to one ordered list.
    """
    import yaml
    data = yaml.safe_load(open(domains_path, "r", encoding="utf-8").read()) or {}
    domains = list(data.get("domains", []) or [])
    seen, out = set(), []
    for d in domains:
        if d and d not in seen:
            out.append(d); seen.add(d)
    return out


def build_web_search_tool_def(allowed_domains: list[str], max_uses: int) -> dict:
    tool: dict = {
        "type": _WEB_SEARCH_TOOL_TYPE,
        "name": "web_search",
        "max_uses": int(max_uses),
    }
    if allowed_domains:
        tool["allowed_domains"] = allowed_domains
    return tool


# ───────────────────────── user-message body ───────────────────────


def build_user_message(pack: dict) -> str:
    """Render the per-ticker user message: pack JSON + closing instruction.

    Order matches the cacheable prefix's "INPUT STRUCTURE" section so the
    model sees fields in the same order across every call.
    """
    pack_text = json.dumps(pack, indent=2, ensure_ascii=False, default=str)
    return (
        "# Context pack\n\n"
        + pack_text
        + "\n\n"
        "Score this catalyst per the framework. Return one ```json``` "
        "block matching the m7-v1 schema. No prose outside the fence."
    )


# ──────────────────── content extraction helpers ───────────────────


def _extract_text_and_tool_uses(message: Any) -> tuple[str, list[dict]]:
    """Walk message.content blocks → (text_concatenated, [tool_results])."""
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
                    "text": getattr(item, "content", None) if hasattr(item, "content") else None,
                })
    return "".join(text_parts), tool_results


def _extract_search_query_from_tool_use(message: Any) -> dict[str, str]:
    """Map URL → query by walking adjacent server_tool_use blocks."""
    out: dict[str, str] = {}
    last_query = ""
    for block in (message.content or []):
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


def _count_web_search_calls(message: Any) -> int:
    return sum(
        1 for b in (message.content or [])
        if getattr(b, "type", None) == "server_tool_use"
        and getattr(b, "name", None) == "web_search"
    )


def _result_from_message(ticker: str, msg: Any) -> DispatchResult:
    text, tool_results = _extract_text_and_tool_uses(msg)
    url_to_query = _extract_search_query_from_tool_use(msg)
    for r in tool_results:
        r["query"] = url_to_query.get(r.get("url"), "")
    usage = getattr(msg, "usage", None)
    return DispatchResult(
        ticker=ticker, success=True, raw_text=text,
        response_id=getattr(msg, "id", ""),
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        web_search_calls=_count_web_search_calls(msg),
        stop_reason=getattr(msg, "stop_reason", "") or "",
        server_tool_results=tool_results,
    )


# ───────────────────────── sync dispatch ───────────────────────────


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
        except Exception as e:                                       # noqa: BLE001
            return DispatchResult(
                ticker=ticker, success=False,
                error_kind="api_error",
                error_detail=f"{type(e).__name__}: {e}",
            )
        return _result_from_message(ticker, resp)


def dispatch_sync(
    *,
    api_key: str,
    model: str,
    max_output_tokens: int,
    system_prompt: str,
    per_ticker: list[dict],
    sync_concurrency: int,
) -> list[DispatchResult]:
    """Run all per-ticker calls via AsyncAnthropic with bounded concurrency.

    Each entry in ``per_ticker`` must carry:
        ticker, user_message, web_search_tool
    """
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


# ───────────────────────── batch dispatch ──────────────────────────


def submit_batch(
    *,
    api_key: str,
    model: str,
    max_output_tokens: int,
    system_prompt: str,
    per_ticker: list[dict],
) -> str:
    """Submit a Message Batch + return batch_id IMMEDIATELY (no poll).

    Splitting submit + poll lets the caller persist `batch_id` to the
    DB right after submission, so a script crash mid-poll doesn't orphan
    the batch on Anthropic's side (D51 pattern from 2_Funds_parser).
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
    _LOG.info("submitted batch %s with %d requests", batch.id, len(requests))
    return batch.id


def poll_and_collect_batch(
    *,
    api_key: str,
    batch_id: str,
    poll_interval_s: int = 30,
    timeout_s: int = 86_400,
) -> list[DispatchResult]:
    """Poll a previously-submitted batch + decode results when complete.

    Idempotent: safe to call repeatedly against the same `batch_id`
    (Anthropic stores results ~29 days). Used by the normal batch flow
    AND by `--resume-run N` to recover from a crashed mid-poll session.
    """
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    started = time.time()
    while True:
        elapsed = time.time() - started
        if elapsed > timeout_s:
            raise TimeoutError(f"batch {batch_id} exceeded {timeout_s}s timeout")
        b = client.messages.batches.retrieve(batch_id)
        if b.processing_status == "ended":
            break
        _LOG.info("batch %s status=%s elapsed=%.0fs",
                  batch_id, b.processing_status, elapsed)
        time.sleep(poll_interval_s)

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
        results.append(_result_from_message(ticker, entry.result.message))
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
    """Submit + poll a Message Batch end-to-end.

    ``on_submit(batch_id)`` fires immediately after submission so the
    caller can persist `batch_id` to the DB before the long poll starts.
    """
    batch_id = submit_batch(
        api_key=api_key, model=model,
        max_output_tokens=max_output_tokens,
        system_prompt=system_prompt, per_ticker=per_ticker,
    )
    if on_submit is not None:
        try:
            on_submit(batch_id)
        except Exception as e:                              # never let a callback kill the batch
            _LOG.warning("on_submit callback raised %s; continuing to poll", e)
    results = poll_and_collect_batch(
        api_key=api_key, batch_id=batch_id,
        poll_interval_s=poll_interval_s, timeout_s=timeout_s,
    )
    return batch_id, results
