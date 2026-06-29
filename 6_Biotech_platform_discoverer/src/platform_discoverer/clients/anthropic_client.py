"""Anthropic API wrapper for the tiered scorer (spec §9.1, §14).

Thin layer over the official ``anthropic`` SDK: structured-output scoring calls (real-time and Batch),
prompt caching of the shared rubric system prompt, and running USD cost tracking against a hard
``max_usd`` ceiling. Model ids and the tiering come from config; pricing is API fact and lives here.

Calls are made ONLY on an explicit dispatch (the CLI gates with a [y/N] + cost estimate, repo
convention). The key is read from ``ANTHROPIC_API_KEY`` (loaded from repo-root ``.env`` by
``clients._net``); never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from . import _net  # noqa: F401  — imports trigger .env load so ANTHROPIC_API_KEY is present

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output). Batch API halves both. Cache reads ~0.1× input; writes ~1.25×.
PRICING = {
    "claude-haiku-4-5":  {"in": 1.0, "out": 5.0},
    "claude-sonnet-4-6": {"in": 3.0, "out": 15.0},
    "claude-opus-4-8":   {"in": 5.0, "out": 25.0},
}


def _price(model: str) -> dict:
    # tolerate dated ids (e.g. claude-haiku-4-5-20251001) by prefix match
    for k, v in PRICING.items():
        if model.startswith(k):
            return v
    return {"in": 5.0, "out": 25.0}


class BudgetExceeded(RuntimeError):
    """Raised when a call would push spend past ``max_usd`` (spec §14 hard stop)."""


def web_search_tool(max_uses: int = 5, allowed_domains: Optional[list] = None) -> dict:
    """The web_search server tool (dynamic-filtering variant; Sonnet 4.6 / Opus 4.8). Bounding
    ``max_uses`` keeps the server-side tool loop short so it finishes within one call (no pause_turn).
    ``allowed_domains`` (optional) restricts searches to a curated whitelist (focus + cost), mirroring
    3_Biopharmcatalyst_parser's dispatch."""
    tool: dict = {"type": "web_search_20260209", "name": "web_search", "max_uses": int(max_uses)}
    if allowed_domains:
        tool["allowed_domains"] = list(allowed_domains)
    return tool


