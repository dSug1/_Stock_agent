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


_WEEKS_PER_MONTH = 4.33


def _months_to_catalyst(r: dict) -> float | None:
    """Return the months value used by the score formula at the row's
    final_horizon: ``max(1.0, time_to_catalyst_weeks / 4.33)``.

    For ``final_horizon == 'either'``, picks the smaller weeks value (the
    sooner catalyst — more conservative interpretation).
    """
    fh = r.get("final_horizon")
    if fh == "3mo":
        weeks = r.get("time_to_catalyst_3mo_weeks")
    elif fh == "12mo":
        weeks = r.get("time_to_catalyst_12mo_weeks")
    else:                                                   # "either" or unknown
        candidates = [w for w in (r.get("time_to_catalyst_3mo_weeks"),
                                  r.get("time_to_catalyst_12mo_weeks")) if w]
        weeks = min(candidates) if candidates else None
    if weeks is None:
        return None
    return max(1.0, float(weeks) / _WEEKS_PER_MONTH)


def _build_detail_row(
    ticker: str,
    by_ticker_horizon: dict,
    colspan: int,
    final_horizon: str = "either",
) -> str:
    """Build the inline detail panel <tr> for one ticker.

    Renders only the horizon card matching ``final_horizon`` (3mo or 12mo).
    When ``final_horizon == 'either'`` (model couldn't decide) both are shown.
    """
    h3 = by_ticker_horizon.get((ticker, "3mo")) or {}
    h12 = by_ticker_horizon.get((ticker, "12mo")) or {}
    shared = h3 or h12 or {}

    # Pretty-print research_brief
    rb_json = shared.get("research_brief_json") or "{}"
    try:
        rb_pretty = json.dumps(json.loads(rb_json), indent=2, ensure_ascii=False)
    except Exception:
        rb_pretty = rb_json

    raw_text = shared.get("raw_text") or ""

    def horizon_card(label: str, h: dict) -> str:
        if not h:
            return f"<div class='horizon empty'>{label}: (no row)</div>"
        current = h.get("current_price_at_scoring_usd")
        current_str = f"${current:.2f}" if current else "n/a"
        score_curr = h.get("score_at_current_pct_per_month")
        score_curr_str = f"{score_curr:.2f}" if score_curr is not None else "n/a"
        return (
            f"<div class='horizon'><b>{label}</b><br>"
            f"target ${h.get('target_price_usd','?')} &middot; "
            f"{h.get('time_to_catalyst_weeks','?')}w &middot; "
            f"prob {h.get('probability','?')} &middot; "
            f"current {current_str}<br>"
            f"<b>score@current {score_curr_str} %/mo</b><br>"
            f"<i>{html.escape((h.get('thesis_summary') or '')[:600])}</i>"
            f"</div>"
        )

    # Only render the horizon card matching the row's final_horizon (or both
    # when "either"). Keeps the panel focused on the actual recommended horizon.
    if final_horizon == "3mo":
        horizons_html = horizon_card("3mo", h3)
    elif final_horizon == "12mo":
        horizons_html = horizon_card("12mo", h12)
    else:                                                       # 'either' or unknown
        horizons_html = horizon_card("3mo", h3) + horizon_card("12mo", h12)

    return (
        f"<tr class='detail-row' data-for='{html.escape(ticker)}' "
        f"id='detail-{html.escape(ticker)}' style='display:none'>"
        f"<td colspan='{colspan}'>"
        f"<div class='detail-panel'>"
        f"<div class='horizons'>{horizons_html}</div>"
        # Per user request 2026-04-25: raw LLM reply first (the primary
        # human-readable output), then research_brief (parsed JSON, more
        # for audit / debug).
        f"<details open><summary>raw LLM reply</summary>"
        f"<pre>{html.escape(raw_text)}</pre></details>"
        f"<details><summary>research_brief (parsed JSON)</summary>"
        f"<pre>{html.escape(rb_pretty)}</pre></details>"
        f"</div></td></tr>"
    )


# D47 / D50 — composite score modifier component order. Mirrored in
# module_6b.modifiers.COMPONENT_NAMES and in the JS embedded below.
_COMPONENT_NAMES: tuple[str, ...] = (
    "crowding", "financing", "dilution", "insider", "mgmt",
    "acquisition", "moat", "failures", "concentration",
)
_DEFAULT_WEIGHTS: dict[str, float] = {n: 1.0 for n in _COMPONENT_NAMES}


