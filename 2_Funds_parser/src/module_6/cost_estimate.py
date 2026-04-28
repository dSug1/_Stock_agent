"""Pre-flight cost estimator (D16, D40 reshape).

Pure-compute function: given a feed of tier-classified tickers, the cached
prefix text, sample pack JSON blobs, the scoring config, and the pricing
table, returns a structured EstimateOutputs with three pricing scenarios
(no-optim / cache-only / cache+batch) plus search-fee + token totals.

Token counting uses a char-based heuristic (chars / 3.6 ≈ tokens for
biotech/financial English text). Anthropic's billed token count typically
falls within ±10% of this estimate. The HTML report explicitly states the
heuristic so the user reads the number with appropriate uncertainty.

Standalone — no Anthropic API calls, no network.
"""
from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .tier import Tier, TierClassification

# Heuristic: average chars per token for English / biotech text.
# Anthropic's Claude tokenizer (o200k_base-like) lands ~3.5-3.8 chars/token
# for prose with structured JSON; 3.6 is a defensible mid-point.
_CHARS_PER_TOKEN = 3.6


def _approx_tokens(text: str) -> int:
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


# ---------------------------------------------------------------------------
# Inputs / outputs dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EstimateInputs:
    quarter: str
    prompt_version: str
    model: str
    cached_prefix_text: str               # full system prompt + few-shots
    pack_jsons_sample: list[str]          # ≥ 1 — pack JSON strings to estimate suffix
    feed_tiers: list[TierClassification]  # one entry per ticker in the feed
    max_output_tokens_full: int           # for Tier C
    max_output_tokens_tier_b: int         # for Tier B
    max_uses_full: int                    # web_search cap for Tier C
    max_uses_tier_b: int                  # web_search cap for Tier B
    pricing: dict                         # scoring.yaml::pricing dict
    user_message_overhead_chars: int = 600  # chars added to pack: prior_research + prior_thesis + framing


@dataclass
class TierBreakdown:
    tier: Tier
    ticker_count: int
    api_calls: int
    avg_input_tokens_per_call: int
    avg_output_tokens_per_call: int
    avg_search_calls_per_call: int


@dataclass
class ScenarioCost:
    name: str                                   # 'no-optim' | 'cache-only' | 'cache+batch'
    input_tokens_total: int
    output_tokens_total: int
    cache_read_tokens_total: int
    cache_creation_tokens_total: int
    token_cost_usd: float
    search_fee_usd_upper_bound: float
    total_usd: float


@dataclass
class EstimateOutputs:
    quarter: str
    prompt_version: str
    model: str
    cached_prefix_tokens: int
    avg_pack_suffix_tokens: int
    avg_search_result_tokens_full: int          # avg snippet tokens × max_uses_full
    avg_search_result_tokens_tier_b: int
    feed_size: int
    tier_breakdowns: list[TierBreakdown]
    scenarios: list[ScenarioCost]               # the three priced scenarios
    worst_case_if_all_b_escalate_usd: float     # cache+batch scenario, all Tier B → C
    timestamp_utc: str
    tier_b_escalation_count: int = 0            # how many Tier B could escalate
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core estimator
# ---------------------------------------------------------------------------


