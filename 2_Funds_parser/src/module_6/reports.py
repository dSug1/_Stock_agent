"""Module 6 reports — final ranking HTML/XLSX + LLM responses HTML.

Three render functions, each self-contained:

    render_final_ranking_html(rows, output_path)
        Sortable HTML table of final_rankings rows. No JS deps; uses native
        <table> with embedded CSS only.

    render_final_ranking_xlsx(rows, output_path)
        Excel with conditional formatting on final_score (green→white→red).

    render_llm_responses_html(score_rows, output_path)
        Per-ticker collapsible cards rendered from llm_scores.raw_text and
        the parsed research_brief_json. Lets user audit the model's actual
        text output for any ticker without leaving the browser.

All three project from already-written DB rows — they do NOT call the API.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Iterable

# Lazy import openpyxl only if XLSX is requested — keeps import cheap.


# ---------------------------------------------------------------------------
# Final ranking — HTML
# ---------------------------------------------------------------------------


def _fmt_usd(x) -> str:
    if x is None:
        return ""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return str(x)
    if abs(x) >= 1_000_000_000:
        return f"${x/1e9:,.2f}B"
    if abs(x) >= 1_000_000:
        return f"${x/1e6:,.1f}M"
    if abs(x) >= 100:
        return f"${x:,.0f}"
    return f"${x:,.2f}"


def _fmt_num(x, decimals: int = 2) -> str:
    if x is None:
        return ""
    try:
        return f"{float(x):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(x)


def render_final_ranking_html(
    rows: list[dict],
    output_path: Path,
    *,
    quarter: str,
    run_id: int,
) -> None:
    """Self-contained HTML; sortable via inline JS (one tiny click handler)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    body_rows = []
    for r in rows:
        # Highlight final_horizon
        h = r.get("final_horizon", "")
        h_class = {"3mo": "h3", "12mo": "h12", "either": "heither"}.get(h, "")
        score = r.get("final_score") or 0.0
        score_color = "#2e7d32" if score >= 5 else ("#c62828" if score < 0 else "#555")
        body_rows.append(
            f"<tr>"
            f"<td>{r.get('final_rank','')}</td>"
            f"<td><b>{html.escape(r.get('ticker',''))}</b></td>"
            f"<td>{html.escape(r.get('industry') or '')}</td>"
            f"<td class='{h_class}'>{h}</td>"
            f"<td style='color:{score_color}; font-weight:600'>{_fmt_num(r.get('final_score'))}</td>"
            f"<td>{_fmt_num(r.get('score_at_fair_3mo'))}</td>"
            f"<td>{_fmt_num(r.get('score_at_fair_12mo'))}</td>"
            f"<td>{_fmt_num(r.get('score_at_full_reward_3mo'))}</td>"
            f"<td>{_fmt_num(r.get('score_at_full_reward_12mo'))}</td>"
            f"<td>{_fmt_usd(r.get('fair_entry_low_usd'))} – {_fmt_usd(r.get('fair_entry_high_usd'))}</td>"
            f"<td>{_fmt_usd(r.get('full_reward_low_usd'))} – {_fmt_usd(r.get('full_reward_high_usd'))}</td>"
            f"<td>{_fmt_usd(r.get('target_price_3mo_usd'))}</td>"
            f"<td>{_fmt_usd(r.get('target_price_12mo_usd'))}</td>"
            f"<td>{_fmt_num(r.get('probability_3mo'))}</td>"
            f"<td>{_fmt_num(r.get('probability_12mo'))}</td>"
            f"<td>{_fmt_usd(r.get('rnpv_per_share_usd'))}</td>"
            f"<td>{_fmt_num(r.get('moat_score'))}</td>"
            f"<td>{_fmt_num(r.get('fda_pos_adjusted_lead'))}</td>"
            f"<td>{_fmt_usd(r.get('market_cap_usd'))}</td>"
            f"</tr>"
        )

    table_rows = "\n".join(body_rows) or "<tr><td colspan=19>(empty)</td></tr>"
    headers = (
        "Rank", "Ticker", "Industry", "Horizon", "Score (final, %/mo)",
        "Score 3mo @fair", "Score 12mo @fair",
        "Score 3mo @full", "Score 12mo @full",
        "Fair entry $ range", "Full-reward $ range",
        "Target 3mo $", "Target 12mo $",
        "Prob 3mo", "Prob 12mo",
        "rNPV/share $", "Moat", "FDA POS lead", "Market cap",
    )
    th_html = "".join(f"<th data-col='{i}'>{html.escape(h)}</th>" for i, h in enumerate(headers))

    html_doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>M6 final ranking {html.escape(quarter)}</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Helvetica, sans-serif;
         margin: 1em; color: #222; }}
 h1 {{ margin-bottom: 0; }}
 .stamp {{ color: #888; font-size: 0.85em; margin-bottom: 1.5em; }}
 table {{ border-collapse: collapse; font-size: 0.85em; width: 100%; }}
 th, td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: right;
          white-space: nowrap; }}
 th {{ background: #f0f0f0; cursor: pointer; user-select: none; text-align: center; }}
 th:hover {{ background: #e0e0e0; }}
 td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
 .h3   {{ background: #fff3e0; }}
 .h12  {{ background: #e3f2fd; }}
 .heither {{ background: #f3e5f5; }}
</style>
</head><body>
<h1>Module 6 — final ranking</h1>
<div class="stamp">Quarter <code>{html.escape(quarter)}</code> &middot;
 run_id <code>{run_id}</code> &middot;
 <a href="llm_responses_{html.escape(quarter)}.html">view LLM raw responses</a></div>
<table id="rank">
<thead><tr>{th_html}</tr></thead>
<tbody>{table_rows}</tbody>
</table>
<script>
// Tiny sortable: click header to toggle asc/desc.
document.querySelectorAll('#rank thead th').forEach(th => {{
  th.addEventListener('click', () => {{
    const tbody = th.closest('table').tBodies[0];
    const idx = +th.dataset.col;
    const asc = th.dataset.dir !== 'asc';
    th.dataset.dir = asc ? 'asc' : 'desc';
    Array.from(tbody.rows)
      .sort((a, b) => {{
        const av = a.cells[idx].innerText.replace(/[^-0-9.]/g, '');
        const bv = b.cells[idx].innerText.replace(/[^-0-9.]/g, '');
        const an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
        return asc ? a.cells[idx].innerText.localeCompare(b.cells[idx].innerText)
                   : b.cells[idx].innerText.localeCompare(a.cells[idx].innerText);
      }})
      .forEach(r => tbody.appendChild(r));
  }});
}});
</script>
</body></html>
"""
    output_path.write_text(html_doc, encoding="utf-8")


# ---------------------------------------------------------------------------
# Final ranking — XLSX
# ---------------------------------------------------------------------------


def render_final_ranking_xlsx(
    rows: list[dict],
    output_path: Path,
    *,
    quarter: str,
    run_id: int,
) -> None:
    """Excel workbook with one row per ticker. Conditional formatting on score."""
    from openpyxl import Workbook
    from openpyxl.formatting.rule import ColorScaleRule
    from openpyxl.styles import Font, PatternFill
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = f"M6 {quarter} run{run_id}"

    headers = (
        "rank", "ticker", "industry", "final_horizon", "final_score",
        "score_at_fair_3mo", "score_at_fair_12mo",
        "score_at_full_reward_3mo", "score_at_full_reward_12mo",
        "fair_entry_low_usd", "fair_entry_high_usd",
        "full_reward_low_usd", "full_reward_high_usd",
        "target_price_3mo_usd", "target_price_12mo_usd",
        "probability_3mo", "probability_12mo",
        "rnpv_per_share_usd", "moat_score",
        "fda_pos_adjusted_lead", "market_cap_usd", "fund_count",
    )
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")

    for r in rows:
        ws.append([
            r.get("final_rank"), r.get("ticker"), r.get("industry"),
            r.get("final_horizon"), r.get("final_score"),
            r.get("score_at_fair_3mo"), r.get("score_at_fair_12mo"),
            r.get("score_at_full_reward_3mo"), r.get("score_at_full_reward_12mo"),
            r.get("fair_entry_low_usd"), r.get("fair_entry_high_usd"),
            r.get("full_reward_low_usd"), r.get("full_reward_high_usd"),
            r.get("target_price_3mo_usd"), r.get("target_price_12mo_usd"),
            r.get("probability_3mo"), r.get("probability_12mo"),
            r.get("rnpv_per_share_usd"), r.get("moat_score"),
            r.get("fda_pos_adjusted_lead"), r.get("market_cap_usd"),
            r.get("fund_count"),
        ])
    ws.freeze_panes = "C2"
    if rows:
        last_row = len(rows) + 1
        # Conditional formatting on final_score column (E)
        rule = ColorScaleRule(
            start_type="num", start_value=-10, start_color="FF6B6B",
            mid_type="num", mid_value=0, mid_color="FFFFFF",
            end_type="num", end_value=20, end_color="6BCB77",
        )
        ws.conditional_formatting.add(f"E2:E{last_row}", rule)
    wb.save(output_path)


# ---------------------------------------------------------------------------
# LLM responses — HTML viewer
# ---------------------------------------------------------------------------


def render_llm_responses_html(
    score_rows: list[dict],
    output_path: Path,
    *,
    quarter: str,
    run_id: int,
) -> None:
    """One <details> card per ticker with raw_text + research_brief pretty-printed.

    `score_rows` is a list of dicts (one per (ticker, horizon) row); we group
    by ticker. Most fields come from llm_scores; both horizons share research
    brief and raw_text so we render them once per ticker.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Group rows by ticker (keep first-seen for shared per-ticker fields).
    by_ticker: dict[str, dict] = {}
    for r in score_rows:
        t = r["ticker"]
        if t not in by_ticker:
            by_ticker[t] = {"rows_by_horizon": {}, "shared": r}
        by_ticker[t]["rows_by_horizon"][r["horizon"]] = r

    cards = []
    for ticker, bundle in by_ticker.items():
        shared = bundle["shared"]
        rb_json = shared.get("research_brief_json") or "{}"
        try:
            rb = json.loads(rb_json)
            rb_pretty = json.dumps(rb, indent=2, ensure_ascii=False)
        except Exception:
            rb_pretty = rb_json

        h3 = bundle["rows_by_horizon"].get("3mo") or {}
        h12 = bundle["rows_by_horizon"].get("12mo") or {}

        def horizon_html(label: str, h: dict) -> str:
            if not h:
                return f"<div class='horizon empty'>{label}: (no row)</div>"
            return (
                f"<div class='horizon'><b>{label}</b><br>"
                f"target ${h.get('target_price_usd','?')} · "
                f"{h.get('time_to_catalyst_weeks','?')}w · "
                f"prob {h.get('probability','?')} · "
                f"score@fair {h.get('score_at_fair_pct_per_month','?'):.2f}<br>"
                f"<i>{html.escape((h.get('thesis_summary') or '')[:400])}</i>"
                f"</div>"
            )

        cards.append(
            f"<details><summary><b>{html.escape(ticker)}</b> &middot; "
            f"tier {html.escape(shared.get('source_tier') or '?')} &middot; "
            f"final score {shared.get('score_at_fair_pct_per_month', 0) or 0:.2f}</summary>"
            f"<div class='body'>"
            f"<div class='horizons'>{horizon_html('3mo', h3)}{horizon_html('12mo', h12)}</div>"
            f"<h4>research_brief</h4>"
            f"<pre>{html.escape(rb_pretty)}</pre>"
            f"<h4>raw LLM reply</h4>"
            f"<pre>{html.escape(shared.get('raw_text') or '')}</pre>"
            f"</div></details>"
        )

    html_doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>M6 LLM responses {html.escape(quarter)}</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Helvetica, sans-serif;
          max-width: 1200px; margin: 1em auto; padding: 0 1em; color: #222; }}
  details {{ border: 1px solid #ddd; border-radius: 6px;
             margin: 0.6em 0; padding: 0.6em 1em; background: #fff; }}
  summary {{ cursor: pointer; font-size: 1.05em; padding: 0.3em 0; }}
  .body {{ margin-top: 0.8em; }}
  .horizons {{ display: flex; gap: 1em; margin: 0.5em 0 1em; }}
  .horizon {{ flex: 1; background: #fafafa; padding: 0.6em 0.8em;
              border-radius: 4px; font-size: 0.9em; }}
  .horizon.empty {{ color: #aaa; font-style: italic; }}
  pre {{ background: #f5f5f5; padding: 0.8em; overflow-x: auto;
         font-size: 0.8em; border-radius: 4px; }}
  h4 {{ margin: 1em 0 0.3em; color: #555; }}
</style>
</head><body>
<h1>Module 6 — LLM responses ({html.escape(quarter)}, run {run_id})</h1>
<p style="color:#888">Click any ticker to expand. <code>raw_text</code> is the
exact reply Anthropic returned. <code>research_brief</code> is the parsed
JSON from that reply.</p>
{''.join(cards) or '<p>(no rows)</p>'}
</body></html>
"""
    output_path.write_text(html_doc, encoding="utf-8")