def render_final_ranking_html(
    rows: list[dict],
    output_path: Path,
    *,
    quarter: str,
    run_id: int,
    score_rows: list[dict] | None = None,
    prior_selection: set[str] | None = None,
    modifier_weights: dict[str, float] | None = None,
    ticker_factors: dict[str, dict] | None = None,
    modifier_cfg: dict | None = None,
) -> None:
    """Self-contained HTML.

    - Sortable via inline JS (one click handler).
    - When ``score_rows`` is provided, each ticker row carries an expand arrow
      that reveals an inline detail panel with the LLM response (per-horizon
      mini-cards + research_brief JSON + raw_text). This is the merged view
      that replaces the standalone llm_responses_*.html.
    - Per-ticker checkboxes (Module 6b Part (b), D48). Default all checked.
      When ``prior_selection`` is provided (re-render after a selective
      dispatch), only tickers in that set are emitted with ``checked`` so
      the user's selection survives round-trips.
    - D47 / D50 — composite score modifier with per-component user weight
      sliders. ``ticker_factors`` is ``{ticker: {"factors": {comp: model_factor}, ...}}``
      from ``module_6b.collect_ticker_factors``. ``modifier_weights`` is the
      initial slider state (defaults to all-1.0). ``modifier_cfg`` is the
      parsed ``config/scoring_modifier.yaml`` (used for slider UI bounds +
      component labels). When all three are None, the modifier columns
      still render with neutral 1.0 values and the slider menu is omitted.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Index llm_scores rows by (ticker, horizon) so the detail panel can pull
    # research_brief_json + raw_text + per-horizon scoring fields.
    by_ticker_horizon: dict[tuple[str, str], dict] = {}
    if score_rows:
        for sr in score_rows:
            by_ticker_horizon[(sr["ticker"], sr["horizon"])] = sr

    # ── D47 / D50 — modifier setup ──────────────────────────────────────
    # `ticker_factors` is the per-ticker MODEL factors blob. Treat None
    # as empty so legacy callers still produce a valid HTML.
    tf_map: dict[str, dict] = ticker_factors or {}
    weights = dict(_DEFAULT_WEIGHTS)
    if modifier_weights:
        for k in _COMPONENT_NAMES:
            v = modifier_weights.get(k)
            if isinstance(v, (int, float)):
                weights[k] = float(v)
    cfg = modifier_cfg or {}
    bounds = cfg.get("bounds") or {"min": 0.5, "max": 1.5}
    weights_ui = cfg.get("weights_ui") or {
        "min": 0.0, "max": 2.0, "step": 0.05, "default": 1.0,
    }
    component_labels = {
        c: (cfg.get("components", {}).get(c, {}).get("label") or c.title())
        for c in _COMPONENT_NAMES
    }

    def _initial_modifier(ticker: str) -> float:
        """Return the initial weighted modifier for this row.

        Mirrors the JS-side compute exactly so server- and client-side
        modifiers are bit-identical for any given (factors, weights, bounds).
        """
        info = tf_map.get(ticker) or {}
        factors = info.get("factors") or {}
        prod = 1.0
        for c in _COMPONENT_NAMES:
            f = float(factors.get(c, 1.0))
            w = float(weights.get(c, 1.0))
            prod *= 1.0 + w * (f - 1.0)
        return max(float(bounds.get("min", 0.5)),
                   min(float(bounds.get("max", 1.5)), prod))

    body_rows = []
    for r in rows:
        # Highlight final_horizon
        h = r.get("final_horizon", "")
        h_class = {"3mo": "h3", "12mo": "h12", "either": "heither"}.get(h, "")
        score = r.get("final_score") or 0.0
        score_color = "#2e7d32" if score >= 5 else ("#c62828" if score < 0 else "#555")

        # D46 — positioning gap colour: well above fair → orange "wait" warning
        gap = r.get("current_vs_fair_mid_pct")
        gap_class = ""
        if gap is not None:
            if gap > 50: gap_class = "gap_high"
            elif gap > 0: gap_class = "gap_pos"
            else: gap_class = "gap_neg"

        ticker = r.get("ticker", "") or ""

        # D48 — per-ticker selection checkbox. Default checked unless a prior
        # selection set is supplied and this ticker isn't in it.
        is_checked = (prior_selection is None) or (ticker in prior_selection)
        checked_attr = " checked" if is_checked else ""
        checkbox_cell = (
            f"<td class='no-sort'><input type='checkbox' name='ticker_select' "
            f"value='{html.escape(ticker)}' data-ticker='{html.escape(ticker)}'"
            f"{checked_attr}></td>"
        )

        # Expand arrow when score_rows are loaded
        if score_rows:
            arrow_cell = (
                f"<td class='no-sort'><button class='expand-toggle' "
                f"data-target='detail-{html.escape(ticker)}' "
                f"aria-label='Toggle details'>&#9654;</button></td>"
            )
        else:
            arrow_cell = "<td class='no-sort'></td>"

        months = _months_to_catalyst(r)
        # D47 / D50 — modifier + adjusted score (initial values; JS recomputes
        # live as the user moves sliders). Stamped on data-* attrs so the
        # JS can re-sort by adjusted score without re-parsing cells.
        base_score = r.get("final_score") or 0.0
        modifier_val = _initial_modifier(ticker)
        adjusted_val = float(base_score) * modifier_val
        mod_class = (
            "mod_strong_neg" if modifier_val < 0.85 else
            "mod_neg"        if modifier_val < 0.95 else
            "mod_pos"        if modifier_val > 1.20 else
            "mod_mild_pos"   if modifier_val > 1.05 else
            "mod_neutral"
        )
        body_rows.append(
            f"<tr class='main-row' data-ticker='{html.escape(ticker)}' "
            f"data-base-score='{float(base_score):.6f}' "
            f"data-adjusted='{adjusted_val:.6f}'>"
            f"{checkbox_cell}"
            f"{arrow_cell}"
            f"<td class='rank-cell'>{r.get('final_rank','')}</td>"
            f"<td><b>{html.escape(ticker)}</b></td>"
            f"<td>{_fmt_usd(r.get('current_price_at_scoring_usd'))}</td>"
            f"<td>{_fmt_usd(r.get('fair_entry_low_usd'))} – {_fmt_usd(r.get('fair_entry_high_usd'))}</td>"
            f"<td class='{gap_class}'>{_fmt_num(gap, 1) + '%' if gap is not None else ''}</td>"
            f"<td class='{h_class}'>{h}</td>"
            f"<td style='color:{score_color}; font-weight:600'>{_fmt_num(r.get('final_score'))}</td>"
            f"<td class='modifier-cell {mod_class}'>{modifier_val:.3f}</td>"
            f"<td class='adjusted-cell' style='font-weight:600'>{adjusted_val:.2f}</td>"
            f"<td>{_fmt_num(months) if months is not None else ''}</td>"
            f"<td>{_fmt_num(r.get('score_at_current_3mo'))}</td>"
            f"<td>{_fmt_num(r.get('score_at_current_12mo'))}</td>"
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

        # Detail row — emitted only when score_rows is provided. Filter by the
        # row's final_horizon so the user sees only the recommended horizon's
        # thesis (per user request 2026-04-25).
        if score_rows:
            body_rows.append(
                _build_detail_row(
                    ticker, by_ticker_horizon,
                    colspan=22, final_horizon=h or "either",
                )
            )

    table_rows = "\n".join(body_rows) or "<tr><td colspan=22>(empty)</td></tr>"
    headers = (
        "__checkbox__",                                       # D48 — selection column
        "",                                                    # arrow column
        "Rank", "Ticker",
        "Current $",                                          # D46
        "Fair entry $ range",
        "Current vs fair-mid %",                              # D46 — positioning gap
        "Horizon",
        "Score (PRIMARY, %/mo)",                              # D46 — score_at_current at final_horizon
        "× Modifier",                                         # D47 / D50
        "= Adjusted (%/mo)",                                  # D47 / D50 — ranking key
        "Months to catalyst",                                 # max(1.0, weeks/4.33) at final_horizon
        "Score 3mo @current", "Score 12mo @current",          # D46 — primary
        "Target 3mo $", "Target 12mo $",
        "Prob 3mo", "Prob 12mo",
        "rNPV/share $", "Moat", "FDA POS lead", "Market cap",
    )
    th_parts = []
    for i, h in enumerate(headers):
        if h == "__checkbox__":
            # Master select-all defaults to checked; JS recomputes indeterminate
            # state on load to reflect any unchecked rows from prior_selection.
            th_parts.append(
                "<th class='no-sort'><input type='checkbox' id='select-all' "
                "checked title='Select / deselect all'></th>"
            )
        elif h == "":
            th_parts.append("<th class='no-sort'></th>")
        else:
            th_parts.append(f"<th data-col='{i}'>{html.escape(h)}</th>")
    th_html = "".join(th_parts)

    # Counts for the toolbar badge.
    total_count = len(rows)
    initial_checked = (
        sum(1 for r in rows if (prior_selection is None)
            or (r.get("ticker") in prior_selection))
    )
    cli_command = (
        f"python scripts/6_score.py --selection-from-html "
        f"Outputs/final_ranking_{quarter}.html -v"
    )

    # Constants injected into the JS.
    quarter_js = json.dumps(quarter)
    cli_command_js = json.dumps(cli_command)
    serve_command = (
        f"python scripts/6_serve_report.py --quarter {quarter}"
    )
    serve_command_js = json.dumps(serve_command)

    # D47 / D50 — embed the per-ticker model factors + initial weights +
    # bounds + component metadata. The JS reads this on load and on every
    # slider change to recompute modifier × base_score per row, then re-sort.
    show_modifier_panel = bool(tf_map)
    factors_for_js: dict[str, dict[str, float]] = {}
    for tk, info in tf_map.items():
        f = (info or {}).get("factors") or {}
        factors_for_js[tk] = {c: float(f.get(c, 1.0)) for c in _COMPONENT_NAMES}
    modifier_data_js = json.dumps({
        "components": list(_COMPONENT_NAMES),
        "labels":     component_labels,
        "weights":    {c: float(weights.get(c, 1.0)) for c in _COMPONENT_NAMES},
        "weights_ui": {
            "min":     float(weights_ui.get("min", 0.0)),
            "max":     float(weights_ui.get("max", 2.0)),
            "step":    float(weights_ui.get("step", 0.05)),
            "default": float(weights_ui.get("default", 1.0)),
        },
        "bounds": {
            "min": float(bounds.get("min", 0.5)),
            "max": float(bounds.get("max", 1.5)),
        },
        "ticker_factors": factors_for_js,
    })

    # Hamburger button + slide-out panel HTML, only when we have factors.
    if show_modifier_panel:
        slider_rows: list[str] = []
        for c in _COMPONENT_NAMES:
            label = html.escape(component_labels[c])
            init = float(weights.get(c, 1.0))
            slider_rows.append(
                f"<div class='mw-row' data-component='{html.escape(c)}'>"
                f"  <label class='mw-label' title='{html.escape(c)}'>{label}</label>"
                f"  <input type='range' class='mw-slider' "
                f"         min='{float(weights_ui.get('min', 0.0))}' "
                f"         max='{float(weights_ui.get('max', 2.0))}' "
                f"         step='{float(weights_ui.get('step', 0.05))}' "
                f"         value='{init:.2f}' />"
                f"  <span class='mw-value' aria-label='Current weight'>{init:.2f}</span>"
                f"  <button type='button' class='mw-reset' title='Reset to 1.0'>↺</button>"
                f"</div>"
            )
        modifier_panel_html = (
            "<div id='mw-overlay' class='mw-overlay' style='display:none' aria-hidden='true'></div>"
            "<aside id='mw-drawer' class='mw-drawer' style='display:none' aria-hidden='true' "
            "       aria-label='Score modifier weights'>"
            "  <div class='mw-header'>"
            "    <h3>Modifier weights</h3>"
            "    <button type='button' id='mw-close' class='mw-x' aria-label='Close'>×</button>"
            "  </div>"
            "  <p class='mw-help'>"
            "    Per-component weights applied to the model's modifier. "
            "    <b>1.00</b> = use model factor as-is &middot; "
            "    <b>0</b> = disable that component &middot; "
            "    <b>2.00</b> = double its deviation from neutral. "
            "    Effective factor = 1 + weight × (model − 1)."
            "  </p>"
            "  <div class='mw-actions'>"
            "    <button type='button' id='mw-reset-all' class='mw-reset-all'>Reset all to 1.00</button>"
            "    <button type='button' id='mw-disable-all' class='mw-disable-all' "
            "            title='Set every weight to 0 — disables all components (ranking reverts to raw score)'>Disable all (0.00)</button>"
            "  </div>"
            "  <div class='mw-rows'>"
            + "".join(slider_rows) +
            "  </div>"
            "</aside>"
        )
        hamburger_button = (
            "<button id='mw-toggle' class='mw-toggle' "
            "        title='Open modifier weights' aria-label='Open modifier weights'>≡</button>"
        )
    else:
        modifier_panel_html = ""
        hamburger_button = ""

    html_doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><title>M6 final ranking {html.escape(quarter)}</title>
<style>
 body {{ font-family: -apple-system, Segoe UI, Helvetica, sans-serif;
         margin: 1em; color: #222; }}
 h1 {{ margin-bottom: 0; }}
 .stamp {{ color: #888; font-size: 0.85em; margin-bottom: 1em; }}

 /* D48 — selection toolbar */
 .toolbar {{ display: flex; align-items: center; gap: 0.6em;
             margin: 0.6em 0 1em; padding: 0.5em 0.8em;
             background: #f7faff; border: 1px solid #cfe0f2;
             border-radius: 6px; flex-wrap: wrap; font-size: 0.9em; }}
 .toolbar button {{ padding: 4px 10px; border: 1px solid #5b8def;
                    background: #fff; color: #1a4fa0; border-radius: 4px;
                    cursor: pointer; font-size: 0.92em; }}
 .toolbar button:hover {{ background: #e6f0ff; }}
 .toolbar button.primary {{ background: #1976d2; color: #fff; border-color: #1976d2; }}
 .toolbar button.primary:hover {{ background: #1565c0; }}
 .toolbar .badge {{ background: #1976d2; color: #fff; padding: 2px 8px;
                    border-radius: 12px; font-weight: 600; }}
 .toolbar .badge.partial {{ background: #ed6c02; }}
 .toolbar .badge.empty   {{ background: #757575; }}
 .toolbar .toast {{ background: #2e7d32; color: #fff; padding: 4px 10px;
                    border-radius: 4px; opacity: 0; transition: opacity 0.3s;
                    font-size: 0.85em; }}
 .toolbar .toast.show {{ opacity: 1; }}
 .toolbar .toast.error {{ background: #c62828; }}
 .toolbar .hint {{ color: #555; font-size: 0.85em; }}

 /* file:// banner — only shown when no HTTP server is in front of us */
 .serve-banner {{ background: #fff3e0; border: 1px solid #ed6c02;
                   border-radius: 6px; padding: 0.6em 0.9em; margin: 0.6em 0;
                   font-size: 0.9em; color: #5b3700; }}
 .serve-banner code {{ background: #fff; padding: 1px 6px; border-radius: 3px;
                        border: 1px solid #d4a25c; font-size: 0.85em; }}
 .serve-banner a {{ color: #1565c0; }}

 /* D47 / D50 — modifier cell colour bands + slider drawer */
 td.modifier-cell {{ font-weight: 600; }}
 .mod_strong_neg {{ background: #ffcdd2; color: #b71c1c; }}      /* < 0.85 */
 .mod_neg        {{ background: #ffe0b2; color: #b25500; }}      /* 0.85–0.95 */
 .mod_neutral    {{ background: #fafafa; color: #555; }}         /* 0.95–1.05 */
 .mod_mild_pos   {{ background: #c8e6c9; color: #1b5e20; }}      /* 1.05–1.20 */
 .mod_pos        {{ background: #66bb6a; color: #fff; }}         /* > 1.20 */

 /* Hamburger button (in toolbar). */
 .mw-toggle {{ background: #1976d2; color: #fff; border: 1px solid #1976d2;
               border-radius: 4px; padding: 2px 12px; font-size: 1.15em;
               line-height: 1.1; cursor: pointer; }}
 .mw-toggle:hover {{ background: #1565c0; }}

 /* Slide-out drawer */
 .mw-overlay {{ position: fixed; inset: 0; background: rgba(0,0,0,0.18);
                z-index: 90; }}
 .mw-drawer  {{ position: fixed; top: 0; right: 0; height: 100vh;
                width: 380px; max-width: 95vw; background: #fff;
                border-left: 1px solid #c4d4e6;
                box-shadow: -4px 0 18px rgba(0,0,0,0.10);
                padding: 1em 1.2em 1.5em; overflow-y: auto;
                z-index: 100; font-size: 0.92em; box-sizing: border-box; }}
 .mw-header  {{ display: flex; align-items: center; justify-content: space-between;
                margin-bottom: 0.4em; }}
 .mw-header h3 {{ margin: 0; font-size: 1.05em; color: #1a4fa0; }}
 .mw-x {{ background: none; border: none; cursor: pointer; font-size: 1.4em;
          color: #555; padding: 0 6px; }}
 .mw-x:hover {{ color: #000; }}
 .mw-help {{ font-size: 0.83em; color: #555; margin: 0.4em 0 0.8em;
             line-height: 1.4; }}
 .mw-actions {{ margin-bottom: 0.6em; }}
 .mw-actions {{ display: flex; gap: 0.4em; flex-wrap: wrap; }}
 .mw-reset-all, .mw-disable-all {{ border-radius: 4px; padding: 4px 10px;
                                    cursor: pointer; font-size: 0.9em; }}
 .mw-reset-all {{ background: #fff; border: 1px solid #999; color: #333; }}
 .mw-reset-all:hover {{ background: #f0f0f0; }}
 .mw-disable-all {{ background: #fff; border: 1px solid #c62828; color: #b71c1c; }}
 .mw-disable-all:hover {{ background: #ffebee; }}
 .mw-rows {{ display: flex; flex-direction: column; gap: 0.4em; }}
 .mw-row  {{ display: grid;
             grid-template-columns: 1fr 130px 44px 28px;
             align-items: center; gap: 0.4em;
             padding: 0.3em 0.4em; border: 1px solid #eee;
             border-radius: 4px; background: #fafafa; }}
 .mw-row.dirty {{ background: #fff8e1; border-color: #f4c542; }}
 .mw-label {{ font-size: 0.88em; color: #333; }}
 .mw-slider {{ width: 100%; }}
 .mw-value  {{ font-variant-numeric: tabular-nums; text-align: right;
               font-weight: 600; color: #1a4fa0; }}
 .mw-row.dirty .mw-value {{ color: #b25500; }}
 .mw-reset {{ background: none; border: 1px solid #aaa; border-radius: 3px;
              cursor: pointer; color: #555; font-size: 0.95em;
              line-height: 1; padding: 1px 5px; }}
 .mw-reset:hover {{ background: #f0f0f0; color: #000; }}

 table {{ border-collapse: collapse; font-size: 0.85em; width: 100%; }}
 th, td {{ border: 1px solid #ddd; padding: 4px 8px; text-align: right;
          white-space: nowrap; }}
 th {{ background: #f0f0f0; cursor: pointer; user-select: none; text-align: center; }}
 th.no-sort {{ cursor: default; background: #e8e8e8; }}
 th:hover:not(.no-sort) {{ background: #e0e0e0; }}
 /* Ticker column shifted from 3 to 4 by D48's checkbox column */
 td:nth-child(4) {{ text-align: left; }}
 /* Checkbox cells centered */
 td.no-sort, th.no-sort {{ text-align: center; }}
 .h3   {{ background: #fff3e0; }}
 .h12  {{ background: #e3f2fd; }}
 .heither {{ background: #f3e5f5; }}
 .gap_high {{ background: #ffebee; color: #b71c1c; font-weight: 600; }}  /* >50% above fair — wait */
 .gap_pos  {{ background: #fff8e1; }}                                     /* above fair */
 .gap_neg  {{ background: #e8f5e9; color: #1b5e20; font-weight: 600; }}   /* at or below fair — buy */

 /* Expand-arrow toggle */
 .expand-toggle {{ background: none; border: 1px solid #aaa; border-radius: 3px;
                   padding: 0 6px; cursor: pointer; font-size: 0.95em; color: #555;
                   width: 22px; height: 22px; line-height: 1; }}
 .expand-toggle:hover {{ background: #f0f0f0; color: #000; }}
 .expand-toggle.open {{ color: #1976d2; border-color: #1976d2; }}

 /* Embedded detail panel */
 tr.detail-row > td {{ background: #fafafa; padding: 1em 1.5em;
                       white-space: normal; text-align: left; }}      /* left-align prose */
 .detail-panel .horizons {{ display: flex; gap: 1em; margin-bottom: 1em; }}
 .detail-panel .horizon {{ flex: 1; background: #fff; padding: 0.6em 0.8em;
                            border: 1px solid #ddd; border-radius: 4px;
                            font-size: 0.9em; }}
 .detail-panel .horizon.empty {{ color: #aaa; font-style: italic; }}
 .detail-panel details {{ margin: 0.5em 0; }}
 .detail-panel summary {{ cursor: pointer; color: #555; font-weight: 600; padding: 4px 0; }}
 .detail-panel pre {{ background: #f5f5f5; padding: 0.8em; overflow-x: auto;
                      max-height: 400px; font-size: 0.78em; border-radius: 4px;
                      white-space: pre-wrap; word-break: break-word; }}
</style>
</head><body>
<h1>Module 6 — final ranking</h1>
<div class="stamp">Quarter <code>{html.escape(quarter)}</code> &middot;
 run_id <code>{run_id}</code> &middot;
 click any &#9654; to expand the LLM response inline</div>

<!-- D49 — selection toolbar (auto-save via local HTTP server) -->
<div id="serve-banner" class="serve-banner" style="display:none">
  <b>Auto-save disabled</b> — this page is loaded directly from disk.
  Start the local server to enable silent auto-save:
  <code>{html.escape(serve_command)}</code>
  &middot; then open
  <a id="serve-banner-link" href="#">http://127.0.0.1:4609/{html.escape(quarter)}</a>.
</div>
<div class="toolbar" id="selection-toolbar">
  {hamburger_button}
  <span>Selected: <span id="selected-count" class="badge">{initial_checked} of {total_count}</span></span>
  <button id="copy-cli" title="Copy the python command that re-scores from this file">Copy CLI command</button>
  <button id="copy-serve" title="Copy the command that starts the local selection editor server">Copy serve command</button>
  <span class="hint">Tip: uncheck any tickers you don't want re-scored — saves automatically.</span>
  <span id="toolbar-toast" class="toast"></span>
</div>
{modifier_panel_html}

<table id="rank">
<thead><tr>{th_html}</tr></thead>
<tbody>{table_rows}</tbody>
</table>
<script>
const QUARTER = {quarter_js};
const CLI_COMMAND = {cli_command_js};
const SERVE_COMMAND = {serve_command_js};
// D47 / D50 — modifier configuration + per-ticker model factors. Driven
// by the hamburger panel; recomputes table cells on every slider tick.
const MODIFIER_DATA = {modifier_data_js};
const CURRENT_WEIGHTS = Object.assign({{}}, MODIFIER_DATA.weights || {{}});

// ─────────────────────────────────────────────────────────────────
// Expand-arrow click handler: toggle the matching detail row.
// ─────────────────────────────────────────────────────────────────
document.querySelectorAll('.expand-toggle').forEach(btn => {{
  btn.addEventListener('click', e => {{
    e.stopPropagation();
    const target = document.getElementById(btn.dataset.target);
    if (!target) return;
    const isOpen = target.style.display !== 'none';
    target.style.display = isOpen ? 'none' : 'table-row';
    btn.innerHTML = isOpen ? '&#9654;' : '&#9660;';
    btn.classList.toggle('open', !isOpen);
  }});
}});

// ─────────────────────────────────────────────────────────────────
// D48 — selection checkboxes (master + per-row + count badge + save)
// ─────────────────────────────────────────────────────────────────
const master = document.getElementById('select-all');
const rowCheckboxes = () => Array.from(
  document.querySelectorAll('input[name="ticker_select"]')
);
const countBadge = document.getElementById('selected-count');
const toast = document.getElementById('toolbar-toast');

function showToast(msg, isError) {{
  toast.textContent = msg;
  toast.classList.toggle('error', !!isError);
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 4000);
}}

function syncMasterAndCount() {{
  const cbs = rowCheckboxes();
  const total = cbs.length;
  const checked = cbs.filter(c => c.checked).length;
  countBadge.textContent = checked + ' of ' + total;
  countBadge.classList.toggle('partial', checked > 0 && checked < total);
  countBadge.classList.toggle('empty',   checked === 0);
  if (master) {{
    if (checked === 0)            {{ master.checked = false; master.indeterminate = false; }}
    else if (checked === total)   {{ master.checked = true;  master.indeterminate = false; }}
    else                          {{ master.checked = false; master.indeterminate = true;  }}
  }}
}}

// ─────────────────────────────────────────────────────────────────
// D49 — silent auto-save via the local HTTP server (sidecar JSON).
// On every checkbox toggle, PUT /selection/<quarter> with the current
// state. The server (scripts/6_serve_report.py) writes the sidecar
// JSON atomically. No file-picker dialog, no permission prompt —
// browser security is satisfied because we're talking to a same-origin
// HTTP endpoint, not the local filesystem.
//
// When the page is opened directly via file:// (no server), auto-save
// is disabled and a banner tells the user how to enable it.
// ─────────────────────────────────────────────────────────────────
const SERVER_MODE = (
  window.location.protocol === 'http:' ||
  window.location.protocol === 'https:'
);
const SELECTION_URL = '/selection/' + encodeURIComponent(QUARTER);
let pendingSave = null;          // debounce timer
let firstSaveSeen = false;       // one-shot warning gate for file://
const SAVE_DEBOUNCE_MS = 300;

function selectionPayload() {{
  const cbs = rowCheckboxes();
  return {{
    schema_version: 2,
    quarter: QUARTER,
    all_tickers:      cbs.map(c => c.value),
    selected_tickers: cbs.filter(c => c.checked).map(c => c.value),
    // D50 — slider state. Server preserves verbatim into the sidecar JSON;
    // the renderer reads it back on next load.
    modifier_weights: typeof CURRENT_WEIGHTS === 'undefined' ? {{}} : CURRENT_WEIGHTS,
  }};
}}

async function autoSave() {{
  pendingSave = null;
  const checked = rowCheckboxes().filter(c => c.checked).length;
  if (!SERVER_MODE) {{
    if (!firstSaveSeen) {{
      firstSaveSeen = true;
      showToast(
        'Auto-save disabled — open via the local server '
        + '(python scripts/6_serve_report.py).',
        true,
      );
    }}
    return;
  }}
  try {{
    const res = await fetch(SELECTION_URL, {{
      method:  'PUT',
      headers: {{'Content-Type': 'application/json'}},
      body:    JSON.stringify(selectionPayload()),
    }});
    if (!res.ok) throw new Error('HTTP ' + res.status);
    showToast('Auto-saved (' + checked + ' selected).');
  }} catch (err) {{
    console.error(err);
    showToast('Auto-save failed: ' + err.message
              + ' — is the local server running?', true);
  }}
}}

function scheduleAutoSave() {{
  if (pendingSave) clearTimeout(pendingSave);
  pendingSave = setTimeout(autoSave, SAVE_DEBOUNCE_MS);
}}

function onSelectionChange() {{
  syncMasterAndCount();
  scheduleAutoSave();
}}

if (master) {{
  master.addEventListener('change', () => {{
    rowCheckboxes().forEach(c => {{ c.checked = master.checked; }});
    onSelectionChange();
  }});
}}
rowCheckboxes().forEach(c => {{
  c.addEventListener('change', onSelectionChange);
}});
syncMasterAndCount();

document.getElementById('copy-cli').addEventListener('click', async () => {{
  try {{
    await navigator.clipboard.writeText(CLI_COMMAND);
    showToast('Copied: ' + CLI_COMMAND);
  }} catch (err) {{
    showToast('Copy failed — command: ' + CLI_COMMAND, true);
  }}
}});
document.getElementById('copy-serve').addEventListener('click', async () => {{
  try {{
    await navigator.clipboard.writeText(SERVE_COMMAND);
    showToast('Copied: ' + SERVE_COMMAND);
  }} catch (err) {{
    showToast('Copy failed — command: ' + SERVE_COMMAND, true);
  }}
}});

// Show the file:// warning banner when the page is NOT served by the HTTP
// server. The banner explains how to start the server for auto-save.
if (!SERVER_MODE) {{
  const banner = document.getElementById('serve-banner');
  if (banner) banner.style.display = '';
}}

// ─────────────────────────────────────────────────────────────────
// On page load, when SERVER_MODE, fetch the live sidecar JSON and
// reconcile both checkbox state and slider weights with whatever the
// server has right now. The embedded MODIFIER_DATA / `checked` attrs
// are snapshots from render time and may be stale relative to a sidecar
// the user (or another tab) has updated since. Without this, the FIRST
// user interaction would PUT the stale HTML state, clobbering the live
// sidecar — bug observed 2026-04-25.
// ─────────────────────────────────────────────────────────────────
async function syncFromSidecar() {{
  if (!SERVER_MODE) return;
  let data;
  try {{
    const res = await fetch(SELECTION_URL, {{cache: 'no-store'}});
    if (!res.ok) return;
    data = await res.json();
  }} catch (err) {{
    console.warn('syncFromSidecar fetch failed:', err);
    return;
  }}
  let weightsChanged = false;
  if (data && data.modifier_weights && typeof MODIFIER_DATA !== 'undefined') {{
    for (const c of MODIFIER_DATA.components) {{
      const v = data.modifier_weights[c];
      if (typeof v !== 'number') continue;
      if (CURRENT_WEIGHTS[c] === v) continue;
      CURRENT_WEIGHTS[c] = v;
      const rowEl = document.querySelector(
        '.mw-row[data-component="' + c + '"]'
      );
      if (rowEl) {{
        const slider = rowEl.querySelector('.mw-slider');
        const valueEl = rowEl.querySelector('.mw-value');
        if (slider) slider.value = String(v);
        if (valueEl) valueEl.textContent = v.toFixed(2);
        markRowDirty(rowEl, v);
      }}
      weightsChanged = true;
    }}
  }}
  let selectionChanged = false;
  if (data && Array.isArray(data.selected_tickers)) {{
    const wanted = new Set(data.selected_tickers);
    rowCheckboxes().forEach(cb => {{
      const want = wanted.has(cb.value);
      if (cb.checked !== want) {{
        cb.checked = want;
        selectionChanged = true;
      }}
    }});
    if (selectionChanged) syncMasterAndCount();
  }}
  if (weightsChanged) recomputeAndRerank();
}}
syncFromSidecar();

// ─────────────────────────────────────────────────────────────────
// D47 / D50 — modifier weight sliders + live per-row recompute + re-rank.
// Mirrors module_6b.modifiers.apply_weights bit-identically.
// ─────────────────────────────────────────────────────────────────
function effectiveFactor(model, weight) {{
  return 1.0 + weight * (model - 1.0);
}}
function modifierFor(ticker) {{
  const tf = (MODIFIER_DATA.ticker_factors || {{}})[ticker];
  if (!tf) return 1.0;
  let p = 1.0;
  for (const c of MODIFIER_DATA.components) {{
    const m = (c in tf) ? tf[c] : 1.0;
    const w = (c in CURRENT_WEIGHTS) ? CURRENT_WEIGHTS[c] : 1.0;
    p *= effectiveFactor(m, w);
  }}
  const b = MODIFIER_DATA.bounds || {{min: 0.5, max: 1.5}};
  return Math.max(b.min, Math.min(b.max, p));
}}
function modifierClass(m) {{
  if (m < 0.85) return 'mod_strong_neg';
  if (m < 0.95) return 'mod_neg';
  if (m > 1.20) return 'mod_pos';
  if (m > 1.05) return 'mod_mild_pos';
  return 'mod_neutral';
}}
function recomputeAndRerank(opts) {{
  opts = opts || {{}};
  const tbody = document.querySelector('#rank tbody');
  if (!tbody) return;
  const mains = Array.from(tbody.querySelectorAll('.main-row'));
  for (const row of mains) {{
    const t = row.dataset.ticker;
    const base = parseFloat(row.dataset.baseScore || '0');
    const m = modifierFor(t);
    const adj = m * base;
    row.dataset.adjusted = adj.toFixed(6);
    const modCell = row.querySelector('.modifier-cell');
    const adjCell = row.querySelector('.adjusted-cell');
    if (modCell) {{
      modCell.textContent = m.toFixed(3);
      modCell.className = 'modifier-cell ' + modifierClass(m);
    }}
    if (adjCell) {{
      adjCell.textContent = isFinite(adj) ? adj.toFixed(2) : '';
    }}
  }}
  // Re-sort by adjusted desc (the canonical ranking key now), update the
  // rank cell, and re-place rows in the DOM keeping main+detail pairs.
  mains.sort((a, b) => parseFloat(b.dataset.adjusted) - parseFloat(a.dataset.adjusted));
  mains.forEach((row, idx) => {{
    const rankCell = row.querySelector('.rank-cell');
    if (rankCell) rankCell.textContent = (idx + 1);
    tbody.appendChild(row);
    const detail = tbody.querySelector(
      '.detail-row[data-for="' + row.dataset.ticker.replace(/"/g, '\\\\"') + '"]'
    );
    if (detail) tbody.appendChild(detail);
  }});
}}

// Hamburger drawer open/close (only present when modifier panel was rendered).
const mwToggle  = document.getElementById('mw-toggle');
const mwDrawer  = document.getElementById('mw-drawer');
const mwOverlay = document.getElementById('mw-overlay');
const mwClose   = document.getElementById('mw-close');
function openDrawer() {{
  if (!mwDrawer) return;
  mwDrawer.style.display = '';  mwDrawer.setAttribute('aria-hidden', 'false');
  mwOverlay.style.display = ''; mwOverlay.setAttribute('aria-hidden', 'false');
}}
function closeDrawer() {{
  if (!mwDrawer) return;
  mwDrawer.style.display = 'none';  mwDrawer.setAttribute('aria-hidden', 'true');
  mwOverlay.style.display = 'none'; mwOverlay.setAttribute('aria-hidden', 'true');
}}
if (mwToggle)  mwToggle.addEventListener('click', openDrawer);
if (mwClose)   mwClose.addEventListener('click', closeDrawer);
if (mwOverlay) mwOverlay.addEventListener('click', closeDrawer);
window.addEventListener('keydown', e => {{
  if (e.key === 'Escape' && mwDrawer && mwDrawer.style.display !== 'none') closeDrawer();
}});

// Slider wiring: input → recompute → debounce-save.
function markRowDirty(rowEl, weight) {{
  const dft = (MODIFIER_DATA.weights_ui && MODIFIER_DATA.weights_ui.default) || 1.0;
  const dirty = Math.abs(weight - dft) > 1e-9;
  rowEl.classList.toggle('dirty', dirty);
}}
function setSliderWeight(component, weight, opts) {{
  opts = opts || {{}};
  CURRENT_WEIGHTS[component] = weight;
  const rowEl = document.querySelector(
    '.mw-row[data-component="' + component + '"]'
  );
  if (rowEl) {{
    const slider = rowEl.querySelector('.mw-slider');
    const valueEl = rowEl.querySelector('.mw-value');
    if (slider) slider.value = String(weight);
    if (valueEl) valueEl.textContent = weight.toFixed(2);
    markRowDirty(rowEl, weight);
  }}
  if (!opts.silent) {{
    recomputeAndRerank();
    scheduleAutoSave();   // weights ride along on the same PUT as selection
  }}
}}
document.querySelectorAll('.mw-row').forEach(rowEl => {{
  const component = rowEl.dataset.component;
  const slider = rowEl.querySelector('.mw-slider');
  const valueEl = rowEl.querySelector('.mw-value');
  const resetBtn = rowEl.querySelector('.mw-reset');
  if (slider) {{
    slider.addEventListener('input', () => {{
      const w = parseFloat(slider.value);
      if (valueEl) valueEl.textContent = w.toFixed(2);
      markRowDirty(rowEl, w);
      CURRENT_WEIGHTS[component] = w;
      recomputeAndRerank();
    }});
    slider.addEventListener('change', scheduleAutoSave);   // commit on release
  }}
  if (resetBtn) {{
    resetBtn.addEventListener('click', () => {{
      const dft = (MODIFIER_DATA.weights_ui && MODIFIER_DATA.weights_ui.default) || 1.0;
      setSliderWeight(component, dft);
    }});
  }}
  // Initialise dirty class from the seeded weight value.
  markRowDirty(rowEl, parseFloat(slider ? slider.value : '1.0'));
}});
const resetAllBtn = document.getElementById('mw-reset-all');
if (resetAllBtn) {{
  resetAllBtn.addEventListener('click', () => {{
    const dft = (MODIFIER_DATA.weights_ui && MODIFIER_DATA.weights_ui.default) || 1.0;
    for (const c of MODIFIER_DATA.components) {{
      setSliderWeight(c, dft, {{silent: true}});
    }}
    recomputeAndRerank();
    scheduleAutoSave();
  }});
}}
const disableAllBtn = document.getElementById('mw-disable-all');
if (disableAllBtn) {{
  disableAllBtn.addEventListener('click', () => {{
    // weight=0 → effective_factor = 1 + 0 × (model − 1) = 1.0 for every
    // component → modifier ≡ 1.0 → ranking reverts to raw score.
    const minW = (MODIFIER_DATA.weights_ui && MODIFIER_DATA.weights_ui.min) ?? 0.0;
    for (const c of MODIFIER_DATA.components) {{
      setSliderWeight(c, minW, {{silent: true}});
    }}
    recomputeAndRerank();
    scheduleAutoSave();
  }});
}}

// ─────────────────────────────────────────────────────────────────
// Sort: only iterate over .main-row; for each placed main row, also
// re-place its associated .detail-row right after it so they stay paired.
// ─────────────────────────────────────────────────────────────────
document.querySelectorAll('#rank thead th:not(.no-sort)').forEach(th => {{
  th.addEventListener('click', () => {{
    const tbody = th.closest('table').tBodies[0];
    const idx = +th.dataset.col;
    const asc = th.dataset.dir !== 'asc';
    th.dataset.dir = asc ? 'asc' : 'desc';
    const mains = Array.from(tbody.querySelectorAll('.main-row'));
    mains.sort((a, b) => {{
      const av = a.cells[idx].innerText.replace(/[^-0-9.]/g, '');
      const bv = b.cells[idx].innerText.replace(/[^-0-9.]/g, '');
      const an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
      return asc ? a.cells[idx].innerText.localeCompare(b.cells[idx].innerText)
                 : b.cells[idx].innerText.localeCompare(a.cells[idx].innerText);
    }});
    mains.forEach(main => {{
      tbody.appendChild(main);
      const detail = tbody.querySelector(
        '.detail-row[data-for="' + main.dataset.ticker.replace(/"/g, '\\\\"') + '"]'
      );
      if (detail) tbody.appendChild(detail);
    }});
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

    # Column layout matches the HTML.
    # Order: identifiers → price/positioning → scoring → targets → fundamentals.
    headers = (
        "rank",                                           # A
        "ticker",                                         # B
        "current_price_at_scoring_usd",                   # C — D46
        "fair_entry_low_usd", "fair_entry_high_usd",      # D, E
        "current_vs_fair_mid_pct",                        # F — D46 (positioning gap)
        "final_horizon",                                  # G
        "final_score",                                    # H — D46 PRIMARY
        "months_to_catalyst",                             # I — divisor used by score formula
        "score_at_current_3mo", "score_at_current_12mo",  # J, K — D46
        "target_price_3mo_usd", "target_price_12mo_usd",  # L, M
        "probability_3mo", "probability_12mo",            # N, O
        "rnpv_per_share_usd", "moat_score",               # P, Q
        "fda_pos_adjusted_lead", "market_cap_usd", "fund_count",  # R, S, T
    )
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")

    for r in rows:
        ws.append([
            r.get("final_rank"), r.get("ticker"),
            r.get("current_price_at_scoring_usd"),
            r.get("fair_entry_low_usd"), r.get("fair_entry_high_usd"),
            r.get("current_vs_fair_mid_pct"),
            r.get("final_horizon"), r.get("final_score"),
            _months_to_catalyst(r),
            r.get("score_at_current_3mo"), r.get("score_at_current_12mo"),
            r.get("target_price_3mo_usd"), r.get("target_price_12mo_usd"),
            r.get("probability_3mo"), r.get("probability_12mo"),
            r.get("rnpv_per_share_usd"), r.get("moat_score"),
            r.get("fda_pos_adjusted_lead"), r.get("market_cap_usd"),
            r.get("fund_count"),
        ])
    ws.freeze_panes = "C2"
    if rows:
        last_row = len(rows) + 1
        # PRIMARY score is now column H (final_score).
        rule = ColorScaleRule(
            start_type="num", start_value=-10, start_color="FF6B6B",
            mid_type="num", mid_value=0, mid_color="FFFFFF",
            end_type="num", end_value=20, end_color="6BCB77",
        )
        ws.conditional_formatting.add(f"H2:H{last_row}", rule)
        # Positioning gap is now column F (current_vs_fair_mid_pct).
        gap_rule = ColorScaleRule(
            start_type="num", start_value=-50, start_color="A5D6A7",
            mid_type="num", mid_value=0, mid_color="FFFFFF",
            end_type="num", end_value=100, end_color="EF9A9A",
        )
        ws.conditional_formatting.add(f"F2:F{last_row}", gap_rule)
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
            current = h.get('current_price_at_scoring_usd')
            current_str = f"${current:.2f}" if current else "n/a"
            score_curr = h.get('score_at_current_pct_per_month')           # D46 — primary
            score_fair = h.get('score_at_fair_pct_per_month')              # reference
            score_curr_str = f"{score_curr:.2f}" if score_curr is not None else "n/a"
            score_fair_str = f"{score_fair:.2f}" if score_fair is not None else "n/a"
            return (
                f"<div class='horizon'><b>{label}</b><br>"
                f"target ${h.get('target_price_usd','?')} &middot; "
                f"{h.get('time_to_catalyst_weeks','?')}w &middot; "
                f"prob {h.get('probability','?')} &middot; "
                f"current {current_str}<br>"
                f"<b>score@current {score_curr_str} %/mo</b> "
                f"<span style='color:#888'>(score@fair {score_fair_str} reference)</span><br>"
                f"<i>{html.escape((h.get('thesis_summary') or '')[:400])}</i>"
                f"</div>"
            )

        # D46 — summary line uses score_at_current; if both horizon rows are present,
        # use the larger one (matches final_score in final_rankings).
        scores_curr = [
            (bundle["rows_by_horizon"].get("3mo") or {}).get("score_at_current_pct_per_month"),
            (bundle["rows_by_horizon"].get("12mo") or {}).get("score_at_current_pct_per_month"),
        ]
        scores_curr = [s for s in scores_curr if s is not None]
        primary_score = max(scores_curr) if scores_curr else 0.0
        cards.append(
            f"<details><summary><b>{html.escape(ticker)}</b> &middot; "
            f"tier {html.escape(shared.get('source_tier') or '?')} &middot; "
            f"final score {primary_score:.2f} %/mo (current-price)</summary>"
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
