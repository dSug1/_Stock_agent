"""Module 5 — user-facing HTML summary.

Reads from the built context_packs (the in-memory list emitted by the
orchestrator) and renders `Outputs/enrichment_report_{quarter}.html` with:

- Funnel counts (M4 rows → excluded → built).
- Archetype + best_horizon + sector + industry + market-cap histograms.
- Fund-flow rescue pool preview (D29) at illustrative D21 thresholds.
- Cache efficiency row (built / cache_hit / refreshed).
- First N packs inlined as collapsible previews.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any, Optional

import pandas as pd

log = logging.getLogger(__name__)


# ─── Market-cap buckets (match Module 4a's interactive prompt) ───────────────

_MCAP_BUCKETS: list[tuple[str, float, float]] = [
    ("<$100M",         0,                100_000_000),
    ("$100M–$500M",    100_000_000,      500_000_000),
    ("$500M–$1B",      500_000_000,    1_000_000_000),
    ("$1B–$2B",      1_000_000_000,    2_000_000_000),
    ("$2B–$3.7B",    2_000_000_000,    3_700_000_000),
    ("$3.7B–$5B",    3_700_000_000,    5_000_000_000),
    ("$5B–$7.5B",    5_000_000_000,    7_500_000_000),
    ("$7.5B–$10B",   7_500_000_000,   10_000_000_000),
    ("≥$10B",       10_000_000_000, float("inf")),
]


_COMPOSITE_BUCKETS: list[tuple[str, float, float]] = [
    ("≤0",      float("-inf"), 0.0001),
    ("0–3",            0,      3.0001),
    ("3–5",            3,      5.0001),
    ("5–6",            5,      6.0001),
    ("6–7",            6,      7.0001),
    ("7–9",            7,      9.0001),
    ("9–10",           9,     10.0001),
]


_TRAIN_LEFT_DEFAULT = {
    "extended_uptrend", "late_stage_extension", "broken_trend",
    "sustained_decline", "parabolic_blowoff",
}


def generate_enrichment_report_html(
    packs: list[dict],
    exclusions_df: pd.DataFrame,
    ranked_count: int,
    run_stats: dict,
    output_path: Path,
    quarter: str,
    *,
    preview_count: int = 10,
    rescue_thresholds: Optional[list[float]] = None,
    rescue_train_has_left: Optional[set[str]] = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    now_iso = dt.datetime.utcnow().isoformat(timespec="seconds")
    rescue_thresholds = rescue_thresholds or [5, 6, 7]
    train_left = rescue_train_has_left or _TRAIN_LEFT_DEFAULT

    if not packs:
        html_body = _render_empty(quarter, ranked_count, len(exclusions_df), now_iso)
        output_path.write_text(html_body, encoding="utf-8")
        return

    archetype_hist = Counter(p["archetype_verdict"].get("archetype", "") for p in packs)
    horizon_hist = Counter(p["archetype_verdict"].get("best_horizon", "") for p in packs)
    sector_hist = Counter(
        (p["identity"].get("sector") or "(none)") for p in packs
    )
    industry_hist = Counter(
        (p["identity"].get("industry") or "(none)") for p in packs
    )

    # Archetype ordering: by descending max composite_best observed in this
    # quarter's packs, so the strongest patterns appear at the top.
    archetype_max_comp: dict[str, float] = {}
    for p in packs:
        arch = p["archetype_verdict"].get("archetype", "")
        comp = p["archetype_verdict"].get("composite_best")
        if comp is None:
            continue
        cur = archetype_max_comp.get(arch)
        if cur is None or comp > cur:
            archetype_max_comp[arch] = float(comp)
    archetype_order = sorted(
        archetype_hist.keys(),
        key=lambda a: archetype_max_comp.get(a, float("-inf")),
        reverse=True,
    )

    mcap_vals = [
        p["market_snapshot"].get("market_cap_usd") for p in packs
        if p["market_snapshot"].get("market_cap_usd") is not None
    ]
    # Market-cap buckets ordered by descending cap (highest bucket on top).
    mcap_hist = list(reversed(_bucketize(mcap_vals, _MCAP_BUCKETS)))

    composite_vals = [
        p["archetype_verdict"].get("composite_best") for p in packs
        if p["archetype_verdict"].get("composite_best") is not None
    ]
    # Composite buckets ordered by descending composite_best.
    composite_hist = list(reversed(_bucketize(composite_vals, _COMPOSITE_BUCKETS)))

    # Rescue preview: highest threshold first (≥7 on top).
    rescue_table = _compute_rescue_preview(packs, rescue_thresholds, train_left)
    rescue_table = sorted(rescue_table, key=lambda r: r["threshold"], reverse=True)

    cache_counts = Counter(run_stats.get("cache_status_counts", {}))
    wall_s = run_stats.get("wall_seconds")
    db_size_mb = run_stats.get("db_size_mb")

    previews_html = _render_previews(packs[:preview_count])

    body = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'>
<title>Module 5 Enrichment — {html.escape(quarter)}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 1.5rem; color: #222; max-width: 1100px; }}
  h1 {{ margin-bottom: 0.2rem; }}
  h2 {{ margin-top: 1.6rem; margin-bottom: 0.4rem; font-size: 1.1rem; color: #333; }}
  .meta {{ color: #666; font-size: 0.85rem; margin-bottom: 1rem; }}
  .counts {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
  .counts div {{ padding: 0.4rem 0.7rem; background: #f7f7f7; border: 1px solid #e0e0e0;
                 border-radius: 6px; font-size: 0.85rem; }}
  table {{ border-collapse: collapse; font-size: 0.85rem; margin: 0.4rem 0; }}
  th, td {{ padding: 3px 10px; border-bottom: 1px solid #eee; text-align: left; }}
  th {{ background: #fafafa; font-weight: 600; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .bar {{ display: inline-block; background: #4a90e2; height: 10px; vertical-align: middle; }}
  .bar-rescue {{ background: #e2994a; }}
  details {{ margin: 0.4rem 0; background: #fafafa; border: 1px solid #e0e0e0;
             border-radius: 6px; padding: 0.4rem 0.7rem; }}
  details summary {{ cursor: pointer; font-weight: 600; }}
  pre {{ white-space: pre-wrap; word-break: break-word; font-size: 0.8rem;
         background: #fff; border: 1px solid #e0e0e0; padding: 0.5rem; border-radius: 4px; }}
  .note {{ font-size: 0.8rem; color: #777; margin-top: 0.2rem; }}
</style></head><body>

<h1>Module 5 — Enrichment report</h1>
<div class='meta'>Quarter {html.escape(quarter)} · built {html.escape(now_iso)} UTC · pack_version {html.escape(run_stats.get("pack_version", ""))}</div>

<h2>Counts</h2>
<div class='counts'>
  <div><b>{ranked_count}</b> ranked (M4b input)</div>
  <div><b>{len(exclusions_df)}</b> excluded</div>
  <div><b>{len(packs)}</b> packs in DB</div>
  <div>cache: built <b>{cache_counts.get('built', 0)}</b> · hit <b>{cache_counts.get('cache_hit', 0)}</b> · refreshed <b>{cache_counts.get('refreshed', 0)}</b></div>
  <div>wall: <b>{(f'{wall_s:.1f} s' if wall_s is not None else 'n/a')}</b></div>
  <div>DB: <b>{(f'{db_size_mb:.2f} MB' if db_size_mb is not None else 'n/a')}</b></div>
</div>

<h2>composite_best distribution <span class='note'>(informs D21 threshold)</span></h2>
{_render_hist_table(composite_hist, total=len(packs))}

<h2>Archetype distribution <span class='note'>(ordered by descending max composite_best)</span></h2>
{_render_counter_table(archetype_hist, total=len(packs), ordered_keys=archetype_order)}

<h2>best_horizon split</h2>
{_render_counter_table(horizon_hist, total=len(packs))}

<h2>Sector distribution <span class='note'>(informs D27 allowlist)</span></h2>
{_render_counter_table(sector_hist, total=len(packs), top_n=20)}

<h2>Industry distribution <span class='note'>(informs D27 finer-grained allowlist)</span></h2>
{_render_counter_table(industry_hist, total=len(packs), top_n=20)}

<h2>Market-cap distribution <span class='note'>(informs D28 cap)</span></h2>
{_render_hist_table(mcap_hist, total=len(packs))}

<h2>Fund-flow rescue pool preview <span class='note'>(D29 — at illustrative D21 thresholds)</span></h2>
{_render_rescue_table(rescue_table)}

<h2>Pack previews (first {min(preview_count, len(packs))})</h2>
{previews_html}

</body></html>"""

    output_path.write_text(body, encoding="utf-8")


