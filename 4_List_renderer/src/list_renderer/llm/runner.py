"""The single LLM task runner (D24).

Every Claude call in the app goes through `run_llm_task`: it executes the request
(prompt caching on the system prefix, JSON forced via output_config.format,
adaptive thinking per Opus 4.8), then writes one row to the `llm_tasks` ledger
with task_type + payer for cost attribution. A summary task later is just a new
`task_type` on this same path.

The COST GATE is enforced by callers BEFORE calling this (estimate -> [y/N]);
this function performs the billed call unconditionally once reached.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from ..db import now_iso
from .billing import BillingContext

log = logging.getLogger("4_render_list.llm")


def run_llm_task(
    conn: sqlite3.Connection,
    *,
    task_type: str,
    system: str,
    user: str,
    schema: dict,
    model: str,
    max_output_tokens: int,
    billing_context: BillingContext,
    effort: str = "medium",
    ref_id: str | None = None,
    user_id: str = "local",
) -> dict:
    """Execute one structured Claude call. Returns the parsed JSON object.
    Logs cost + payer to `llm_tasks` regardless of success."""
    client = billing_context.client()
    status = "ok"
    usage = None
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=int(max_output_tokens),
            thinking={"type": "adaptive"},
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},  # cache the prefix
            }],
            messages=[{"role": "user", "content": user}],
        )
        usage = resp.usage
        if resp.stop_reason == "refusal":
            status = "refusal"
            raise RuntimeError("Claude refused the request (stop_reason=refusal)")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return json.loads(text)
    except Exception:
        if status == "ok":
            status = "error"
        raise
    finally:
        _log_task(conn, task_type, ref_id, billing_context, model, usage,
                  status, user_id)


def _log_task(conn, task_type, ref_id, billing_context, model, usage, status, user_id):
    """Append one row to the llm_tasks ledger (D24)."""
    in_tok = getattr(usage, "input_tokens", None) if usage else None
    out_tok = getattr(usage, "output_tokens", None) if usage else None
    cache_read = getattr(usage, "cache_read_input_tokens", None) if usage else None
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) if usage else 0
    usd = _usd(model, in_tok, out_tok, cache_read, cache_create)
    try:
        conn.execute(
            """
            INSERT INTO llm_tasks
                (task_type, ref_id, billing_context, payer, user_id, usd,
                 input_tokens, output_tokens, cache_read, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (task_type, ref_id, billing_context.name, billing_context.payer,
             user_id, usd, in_tok, out_tok, cache_read, status, now_iso()),
        )
        conn.commit()
    except Exception as exc:  # ledger failure must never mask the task result
        log.warning("llm_tasks ledger write failed: %s", exc)


# Actual-cost pricing (USD per token), mirrors resolver.yaml; used for the ledger.
_PRICE = {
    "claude-opus-4-8": (5.0e-6, 25.0e-6),
    "claude-sonnet-4-6": (3.0e-6, 15.0e-6),
    "claude-haiku-4-5": (1.0e-6, 5.0e-6),
}


def _usd(model, in_tok, out_tok, cache_read, cache_create):
    rate = _PRICE.get(model)
    if not rate or in_tok is None:
        return None
    in_rate, out_rate = rate
    # input_tokens excludes cached; add cache read (0.1x) + cache create (1.25x).
    cost = (in_tok * in_rate
            + (cache_read or 0) * in_rate * 0.10
            + (cache_create or 0) * in_rate * 1.25
            + (out_tok or 0) * out_rate)
    return round(cost, 6)