def estimate_cost(inputs: EstimateInputs) -> EstimateOutputs:
    """Compute the three scenarios and the worst-case escalation ceiling."""

    pricing = inputs.pricing
    in_per_mtok = float(pricing["input_per_mtok"])
    out_per_mtok = float(pricing["output_per_mtok"])
    cache_read_mult = float(pricing["cache_read_multiplier"])
    cache_create_mult = float(pricing["cache_creation_multiplier"])
    batch_disc = float(pricing["batch_discount"])
    search_per_1k = float(pricing["web_search_per_1k"])
    search_avg_tokens = int(pricing.get("search_result_avg_tokens", 1500))
    # 2026-04-28 — empirical calibration vs real Anthropic invoices. Applied
    # to every scenario's `total_usd` (and the worst-case ceiling) so the
    # number the user sees pre-dispatch matches what they'll actually be billed.
    # Source data + rationale in scoring.yaml::pricing.cost_calibration_factor.
    calibration = float(pricing.get("cost_calibration_factor", 1.0))

    cached_prefix_tokens = _approx_tokens(inputs.cached_prefix_text)
    if not inputs.pack_jsons_sample:
        raise ValueError("pack_jsons_sample must contain at least one pack")
    pack_suffix_tokens_each = [_approx_tokens(s) for s in inputs.pack_jsons_sample]
    avg_pack_suffix_tokens = sum(pack_suffix_tokens_each) // len(pack_suffix_tokens_each)
    user_overhead_tokens = max(1, int(inputs.user_message_overhead_chars / _CHARS_PER_TOKEN))
    avg_user_message_tokens = avg_pack_suffix_tokens + user_overhead_tokens

    avg_search_result_tokens_full = inputs.max_uses_full * search_avg_tokens
    avg_search_result_tokens_tier_b = inputs.max_uses_tier_b * search_avg_tokens

    # Tier breakdown
    tier_counts = {Tier.A: 0, Tier.B: 0, Tier.C: 0}
    for tc in inputs.feed_tiers:
        tier_counts[tc.overall_tier] += 1

    breakdowns: list[TierBreakdown] = []
    breakdowns.append(TierBreakdown(
        tier=Tier.A,
        ticker_count=tier_counts[Tier.A],
        api_calls=0,
        avg_input_tokens_per_call=0,
        avg_output_tokens_per_call=0,
        avg_search_calls_per_call=0,
    ))
    breakdowns.append(TierBreakdown(
        tier=Tier.B,
        ticker_count=tier_counts[Tier.B],
        api_calls=tier_counts[Tier.B],
        avg_input_tokens_per_call=avg_user_message_tokens + avg_search_result_tokens_tier_b,
        avg_output_tokens_per_call=inputs.max_output_tokens_tier_b,
        avg_search_calls_per_call=inputs.max_uses_tier_b,
    ))
    breakdowns.append(TierBreakdown(
        tier=Tier.C,
        ticker_count=tier_counts[Tier.C],
        api_calls=tier_counts[Tier.C],
        avg_input_tokens_per_call=avg_user_message_tokens + avg_search_result_tokens_full,
        avg_output_tokens_per_call=inputs.max_output_tokens_full,
        avg_search_calls_per_call=inputs.max_uses_full,
    ))

    # ---------- Token totals (across all paid calls = Tier B + Tier C) ----------
    total_api_calls = tier_counts[Tier.B] + tier_counts[Tier.C]

    # Non-cached input (pack + search results) — paid full price
    non_cached_input_tokens = (
        breakdowns[1].api_calls * breakdowns[1].avg_input_tokens_per_call
        + breakdowns[2].api_calls * breakdowns[2].avg_input_tokens_per_call
    )
    # Output tokens
    output_tokens = (
        breakdowns[1].api_calls * breakdowns[1].avg_output_tokens_per_call
        + breakdowns[2].api_calls * breakdowns[2].avg_output_tokens_per_call
    )

    # Cache split: first call writes the prefix (cache_creation), the rest read it.
    # Note this is per dispatch — the cache TTL is 5 min for ephemeral, but a
    # batch submission processes all calls in one window so all calls after #1
    # can hit the cached prefix.
    if total_api_calls > 0:
        cache_creation_tokens = cached_prefix_tokens
        cache_read_tokens = cached_prefix_tokens * (total_api_calls - 1)
    else:
        cache_creation_tokens = 0
        cache_read_tokens = 0

    # Web search calls
    total_search_calls = (
        breakdowns[1].api_calls * breakdowns[1].avg_search_calls_per_call
        + breakdowns[2].api_calls * breakdowns[2].avg_search_calls_per_call
    )
    search_fee_upper_bound = (total_search_calls / 1000.0) * search_per_1k

    # ---------- Scenario A: no optimization (no cache, no batch) ----------
    # Every call sends full prefix + suffix at full input price.
    no_optim_input_total = total_api_calls * cached_prefix_tokens + non_cached_input_tokens
    no_optim_token_cost = (
        (no_optim_input_total / 1_000_000.0) * in_per_mtok
        + (output_tokens / 1_000_000.0) * out_per_mtok
    )
    scenario_no_optim = ScenarioCost(
        name="no-optim",
        input_tokens_total=no_optim_input_total,
        output_tokens_total=output_tokens,
        cache_read_tokens_total=0,
        cache_creation_tokens_total=0,
        token_cost_usd=no_optim_token_cost,
        search_fee_usd_upper_bound=search_fee_upper_bound,
        total_usd=(no_optim_token_cost + search_fee_upper_bound) * calibration,
    )

    # ---------- Scenario B: cache-only (no batch) ----------
    cache_only_token_cost = (
        (cache_creation_tokens / 1_000_000.0) * in_per_mtok * cache_create_mult
        + (cache_read_tokens / 1_000_000.0) * in_per_mtok * cache_read_mult
        + (non_cached_input_tokens / 1_000_000.0) * in_per_mtok
        + (output_tokens / 1_000_000.0) * out_per_mtok
    )
    scenario_cache_only = ScenarioCost(
        name="cache-only",
        input_tokens_total=non_cached_input_tokens + cache_creation_tokens,
        output_tokens_total=output_tokens,
        cache_read_tokens_total=cache_read_tokens,
        cache_creation_tokens_total=cache_creation_tokens,
        token_cost_usd=cache_only_token_cost,
        search_fee_usd_upper_bound=search_fee_upper_bound,
        total_usd=(cache_only_token_cost + search_fee_upper_bound) * calibration,
    )

    # ---------- Scenario C: cache + batch (production default) ----------
    cache_batch_token_cost = cache_only_token_cost * batch_disc
    # Search fees do NOT get the batch discount per Anthropic docs.
    scenario_cache_batch = ScenarioCost(
        name="cache+batch",
        input_tokens_total=non_cached_input_tokens + cache_creation_tokens,
        output_tokens_total=output_tokens,
        cache_read_tokens_total=cache_read_tokens,
        cache_creation_tokens_total=cache_creation_tokens,
        token_cost_usd=cache_batch_token_cost,
        search_fee_usd_upper_bound=search_fee_upper_bound,
        total_usd=(cache_batch_token_cost + search_fee_upper_bound) * calibration,
    )

    # ---------- Worst case: every Tier B escalates to Tier C ----------
    # Compute incremental cost of converting tier_counts[B] from B-cost to C-cost.
    if tier_counts[Tier.B] > 0:
        # Recompute totals as if all Bs were Cs.
        b_to_c_extra_input = tier_counts[Tier.B] * (
            breakdowns[2].avg_input_tokens_per_call - breakdowns[1].avg_input_tokens_per_call
        )
        b_to_c_extra_output = tier_counts[Tier.B] * (
            breakdowns[2].avg_output_tokens_per_call - breakdowns[1].avg_output_tokens_per_call
        )
        b_to_c_extra_search_calls = tier_counts[Tier.B] * (
            inputs.max_uses_full - inputs.max_uses_tier_b
        )
        extra_token_cost_full_price = (
            (b_to_c_extra_input / 1_000_000.0) * in_per_mtok
            + (b_to_c_extra_output / 1_000_000.0) * out_per_mtok
        )
        extra_token_cost_batched = extra_token_cost_full_price * batch_disc
        extra_search_fee = (b_to_c_extra_search_calls / 1000.0) * search_per_1k
        worst_case = scenario_cache_batch.total_usd + (extra_token_cost_batched + extra_search_fee) * calibration
    else:
        worst_case = scenario_cache_batch.total_usd

    notes = [
        f"Token counts are heuristic (chars / {_CHARS_PER_TOKEN}); actual Anthropic billing may differ ±10%.",
        f"Pricing values from scoring.yaml::pricing — verify against Anthropic's pricing page before dispatch.",
        f"Web search fees do not benefit from the batch 50% discount (per Anthropic docs).",
        f"Search-result tokens are estimated at {search_avg_tokens} tokens per snippet × max_uses; actual snippets vary by domain (paywalled domains return shorter content).",
    ]
    if tier_counts[Tier.B] > 0:
        notes.append(
            f"'Worst-case if all Tier B escalate' assumes every Tier B's light-refresh call returns "
            f"material_change=true and the second-batch escalation runs at Tier C cost."
        )

    return EstimateOutputs(
        quarter=inputs.quarter,
        prompt_version=inputs.prompt_version,
        model=inputs.model,
        cached_prefix_tokens=cached_prefix_tokens,
        avg_pack_suffix_tokens=avg_pack_suffix_tokens,
        avg_search_result_tokens_full=avg_search_result_tokens_full,
        avg_search_result_tokens_tier_b=avg_search_result_tokens_tier_b,
        feed_size=len(inputs.feed_tiers),
        tier_breakdowns=breakdowns,
        scenarios=[scenario_no_optim, scenario_cache_only, scenario_cache_batch],
        worst_case_if_all_b_escalate_usd=worst_case,
        timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tier_b_escalation_count=tier_counts[Tier.B],
        notes=notes,
    )