# ─── Histogram helpers ───────────────────────────────────────────────────────

def _bucketize(
    values: list[float], buckets: list[tuple[str, float, float]],
) -> list[tuple[str, int]]:
    counts = [0] * len(buckets)
    for v in values:
        for i, (_, lo, hi) in enumerate(buckets):
            if lo <= float(v) < hi:
                counts[i] += 1
                break
    return [(label, c) for (label, _, _), c in zip(buckets, counts)]


def _render_hist_table(rows: list[tuple[str, int]], *, total: int) -> str:
    max_c = max((c for _, c in rows), default=1) or 1
    body = []
    body.append("<table><thead><tr><th>Bucket</th><th class='num'>Count</th><th class='num'>%</th><th></th></tr></thead><tbody>")
    for label, c in rows:
        pct = 100.0 * c / total if total else 0.0
        width = int(200 * c / max_c)
        body.append(
            f"<tr><td>{html.escape(label)}</td>"
            f"<td class='num'>{c}</td>"
            f"<td class='num'>{pct:.1f}%</td>"
            f"<td><span class='bar' style='width:{width}px'></span></td></tr>"
        )
    body.append("</tbody></table>")
    return "".join(body)


def _render_counter_table(
    counter: Counter,
    *,
    total: int,
    top_n: Optional[int] = None,
    ordered_keys: Optional[list[str]] = None,
) -> str:
    if ordered_keys is not None:
        # Caller-supplied ordering (e.g. archetype by descending composite score).
        items = [(k, counter.get(k, 0)) for k in ordered_keys if counter.get(k, 0) > 0]
        if top_n is not None:
            items = items[:top_n]
    else:
        items = counter.most_common(top_n) if top_n else counter.most_common()
    max_c = max((c for _, c in items), default=1) or 1
    body = []
    body.append("<table><thead><tr><th>Name</th><th class='num'>Count</th><th class='num'>%</th><th></th></tr></thead><tbody>")
    for name, c in items:
        pct = 100.0 * c / total if total else 0.0
        width = int(200 * c / max_c)
        body.append(
            f"<tr><td>{html.escape(str(name))}</td>"
            f"<td class='num'>{c}</td>"
            f"<td class='num'>{pct:.1f}%</td>"
            f"<td><span class='bar' style='width:{width}px'></span></td></tr>"
        )
    body.append("</tbody></table>")
    if top_n and len(counter) > top_n:
        body.append(f"<div class='note'>showing top {top_n} of {len(counter)}</div>")
    return "".join(body)


