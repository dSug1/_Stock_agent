"""Claude dispatch for Stage 3 — the only networked piece. Implements the project's hard-won lessons:

* **Bounded SDK timeout** (the "1hr hang" fix, memory D14) — `client.with_options(timeout=...)`.
* **Prompt caching** — the stable system prefix carries `cache_control` (set in `rubric.build_request`).
* **Basic `web_search_20250305`** variant + bounded `max_uses` + the 12500-token output cap (memory D16).
* **Batch API** submit/poll split (−50% tokens) — the production default; `batch_id` persisted by the
  caller before polling for `--resume`.
* **Async bounded concurrency** for the real-time tiers (`score_many_realtime`, memory dispatch patterns).
* **Crash-safe incremental persist** — results are handed to an `on_result` callback the instant each
  completes, so the caller writes them to the DB one at a time (never a bulk write at the end).
* **pause_turn** handling for the server-tool loop.

`stage3_score.py` depends only on the three public methods (`score_many_realtime`, `submit_batch`,
`poll_batch`), so it's tested offline with a fake implementing the same shape.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Callable, Optional

from .. import config as _cfg
from ..scoring.rubric import clamp_parsed


def _load_env() -> None:
    """Read the repo-root `.env` for `ANTHROPIC_API_KEY` if not already in the environment (never logged)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env = _cfg.ROOT.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, val = line.split("=", 1)
        os.environ.setdefault(k.strip(), val.strip())


def _parse_message(message) -> dict:
    """Extract + clamp the structured JSON from a Claude response; count web searches billed."""
    text = next((b.text for b in message.content if getattr(b, "type", "") == "text"), "")
    searches = sum(1 for b in message.content
                   if getattr(b, "type", "") == "server_tool_use" and getattr(b, "name", "") == "web_search")
    parsed = clamp_parsed(json.loads(text)) if text else {}
    usage = getattr(message, "usage", None)
    return {"parsed": parsed, "web_searches": searches,
            "usage": {"input": getattr(usage, "input_tokens", 0),
                      "output": getattr(usage, "output_tokens", 0),
                      "cache_read": getattr(usage, "cache_read_input_tokens", 0)} if usage else {}}


class AnthropicScorer:
    """Thin wrapper over the Anthropic SDK with the dispatch lessons applied."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cl = cfg.get("claude", {})
        self.timeout = float(self.cl.get("request_timeout_s", 120.0))
        self.max_continuations = int(self.cl.get("max_continuations", 4))
        self.web_searches = 0

    # ------------------------------------------------------------------ real-time (async, bounded)
    def score_many_realtime(self, requests: list[dict], on_result: Callable[[dict], None],
                            concurrency: Optional[int] = None) -> None:
        """Score many requests concurrently (bounded); ``on_result`` fires per completion for incremental
        persist. Errors are returned per-item (``error`` set), never raised, so one bad name can't sink
        the batch."""
        if not requests:
            return
        n = int(concurrency or self.cl.get("concurrency", 6))
        asyncio.run(self._run_many(requests, on_result, n))

    async def _run_many(self, requests, on_result, concurrency) -> None:
        _load_env()
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic(timeout=self.timeout)
        sem = asyncio.Semaphore(concurrency)

        async def one(req):
            async with sem:
                res = await self._ascore(client, req)
                on_result(res)

        try:
            await asyncio.gather(*[one(r) for r in requests])
        finally:
            await client.close()

    async def _ascore(self, client, req: dict) -> dict:
        cid, params = req["custom_id"], req["params"]
        try:
            msg = await client.messages.create(**params)
            cont = 0
            while getattr(msg, "stop_reason", "") == "pause_turn" and cont < self.max_continuations:
                cont += 1
                params2 = dict(params)
                params2["messages"] = list(params["messages"]) + [
                    {"role": "assistant", "content": msg.content}]
                msg = await client.messages.create(**params2)
            out = _parse_message(msg)
            self.web_searches += out["web_searches"]
            return {"custom_id": cid, **out, "error": None}
        except Exception as exc:                                   # fail-soft per item
            return {"custom_id": cid, "parsed": {}, "usage": {}, "web_searches": 0, "error": str(exc)}

    # ------------------------------------------------------------------ single structured call (Stage 0a)
    def complete(self, params: dict, timeout: Optional[float] = None) -> dict:
        """One synchronous structured call (no clamp — used for discovery, not scoring). pause_turn-safe.
        ``timeout`` overrides the default — discovery runs several searches and needs longer."""
        _load_env()
        import anthropic
        client = anthropic.Anthropic(timeout=timeout or self.timeout)
        try:
            msg = client.messages.create(**params)
            cont = 0
            while getattr(msg, "stop_reason", "") == "pause_turn" and cont < self.max_continuations:
                cont += 1
                p2 = dict(params)
                p2["messages"] = list(params["messages"]) + [{"role": "assistant", "content": msg.content}]
                msg = client.messages.create(**p2)
            text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), "")
            searches = sum(1 for b in msg.content
                           if getattr(b, "type", "") == "server_tool_use"
                           and getattr(b, "name", "") == "web_search")
            self.web_searches += searches
            return {"parsed": json.loads(text) if text else {}, "web_searches": searches, "error": None}
        except Exception as exc:
            return {"parsed": {}, "web_searches": 0, "error": str(exc)}

    # ------------------------------------------------------------------ Batch API (submit / poll split)
    def submit_batch(self, requests: list[dict]) -> str:
        """Submit a batch (−50% tokens) and return its id. The caller persists the id BEFORE polling."""
        _load_env()
        import anthropic
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        client = anthropic.Anthropic(timeout=self.timeout)
        batch = client.messages.batches.create(requests=[
            Request(custom_id=r["custom_id"], params=MessageCreateParamsNonStreaming(**r["params"]))
            for r in requests
        ])
        return batch.id

    def poll_batch(self, batch_id: str) -> Optional[list[dict]]:
        """Return results once the batch has ``ended``; ``None`` while still processing (caller re-polls)."""
        _load_env()
        import anthropic
        client = anthropic.Anthropic(timeout=self.timeout)
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            return None
        results = []
        for r in client.messages.batches.results(batch_id):
            cid = r.custom_id
            if r.result.type == "succeeded":
                out = _parse_message(r.result.message)
                self.web_searches += out["web_searches"]
                results.append({"custom_id": cid, **out, "error": None})
            else:
                results.append({"custom_id": cid, "parsed": {}, "usage": {}, "web_searches": 0,
                                "error": r.result.type})
        return results