class AnthropicClient:
    def __init__(self, *, max_usd: float = 50.0, web_search_cost_per_search: float = 0.01,
                 request_timeout_s: float = 180.0, max_retries: int = 1):
        self.max_usd = float(max_usd)
        self.spent_usd = 0.0
        self.calls = 0
        self.web_searches = 0
        self.web_search_cost_per_search = float(web_search_cost_per_search)
        # Per-request timeout + SDK retry cap. The SDK default is 10 MIN timeout + 2 retries, so a
        # single stalled call balloons to ~30 min (×retries) and a few look like a multi-hour hang —
        # the real cause of the earlier wedged runs (a single web_search call completes in ~20s).
        # Bound BOTH: 180s timeout × (max_retries+1) caps a wedged call's worst case.
        self.request_timeout_s = float(request_timeout_s)
        self.max_retries = int(max_retries)
        self._sdk = None

    def _client(self):
        if self._sdk is None:
            import anthropic  # lazy — only needed on dispatch
            self._sdk = anthropic.Anthropic(timeout=self.request_timeout_s,
                                            max_retries=self.max_retries)
        return self._sdk

    # -- cost --
    def _track(self, usage, model: str, *, batch: bool) -> None:
        p = _price(model)
        factor = 0.5 if batch else 1.0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        in_tok = getattr(usage, "input_tokens", 0) or 0
        out_tok = getattr(usage, "output_tokens", 0) or 0
        cost = ((in_tok * p["in"] + cache_write * p["in"] * 1.25 + cache_read * p["in"] * 0.1
                 + out_tok * p["out"]) / 1_000_000) * factor
        self.spent_usd += cost
        self.calls += 1

    def _track_searches(self, message) -> None:
        """Count web_search server-tool uses in a response and bill the per-search fee (separate from
        token cost). Web-search fetched content is already in usage.input_tokens (tracked above)."""
        n = sum(1 for b in getattr(message, "content", [])
                if getattr(b, "type", "") == "server_tool_use"
                and getattr(b, "name", "") == "web_search")
        if n:
            self.web_searches += n
            self.spent_usd += n * self.web_search_cost_per_search

    def _guard(self) -> None:
        if self.spent_usd >= self.max_usd:
            raise BudgetExceeded(f"spend ${self.spent_usd:.2f} >= cap ${self.max_usd:.2f}")

    # -- request shape (structured output + cached system, optional web_search tool) --
    @staticmethod
    def _params(model: str, system: str, schema: dict, bundle: dict, max_tokens: int,
                tools: Optional[list] = None) -> dict:
        p = {
            "model": model, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": json.dumps(bundle, default=str)}],
        }
        if tools:
            p["tools"] = tools
        return p

    @staticmethod
    def _extract(message) -> dict:
        # the LAST text block is the final structured JSON (server-tool web_search may emit earlier
        # text blocks before the constrained answer); fall back to None → {}.
        text = next((b.text for b in reversed(message.content) if b.type == "text"), None)
        return json.loads(text) if text else {}

    # -- real-time scoring (Haiku triage, Opus finalize) --
    def score_realtime(self, model: str, system: str, schema: dict, bundle: dict,
                       max_tokens: int, *, validate=None, max_retries: int = 3,
                       tools: Optional[list] = None, max_pause_turns: int = 4) -> Optional[dict]:
        self._guard()
        for _ in range(max_retries):
            try:
                params = self._params(model, system, schema, bundle, max_tokens, tools)
                msg = self._client().messages.create(**params)
                self._track(msg.usage, model, batch=False)
                self._track_searches(msg)
                # web_search runs a server-side tool loop; if it hits the iteration cap it returns
                # stop_reason=pause_turn — re-send the accumulated turn to let it finish.
                pauses = 0
                while getattr(msg, "stop_reason", None) == "pause_turn" and pauses < max_pause_turns:
                    params["messages"] = params["messages"] + [
                        {"role": "assistant", "content": msg.content}]
                    msg = self._client().messages.create(**params)
                    self._track(msg.usage, model, batch=False)
                    self._track_searches(msg)
                    pauses += 1
            except Exception as exc:  # noqa: BLE001 — fail-open one company, not the run
                log.warning("score_realtime error (%s): %s", model, exc)
                return None
            try:
                data = self._extract(msg)
                return validate(data) if validate else data
            except Exception as exc:  # noqa: BLE001 — malformed → retry (rare under structured out)
                log.debug("rubric validation failed, retrying: %s", exc)
        return None

    # -- async bounded-concurrency real-time fan-out (D13; reapplied from 3_Biopharmcatalyst) --
    def score_realtime_many(self, model: str, system: str, schema: dict, items: dict[str, dict],
                            max_tokens: int, *, validate=None, tools: Optional[list] = None,
                            concurrency: int = 6, max_pause_turns: int = 4,
                            validation_retries: int = 2, on_result=None) -> dict[str, dict]:
        """Score many bundles in PARALLEL via AsyncAnthropic with a bounded semaphore — far faster
        wall-clock than a sequential `score_realtime` loop (the cause of the ~1hr web-search run).
        Returns {id: data} (failed/None items dropped). ``on_result(cid, data)`` fires inside each task
        the instant that item's score is produced — so results are PERSISTED as each returns (per-item
        durability during the parallel tier, not only after the gather). Same pause_turn + cost/search
        tracking; asyncio is single-threaded so the `_track`/persist increments are safe."""
        if not items:
            return {}
        self._guard()
        from anthropic import AsyncAnthropic

        async def _one(aclient, sem, cid, bundle):
            async with sem:
                for _ in range(validation_retries):
                    try:
                        params = self._params(model, system, schema, bundle, max_tokens, tools)
                        msg = await aclient.messages.create(**params)
                        self._track(msg.usage, model, batch=False)
                        self._track_searches(msg)
                        pauses = 0
                        while getattr(msg, "stop_reason", None) == "pause_turn" and pauses < max_pause_turns:
                            params["messages"] = params["messages"] + [
                                {"role": "assistant", "content": msg.content}]
                            msg = await aclient.messages.create(**params)
                            self._track(msg.usage, model, batch=False)
                            self._track_searches(msg)
                            pauses += 1
                    except Exception as exc:  # noqa: BLE001 — fail-open one item
                        log.warning("score_realtime_many error (%s): %s", model, exc)
                        return cid, None
                    try:
                        data = self._extract(msg)
                        data = validate(data) if validate else data
                    except Exception as exc:  # noqa: BLE001
                        log.debug("validation failed, retrying: %s", exc)
                        continue
                    if on_result is not None:
                        on_result(cid, data)            # persist this item NOW (durable per-item)
                    return cid, data
                return cid, None

        async def _run():
            aclient = AsyncAnthropic(timeout=self.request_timeout_s, max_retries=self.max_retries)
            sem = asyncio.Semaphore(max(1, int(concurrency)))
            pairs = await asyncio.gather(*[_one(aclient, sem, cid, b) for cid, b in items.items()])
            return {cid: r for cid, r in pairs if r is not None}

        return asyncio.run(_run())

    # -- batch scoring (the Sonnet pass) — split submit/collect for crash-recovery (D13) --
    def submit_batch(self, model: str, system: str, schema: dict, bundles: dict[str, dict],
                     max_tokens: int, *, tools: Optional[list] = None) -> Optional[str]:
        """Submit a Message Batch and return its ``batch_id`` IMMEDIATELY (no poll). Persist the id
        before polling so a crash mid-poll can re-attach (`collect_batch`) instead of orphaning the
        batch (Anthropic keeps results ~29 days). Returns None for an empty set."""
        if not bundles:
            return None
        self._guard()
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        client = self._client()
        requests = [Request(custom_id=cid,
                            params=MessageCreateParamsNonStreaming(
                                **self._params(model, system, schema, b, max_tokens, tools)))
                    for cid, b in bundles.items()]
        batch = client.messages.batches.create(requests=requests)
        log.info("batch %s submitted (%d requests)", batch.id, len(requests))
        return batch.id

    def collect_batch(self, batch_id: str, model: str, *, validate=None, poll_seconds: int = 30,
                      max_wait_seconds: int = 24 * 3600) -> dict[str, dict]:
        """Poll a submitted batch to completion, then decode + track usage/searches. Idempotent —
        safe to re-call on the same ``batch_id`` (the `--resume` recovery path)."""
        client = self._client()
        waited = 0
        while waited < max_wait_seconds:
            b = client.messages.batches.retrieve(batch_id)
            if b.processing_status == "ended":
                break
            time.sleep(poll_seconds)
            waited += poll_seconds

        out: dict[str, dict] = {}
        for result in client.messages.batches.results(batch_id):
            if result.result.type != "succeeded":
                log.warning("batch item %s: %s", result.custom_id, result.result.type)
                continue
            self._track(result.result.message.usage, model, batch=True)
            self._track_searches(result.result.message)
            try:
                data = self._extract(result.result.message)
                out[result.custom_id] = validate(data) if validate else data
            except Exception as exc:  # noqa: BLE001
                log.warning("batch item %s invalid: %s", result.custom_id, exc)
        return out

    def score_batch(self, model: str, system: str, schema: dict, bundles: dict[str, dict],
                    max_tokens: int, *, validate=None, poll_seconds: int = 30,
                    max_wait_seconds: int = 24 * 3600, tools: Optional[list] = None,
                    on_submit=None) -> dict[str, dict]:
        """Submit + poll a Message Batch (50% cost). ``on_submit(batch_id)`` fires the instant the
        batch is created — the caller persists the id before the long poll (crash-recovery, D13).
        With ``tools`` (web_search) the server runs the bounded tool loop per request."""
        batch_id = self.submit_batch(model, system, schema, bundles, max_tokens, tools=tools)
        if batch_id is None:
            return {}
        if on_submit is not None:
            try:
                on_submit(batch_id)
            except Exception as exc:  # noqa: BLE001 — never let the callback kill the batch
                log.warning("on_submit callback raised %s; continuing to poll", exc)
        return self.collect_batch(batch_id, model, validate=validate, poll_seconds=poll_seconds,
                                  max_wait_seconds=max_wait_seconds)