# ─── Rescue preview (D29) ────────────────────────────────────────────────────

def _compute_rescue_preview(
    packs: list[dict], thresholds: list[float], train_left: set[str],
) -> list[dict]:
    """For each illustrative D21 threshold, count how many pass outright vs.
    how many are rescued by D29 under the default rule."""
    rows = []
    for thr in thresholds:
        pass_count = 0
        rescue_count = 0
        rescue_arch_counts: Counter = Counter()
        for p in packs:
            comp = p["archetype_verdict"].get("composite_best")
            arch = p["archetype_verdict"].get("archetype") or ""
            new_pos = p["fund_accumulation"].get("new_positions") or 0
            qoq = p["fund_accumulation"].get("qoq_fund_count_change") or 0
            if comp is None:
                continue
            if comp >= thr:
                pass_count += 1
                continue
            if arch in train_left:
                continue
            if new_pos >= 1 or qoq >= 1:
                rescue_count += 1
                rescue_arch_counts[arch] += 1
        rows.append({
            "threshold": thr,
            "pass": pass_count,
            "rescued": rescue_count,
            "total": pass_count + rescue_count,
            "by_archetype": dict(rescue_arch_counts.most_common(6)),
        })
    return rows


def _render_rescue_table(rows: list[dict]) -> str:
    body = []
    body.append(
        "<table><thead><tr>"
        "<th>D21 threshold</th><th class='num'>Pass outright</th>"
        "<th class='num'>Rescued by D29</th><th class='num'>Total to M6</th>"
        "<th>Rescued archetypes (top)</th>"
        "</tr></thead><tbody>"
    )
    for r in rows:
        arch_str = ", ".join(f"{k}={v}" for k, v in r["by_archetype"].items())
        body.append(
            f"<tr><td class='num'>≥{r['threshold']}</td>"
            f"<td class='num'>{r['pass']}</td>"
            f"<td class='num'>{r['rescued']}</td>"
            f"<td class='num'>{r['total']}</td>"
            f"<td>{html.escape(arch_str)}</td></tr>"
        )
    body.append("</tbody></table>")
    body.append(
        "<div class='note'>Rule: <code>composite_best &lt; threshold AND archetype NOT IN "
        "train_has_left AND (new_positions ≥ 1 OR qoq_fund_count_change ≥ 1)</code>. "
        "D27/D28 filters (sector, market cap) not applied here — they narrow the pool further at Module 6 query time.</div>"
    )
    return "".join(body)


# ─── Pack previews ───────────────────────────────────────────────────────────

def _render_previews(packs: list[dict]) -> str:
    blocks = []
    for p in packs:
        ident = p.get("identity", {})
        verdict = p.get("archetype_verdict", {})
        headline = (
            f"{ident.get('ticker', '?')} — {ident.get('name_of_issuer', '')} "
            f"[{verdict.get('archetype', '')}] "
            f"composite_best={_fmt(verdict.get('composite_best'))}, "
            f"best_horizon={verdict.get('best_horizon', '')}"
        )
        pretty = json.dumps(p, indent=2, sort_keys=True)
        blocks.append(
            f"<details><summary>{html.escape(headline)}</summary>"
            f"<pre>{html.escape(pretty)}</pre></details>"
        )
    return "".join(blocks) if blocks else "<p>(none)</p>"


def _render_empty(
    quarter: str, ranked_count: int, excluded_count: int, now_iso: str,
) -> str:
    return f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'>
<title>Module 5 Enrichment — {html.escape(quarter)}</title></head><body style='font-family: system-ui, sans-serif; margin: 2rem;'>
<h1>Module 5 — Enrichment report</h1>
<p>Quarter <b>{html.escape(quarter)}</b> · built {html.escape(now_iso)} UTC</p>
<p>No context packs produced. {ranked_count} ranked rows in M4b input; {excluded_count} excluded.</p>
<p>Possible causes: ranked parquet empty, all rows in denylist, or <code>include_unclassified=false</code> with no classified tickers.</p>
</body></html>"""


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)
