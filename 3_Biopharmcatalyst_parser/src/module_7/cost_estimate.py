"""Module 7 — pre-flight cost estimator (single-tier).

Pure-compute, no API calls. Given the cached prefix text, sample pack
JSONs, and the pricing block, returns three scenarios:

  no-optim     — every call sends full prefix at full price
  cache-only   — prefix cached after first call
  cache+batch  — prefix cached AND 50% batch discount applied

Plus search-fee + token totals + the `cost_calibration_factor` (0.10)
applied to FINAL totals so the number the user sees pre-dispatch
matches the actual Anthropic invoice (memory `project_anthropic_cost_calibration`).

Spec: spec/module_7_spec.md §5.9.
Decisions: spec/decisions.md § D16.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Heuristic: average chars per token for English / biotech text.
_CHARS_PER_TOKEN = 3.6


def _approx_tokens(text: str) -> int:
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


# ─────────────────────── inputs / outputs ──────────────────────


@dataclass(frozen=True)
class EstimateInputs:
    snapshot_date: str
    prompt_version: str
    model: str
    cached_prefix_text: str               # full system prompt + few-shots
    pack_jsons_sample: list[str]           # >=1 — pack JSON strings to estimate suffix
    n_tickers: int                          # number of full-deep-dive calls
    max_output_tokens: int
    max_uses_web_search: int
    pricing: dict                           # module_7.yaml::pricing dict
    user_message_overhead_chars: int = 400  # framing line + closing instruction
    sync_concurrency: int = 1               # D21 — affects `cache-only` scenario; concurrency>1 in sync
                                            # mode means each parallel call writes its own cache.


@dataclass
class ScenarioCost:
    name: str                                 # 'no-optim' | 'cache-only' | 'cache+batch'
    input_tokens_total: int
    output_tokens_total: int
    cache_read_tokens_total: int
    cache_creation_tokens_total: int
    token_cost_usd: float
    search_fee_usd_upper_bound: float
    total_usd: float


@dataclass
class EstimateOutputs:
    snapshot_date: str
    prompt_version: str
    model: str
    cached_prefix_tokens: int
    avg_pack_suffix_tokens: int
    avg_search_result_tokens: int
    n_tickers: int
    n_api_calls: int
    scenarios: list[ScenarioCost]
    timestamp_utc: str
    notes: list[str] = field(default_factory=list)

    @property
    def production_total_usd(self) -> float:
        return self.scenarios[2].total_usd if len(self.scenarios) >= 3 else 0.0


# ─────────────────────── core estimator ────────────────────────


def estimate_cost(inputs: EstimateInputs) -> EstimateOutputs:
    if not inputs.pack_jsons_sample:
        raise ValueError("pack_jsons_sample must contain at least one pack")

    pricing = inputs.pricing
    in_per_mtok = float(pricing["input_per_mtok"])
    out_per_mtok = float(pricing["output_per_mtok"])
    cache_read_mult = float(pricing["cache_read_multiplier"])
    cache_create_mult = float(pricing["cache_creation_multiplier"])
    batch_disc = float(pricing["batch_discount"])
    search_per_1k = float(pricing["web_search_per_1k"])
    search_avg_tokens = int(pricing.get("search_result_avg_tokens", 1500))
    calibration = float(pricing.get("cost_calibration_factor", 1.0))

    cached_prefix_tokens = _approx_tokens(inputs.cached_prefix_text)
    pack_suffix_tokens_each = [_approx_tokens(s) for s in inputs.pack_jsons_sample]
    avg_pack_suffix_tokens = sum(pack_suffix_tokens_each) // len(pack_suffix_tokens_each)
    user_overhead_tokens = max(1, int(inputs.user_message_overhead_chars / _CHARS_PER_TOKEN))
    avg_user_message_tokens = avg_pack_suffix_tokens + user_overhead_tokens

    avg_search_result_tokens = inputs.max_uses_web_search * search_avg_tokens

    n_calls = int(inputs.n_tickers)

    # Non-cached input per call = user message + search results streamed in
    avg_input_per_call = avg_user_message_tokens + avg_search_result_tokens
    non_cached_input_tokens = n_calls * avg_input_per_call

    output_tokens = n_calls * int(inputs.max_output_tokens)

    # Optimistic-cache model: 1 cache_create + (N-1) cache_reads. Reality
    # in batch mode (Anthropic processes batch with intra-batch cache
    # sharing); also reality in sync mode at concurrency=1.
    if n_calls > 0:
        cache_creation_tokens = cached_prefix_tokens
        cache_read_tokens = cached_prefix_tokens * (n_calls - 1)
    else:
        cache_creation_tokens = 0
        cache_read_tokens = 0

    # D21 sync-realistic cache model: with sync_concurrency = C, the first
    # min(N, C) calls all dispatch in parallel before any cache is warm →
    # each writes its own cache. Only the (N - C) calls that go out after
    # wave 1 can read the cache.
    sync_concurrency = max(1, int(inputs.sync_concurrency))
    if n_calls > 0:
        n_parallel_writers = min(n_calls, sync_concurrency)
        sync_cache_creation_tokens = cached_prefix_tokens * n_parallel_writers
        sync_cache_read_tokens = cached_prefix_tokens * max(0, n_calls - n_parallel_writers)
    else:
        sync_cache_creation_tokens = 0
        sync_cache_read_tokens = 0

    total_search_calls = n_calls * inputs.max_uses_web_search
    search_fee_upper_bound = (total_search_calls / 1000.0) * search_per_1k

    # --- A. no-optim ---
    no_optim_input_total = n_calls * cached_prefix_tokens + non_cached_input_tokens
    no_optim_token_cost = (
        (no_optim_input_total / 1_000_000.0) * in_per_mtok
        + (output_tokens / 1_000_000.0) * out_per_mtok
    )
    scen_no_optim = ScenarioCost(
        name="no-optim",
        input_tokens_total=no_optim_input_total,
        output_tokens_total=output_tokens,
        cache_read_tokens_total=0,
        cache_creation_tokens_total=0,
        token_cost_usd=no_optim_token_cost,
        search_fee_usd_upper_bound=search_fee_upper_bound,
        total_usd=(no_optim_token_cost + search_fee_upper_bound) * calibration,
    )

    # --- B. cache-only (sync-realistic, no batch discount) ---
    # D21: in sync mode at concurrency C, the first C calls all create
    # their own cache copy. Only calls in subsequent waves read the cache.
    cache_only_token_cost = (
        (sync_cache_creation_tokens / 1_000_000.0) * in_per_mtok * cache_create_mult
        + (sync_cache_read_tokens / 1_000_000.0) * in_per_mtok * cache_read_mult
        + (non_cached_input_tokens / 1_000_000.0) * in_per_mtok
        + (output_tokens / 1_000_000.0) * out_per_mtok
    )
    scen_cache_only = ScenarioCost(
        name="cache-only",
        input_tokens_total=non_cached_input_tokens + sync_cache_creation_tokens,
        output_tokens_total=output_tokens,
        cache_read_tokens_total=sync_cache_read_tokens,
        cache_creation_tokens_total=sync_cache_creation_tokens,
        token_cost_usd=cache_only_token_cost,
        search_fee_usd_upper_bound=search_fee_upper_bound,
        total_usd=(cache_only_token_cost + search_fee_upper_bound) * calibration,
    )

    # --- C. cache+batch (production default — optimistic cache, 50% discount) ---
    # In batch mode Anthropic processes intra-batch with cache reuse, so
    # 1 cache_create + (N-1) cache_reads is realistic. Also gets the 50%
    # batch discount on token costs (search fees are NOT discounted).
    cache_batch_token_cost = (
        (cache_creation_tokens / 1_000_000.0) * in_per_mtok * cache_create_mult
        + (cache_read_tokens / 1_000_000.0) * in_per_mtok * cache_read_mult
        + (non_cached_input_tokens / 1_000_000.0) * in_per_mtok
        + (output_tokens / 1_000_000.0) * out_per_mtok
    ) * batch_disc
    scen_cache_batch = ScenarioCost(
        name="cache+batch",
        input_tokens_total=non_cached_input_tokens + cache_creation_tokens,
        output_tokens_total=output_tokens,
        cache_read_tokens_total=cache_read_tokens,
        cache_creation_tokens_total=cache_creation_tokens,
        token_cost_usd=cache_batch_token_cost,
        search_fee_usd_upper_bound=search_fee_upper_bound,
        total_usd=(cache_batch_token_cost + search_fee_upper_bound) * calibration,
    )

    notes = [
        f"Token counts are heuristic (chars / {_CHARS_PER_TOKEN}); "
        f"actual Anthropic billing may differ ±10%.",
        f"Pricing values from module_7.yaml::pricing — verify against "
        f"Anthropic's pricing page before dispatch.",
        f"Web-search fees do not benefit from the 50% batch discount.",
        f"Search-result tokens estimated at {search_avg_tokens} tokens × max_uses "
        f"({inputs.max_uses_web_search}); paywalled domains return shorter content.",
        f"`cost_calibration_factor`={calibration:g} multiplies the FINAL "
        f"total of every scenario (empirical, see "
        f"memory:project_anthropic_cost_calibration).",
    ]

    return EstimateOutputs(
        snapshot_date=inputs.snapshot_date,
        prompt_version=inputs.prompt_version,
        model=inputs.model,
        cached_prefix_tokens=cached_prefix_tokens,
        avg_pack_suffix_tokens=avg_pack_suffix_tokens,
        avg_search_result_tokens=avg_search_result_tokens,
        n_tickers=inputs.n_tickers,
        n_api_calls=n_calls,
        scenarios=[scen_no_optim, scen_cache_only, scen_cache_batch],
        timestamp_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        notes=notes,
    )


# ─────────────────────── HTML renderer ─────────────────────────


def render_cost_html(out: EstimateOutputs, gates: dict, output_path: Path) -> None:
    """Self-contained HTML report. No JS deps. One file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def fmt_usd(x: float) -> str:
        if x >= 1000:
            return f"${x:,.0f}"
        if x >= 10:
            return f"${x:,.2f}"
        return f"${x:.4f}"

    def fmt_int(x: int) -> str:
        return f"{x:,}"

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
<title>M7 cost estimate {html.escape(out.snapshot_date)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Helvetica, Arial, sans-serif;
          max-width: 1100px; margin: 2em auto; padding: 0 1.5em;
          color: #222; background: #fafafa; line-height: 1.5; }}
  h1 {{ margin-bottom: 0.2em; }}
  .stamp {{ color: #888; font-size: 0.85em; margin-bottom: 2em; }}
  table {{ border-collapse: collapse; margin: 1em 0; width: 100%; background: #fff; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 10px; text-align: right; font-size: 0.95em; }}
  th {{ background: #f0f0f0; text-align: center; }}
  td:first-child, th:first-child {{ text-align: left; }}
  .summary {{ background: #fff8e1; border-left: 4px solid #f9a825;
              padding: 1em 1.5em; margin: 1.5em 0; }}
  .gates code {{ font-size: 0.85em; }}
  .notes {{ font-size: 0.85em; color: #555; }}
</style>
</head>
<body>
<h1>Module 7 — pre-flight cost estimate</h1>
<div class="stamp">
  Snapshot: <code>{html.escape(out.snapshot_date)}</code> &middot;
  Prompt version: <code>{html.escape(out.prompt_version)}</code> &middot;
  Model: <code>{html.escape(out.model)}</code> &middot;
  Generated: {html.escape(out.timestamp_utc)}
</div>

<div class="summary">
  <b>Feed size:</b> {fmt_int(out.n_tickers)} tickers ({fmt_int(out.n_api_calls)} API calls).<br>
  <b>Production cost (cache + batch):</b>
    {fmt_usd(out.scenarios[2].total_usd)}
</div>

<h2>Active gates</h2>
<div class="gates">{gate_lines}</div>

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
  <tr><td>Avg search-result tokens per call</td>
      <td>{fmt_int(out.avg_search_result_tokens)}</td></tr>
</table>

<h2>Notes</h2>
<div class="notes">{notes_html}</div>

</body></html>
"""
    output_path.write_text(html_doc, encoding="utf-8")
