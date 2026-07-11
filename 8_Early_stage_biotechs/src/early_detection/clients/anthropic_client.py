"""Anthropic API wrapper (Module 8) — ported from platform_discoverer with current-fact corrections.

Bakes in the accumulated Claude-dispatch lessons (see memory: reuse_claude_dispatch_patterns,
claude_web_search_variant_and_output_cap, persist_during_long_api_batches):
  - structured output via ``output_config.format`` (json_schema), validated at the tool-call layer;
  - prompt caching of the static system prompt (``cache_control: ephemeral``);
  - **basic** ``web_search_20250305`` — honors ``max_uses`` and is ~10× faster than the dynamic
    ``web_search_20260209`` (which ignores max_uses and timed out); also the only variant valid on
    Haiku 4.5, the cheap extraction tier (spec §5.5);
  - full ``max_tokens`` so structured-output JSON isn't truncated → dropped;
  - async bounded-concurrency real-time fan-out with per-item persist (crash-safe);
  - Batch submit/collect split with a persisted batch_id for ``--resume``;
  - a hard ``max_usd`` guard + running cost tracking; key from env, never logged.

Model IDs + pricing are current API facts (verified against the claude-api skill, 2026-07-10).
Calls happen ONLY on explicit dispatch behind the CLI's ``[y/N]`` cost gate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from . import _net  # noqa: F401 — import triggers .env load so ANTHROPIC_API_KEY is present

log = logging.getLogger(__name__)

# USD per 1M tokens (input, output). Batch API halves both; cache reads ~0.1× input, writes ~1.25×.
PRICING = {
    "claude-haiku-4-5":  {"in": 1.0, "out": 5.0},
    "claude-sonnet-5":   {"in": 3.0, "out": 15.0},
    "claude-opus-4-8":   {"in": 5.0, "out": 25.0},
}


def _price(model: str) -> dict:
    for k, v in PRICING.items():
        if model.startswith(k):
            return v
    return {"in": 5.0, "out": 25.0}


class BudgetExceeded(RuntimeError):
    """Raised when a call would push spend past ``max_usd`` (hard stop)."""


def web_search_tool(max_uses: int = 4, allowed_domains: Optional[list] = None) -> dict:
    """The BASIC ``web_search_20250305`` server tool — honors ``max_uses`` and is ~10× faster than the
    dynamic ``web_search_20260209`` variant (which ignored max_uses and timed out >600s). It is also
    the only web_search variant valid on Haiku 4.5. ``allowed_domains`` restricts the search surface."""
    tool: dict = {"type": "web_search_20250305", "name": "web_search", "max_uses": int(max_uses)}
    if allowed_domains:
        tool["allowed_domains"] = list(allowed_domains)
    return tool


class AnthropicClient:
    def __init__(self, *, max_usd: float = 5.0, web_search_cost_per_search: float = 0.01,
                 request_timeout_s: float = 180.0, max_retries: int = 1):
        self.max_usd = float(max_usd)
        self.spent_usd = 0.0
        self.calls = 0
        self.web_searches = 0
        self.web_search_cost_per_search = float(web_search_cost_per_search)
        # Bound both timeout and SDK retries: the SDK default (10min × 2 retries) turns one stalled
        # call into a ~30min hang. 180s × (max_retries+1) caps a wedged call.
        self.request_timeout_s = float(request_timeout_s)
        self.max_retries = int(max_retries)
        self._sdk = None

    def _client(self):
        if self._sdk is None:
            import anthropic  # lazy — only needed on dispatch
            self._sdk = anthropic.Anthropic(timeout=self.request_timeout_s, max_retries=self.max_retries)
        return self._sdk

    # ── cost ──────────────────────────────────────────────────────────────────
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
        n = sum(1 for b in getattr(message, "content", [])
                if getattr(b, "type", "") == "server_tool_use"
                and getattr(b, "name", "") == "web_search")
        if n:
            self.web_searches += n
            self.spent_usd += n * self.web_search_cost_per_search

    def _guard(self) -> None:
        if self.spent_usd >= self.max_usd:
            raise BudgetExceeded(f"spend ${self.spent_usd:.2f} >= cap ${self.max_usd:.2f}")

    # ── request shape (structured output + cached system, optional web_search) ──
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
        # last text block is the constrained JSON (web_search emits earlier text blocks)
        text = next((b.text for b in reversed(message.content) if b.type == "text"), None)
        return json.loads(text) if text else {}

    # ── real-time single ────────────────────────────────────────────────────────
    def run_realtime(self, model: str, system: str, schema: dict, bundle: dict, max_tokens: int, *,
                     validate=None, max_retries: int = 3, tools: Optional[list] = None,
                     max_pause_turns: int = 4) -> Optional[dict]:
        self._guard()
        for _ in range(max_retries):
            try:
                params = self._params(model, system, schema, bundle, max_tokens, tools)
                msg = self._client().messages.create(**params)
                self._track(msg.usage, model, batch=False)
                self._track_searches(msg)
                pauses = 0
                while getattr(msg, "stop_reason", None) == "pause_turn" and pauses < max_pause_turns:
                    params["messages"] = params["messages"] + [{"role": "assistant", "content": msg.content}]
                    msg = self._client().messages.create(**params)
                    self._track(msg.usage, model, batch=False)
                    self._track_searches(msg)
                    pauses += 1
                if getattr(msg, "stop_reason", None) == "refusal":
                    log.warning("run_realtime: refusal"); return None
            except Exception as exc:  # noqa: BLE001 — fail-open one item, not the run
                log.warning("run_realtime error (%s): %s", model, exc)
                return None
            try:
                data = self._extract(msg)
                return validate(data) if validate else data
            except Exception as exc:  # noqa: BLE001 — malformed → retry (rare under structured out)
                log.debug("validation failed, retrying: %s", exc)
        return None

    # ── async bounded-concurrency fan-out, per-item persist ─────────────────────
    def run_realtime_many(self, model: str, system: str, schema: dict, items: dict[str, dict],
                          max_tokens: int, *, validate=None, tools: Optional[list] = None,
                          concurrency: int = 6, max_pause_turns: int = 4, validation_retries: int = 2,
                          on_result=None) -> dict[str, dict]:
        """Run many bundles in PARALLEL via AsyncAnthropic with a bounded semaphore. ``on_result(cid,
        data)`` fires the instant each item's result is produced — so results are PERSISTED as each
        returns (per-item durability). Returns {id: data} with failed items dropped."""
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
                            params["messages"] = params["messages"] + [{"role": "assistant", "content": msg.content}]
                            msg = await aclient.messages.create(**params)
                            self._track(msg.usage, model, batch=False)
                            self._track_searches(msg)
                            pauses += 1
                        if getattr(msg, "stop_reason", None) == "refusal":
                            return cid, None
                    except Exception as exc:  # noqa: BLE001 — fail-open one item
                        log.warning("run_realtime_many error (%s): %s", model, exc)
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

    # ── Batch: submit/collect split for crash recovery ──────────────────────────
    def submit_batch(self, model: str, system: str, schema: dict, bundles: dict[str, dict],
                     max_tokens: int, *, tools: Optional[list] = None) -> Optional[str]:
        """Submit a Message Batch (50% cost) and return its ``batch_id`` IMMEDIATELY. Persist the id
        before polling so a crash mid-poll can re-attach via ``collect_batch`` (results kept ~29 days)."""
        if not bundles:
            return None
        self._guard()
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request
        requests = [Request(custom_id=cid,
                            params=MessageCreateParamsNonStreaming(
                                **self._params(model, system, schema, b, max_tokens, tools)))
                    for cid, b in bundles.items()]
        batch = self._client().messages.batches.create(requests=requests)
        log.info("batch %s submitted (%d requests)", batch.id, len(requests))
        return batch.id

    def collect_batch(self, batch_id: str, model: str, *, validate=None, poll_seconds: int = 30,
                      max_wait_seconds: int = 24 * 3600, on_result=None) -> dict[str, dict]:
        """Poll a submitted batch to completion, decode + track usage. Idempotent — safe to re-call on
        the same ``batch_id`` (the ``--resume`` path). ``on_result`` persists each item as decoded."""
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
                data = validate(data) if validate else data
            except Exception as exc:  # noqa: BLE001
                log.warning("batch item %s invalid: %s", result.custom_id, exc)
                continue
            out[result.custom_id] = data
            if on_result is not None:
                on_result(result.custom_id, data)
        return out
