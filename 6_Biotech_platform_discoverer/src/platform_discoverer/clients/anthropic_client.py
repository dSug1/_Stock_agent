"""Anthropic API wrapper for the tiered scorer (spec §9.1, §14).

Thin layer over the official ``anthropic`` SDK: structured-output scoring calls (real-time and Batch),
prompt caching of the shared rubric system prompt, and running USD cost tracking against a hard
``max_usd`` ceiling. Model ids and the tiering come from config; pricing is API fact and lives here.

Calls are made ONLY on an explicit dispatch (the CLI gates with a [y/N] + cost estimate, repo
convention). The key is read from ``ANTHROPIC_API_KEY`` (loaded from repo-root ``.env`` by
``clients._net``); never logged.
"""

from __future__ import annotations

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


class AnthropicClient:
    def __init__(self, *, max_usd: float = 50.0):
        self.max_usd = float(max_usd)
        self.spent_usd = 0.0
        self.calls = 0
        self._sdk = None

    def _client(self):
        if self._sdk is None:
            import anthropic  # lazy — only needed on dispatch
            self._sdk = anthropic.Anthropic()
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

    def _guard(self) -> None:
        if self.spent_usd >= self.max_usd:
            raise BudgetExceeded(f"spend ${self.spent_usd:.2f} >= cap ${self.max_usd:.2f}")

    # -- request shape (structured output + cached system) --
    @staticmethod
    def _params(model: str, system: str, schema: dict, bundle: dict, max_tokens: int) -> dict:
        return {
            "model": model, "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": json.dumps(bundle, default=str)}],
        }

    @staticmethod
    def _extract(message) -> dict:
        text = next((b.text for b in message.content if b.type == "text"), None)
        return json.loads(text) if text else {}

    # -- real-time scoring (Haiku triage, Opus finalize) --
    def score_realtime(self, model: str, system: str, schema: dict, bundle: dict,
                       max_tokens: int, *, validate=None, max_retries: int = 3) -> Optional[dict]:
        self._guard()
        for _ in range(max_retries):
            try:
                msg = self._client().messages.create(
                    **self._params(model, system, schema, bundle, max_tokens))
            except Exception as exc:  # noqa: BLE001 — fail-open one company, not the run
                log.warning("score_realtime error (%s): %s", model, exc)
                return None
            self._track(msg.usage, model, batch=False)
            try:
                data = self._extract(msg)
                return validate(data) if validate else data
            except Exception as exc:  # noqa: BLE001 — malformed → retry (rare under structured out)
                log.debug("rubric validation failed, retrying: %s", exc)
        return None

    # -- batch scoring (the Sonnet pass) --
    def score_batch(self, model: str, system: str, schema: dict, bundles: dict[str, dict],
                    max_tokens: int, *, validate=None, poll_seconds: int = 30,
                    max_wait_seconds: int = 24 * 3600) -> dict[str, dict]:
        """Score many bundles via the Message Batches API (50% cost). Returns {company_id: data}."""
        if not bundles:
            return {}
        self._guard()
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        client = self._client()
        requests = [Request(custom_id=cid,
                            params=MessageCreateParamsNonStreaming(
                                **self._params(model, system, schema, b, max_tokens)))
                    for cid, b in bundles.items()]
        batch = client.messages.batches.create(requests=requests)
        log.info("batch %s submitted (%d requests)", batch.id, len(requests))

        waited = 0
        while waited < max_wait_seconds:
            b = client.messages.batches.retrieve(batch.id)
            if b.processing_status == "ended":
                break
            time.sleep(poll_seconds)
            waited += poll_seconds

        out: dict[str, dict] = {}
        for result in client.messages.batches.results(batch.id):
            if result.result.type != "succeeded":
                log.warning("batch item %s: %s", result.custom_id, result.result.type)
                continue
            self._track(result.result.message.usage, model, batch=True)
            try:
                data = self._extract(result.result.message)
                out[result.custom_id] = validate(data) if validate else data
            except Exception as exc:  # noqa: BLE001
                log.warning("batch item %s invalid: %s", result.custom_id, exc)
        return out