# ---------------------------------------------------------------------------
# HTML renderer — self-contained, no JS deps
# ---------------------------------------------------------------------------


def render_cost_html(out: EstimateOutputs, gates: dict, output_path: Path) -> None:
    """Write a self-contained HTML report to ``output_path``."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def fmt_usd(x: float) -> str:
        if x >= 1000:
            return f"${x:,.0f}"
        if x >= 10:
            return f"${x:,.2f}"
        return f"${x:.4f}"

    def fmt_int(x: int) -> str:
        return f"{x:,}"

    tier_rows = ""
    for tb in out.tier_breakdowns:
        tier_rows += (
            f"<tr><td><b>Tier {tb.tier.value}</b></td>"
            f"<td>{fmt_int(tb.ticker_count)}</td>"
            f"<td>{fmt_int(tb.api_calls)}</td>"
            f"<td>{fmt_int(tb.avg_input_tokens_per_call)}</td>"
            f"<td>{fmt_int(tb.avg_output_tokens_per_call)}</td>"
            f"<td>{fmt_int(tb.avg_search_calls_per_call)}</td></tr>"
        )

    scenario_rows = ""
    for s in out.scenarios:
        is_prod = s.name == "cache+batch"
        bg = ' style="background:#e8f5e9; font-weight:600"' if is_prod else ""
        scenario_rows += (
            f"<tr{bg}><td>{html.escape(s.name)}{' (production)' if is_prod else ''}</td>"
            f"<td>{fmt_int(s.input_tokens_total)}</td>"
            f"<td>{fmt_int(s.cache_read_tokens_total)}</td>"
            f"<td>{fmt_int(s.cache_creation_tokens_total)}</td>"
            f"<td>{fmt_int(s.output_tokens_total)}</td>"
            f"<td>{fmt_usd(s.token_cost_usd)}</td>"
            f"<td>{fmt_usd(s.search_fee_usd_upper_bound)}</td>"
            f"<td><b>{fmt_usd(s.total_usd)}</b></td></tr>"
        )

    gate_lines = "<br>".join(
        f"<code>{html.escape(k)}</code> = <code>{html.escape(repr(v))}</code>"
        for k, v in gates.items()
    )
    notes_html = "<ul>" + "".join(f"<li>{html.escape(n)}</li>" for n in out.notes) + "</ul>"

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>M6 cost estimate {html.escape(out.quarter)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
          max-width: 1100px; margin: 2em auto; padding: 0 1.5em;
          color: #222; background: #fafafa; line-height: 1.5; }}
  h1 {{ margin-bottom: 0.2em; }}
  .stamp {{ color: #888; font-size: 0.85em; margin-bottom: 2em; }}
  table {{ border-collapse: collapse; margin: 1em 0; width: 100%;
           background: #fff; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: right;
            font-size: 0.95em; }}
  th {{ background: #f0f0f0; text-align: center; }}
  td:first-child, th:first-child {{ text-align: left; }}
  .summary {{ background: #fff8e1; border-left: 4px solid #f9a825;
              padding: 1em 1.5em; margin: 1.5em 0; }}
  .gates code {{ font-size: 0.85em; }}
  .notes {{ font-size: 0.85em; color: #555; }}
  .worst {{ color: #b71c1c; font-weight: 600; }}
</style>
</head>
<body>
<h1>Module 6 — pre-flight cost estimate</h1>
<div class="stamp">
  Quarter: <code>{html.escape(out.quarter)}</code> &middot;
  Prompt version: <code>{html.escape(out.prompt_version)}</code> &middot;
  Model: <code>{html.escape(out.model)}</code> &middot;
  Generated: {html.escape(out.timestamp_utc)}
</div>

<div class="summary">
  <b>Feed size:</b> {fmt_int(out.feed_size)} tickers.<br>
  <b>Production cost (cache + batch):</b>
    {fmt_usd(out.scenarios[2].total_usd)}<br>
  <b>Worst case if all Tier B escalate:</b>
    <span class="worst">{fmt_usd(out.worst_case_if_all_b_escalate_usd)}</span>
</div>

<h2>Active gates (D21 / D27 / D28 / D29)</h2>
<div class="gates">{gate_lines}</div>

<h2>Tier breakdown</h2>
<table>
  <tr>
    <th>Tier</th><th>Tickers</th><th>API calls</th>
    <th>Avg input tokens / call</th><th>Avg output tokens / call</th>
    <th>Avg search calls / call</th>
  </tr>
  {tier_rows}
</table>

<h2>Pricing scenarios</h2>
<table>
  <tr>
    <th>Scenario</th>
    <th>Non-cached input tokens</th>
    <th>Cache-read tokens</th>
    <th>Cache-creation tokens</th>
    <th>Output tokens</th>
    <th>Token cost</th>
    <th>Web-search fee (upper bound)</th>
    <th>Total</th>
  </tr>
  {scenario_rows}
</table>

<h2>Token-count assumptions</h2>
<table>
  <tr><td>Cached prefix tokens (system + few-shots)</td>
      <td>{fmt_int(out.cached_prefix_tokens)}</td></tr>
  <tr><td>Avg pack suffix tokens</td>
      <td>{fmt_int(out.avg_pack_suffix_tokens)}</td></tr>
  <tr><td>Avg search-result tokens per Tier C call</td>
      <td>{fmt_int(out.avg_search_result_tokens_full)}</td></tr>
  <tr><td>Avg search-result tokens per Tier B call</td>
      <td>{fmt_int(out.avg_search_result_tokens_tier_b)}</td></tr>
</table>

<h2>Notes</h2>
<div class="notes">{notes_html}</div>

</body></html>
"""
    output_path.write_text(html_doc, encoding="utf-8")
