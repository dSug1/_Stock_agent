"""Render the theme-discovery output to a single self-contained HTML diagnostic.

Intermediate diagnostic (Protocol §6: intermediates -> _intermediate_outputs/, inline-styled for
portability), the discovery analogue of render_radar. Per discovered theme — ranked by the fused
combined score — it shows the jury convergence (which independent leading juries fired), the
jury-timeline nascency (β_jury / recency / runway), the corpus diffusion (β_spec / p_main / gate, when
the radar has measured it), the Track-A (investable) tickers and Track-B (private, listing-watch) roster,
and any specialist smart-money confirmation. A hand-seeded baseline strip lets you eyeball whether the
discovered themes look as early as the curated ones. Every threshold is unfit -> read the instrument,
do not trade it.
"""

import html
from datetime import datetime, timezone


def _fmt(x, nd=3):
    if x is None:
        return "-"
    if x == float("inf"):
        return "inf"
    return f"{x:.{nd}f}"


def _badge(ok, yes="PASS", no="no"):
    cls = "b-yes" if ok else "b-no"
    return f'<span class="badge {cls}">{yes if ok else no}</span>'


def render(report, *, out_path, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    s = report.get("summary", {})
    themes = report.get("themes", [])
    seeds = report.get("seed_baseline", [])
    p = []
    p.append(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hype Parser - Theme Discovery</title>
<style>
 body{{font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
   margin:0;background:#f6f7f9;color:#1a202c}}
 .wrap{{max-width:980px;margin:0 auto;padding:20px 16px 60px}}
 h1{{font-size:22px;margin:0 0 4px}} .sub{{color:#555;margin:0 0 16px}}
 .banner{{padding:10px 12px;border-radius:8px;margin:10px 0;font-size:13px}}
 .info{{background:#eef4ff;border:1px solid #c7d6f5}} .warn{{background:#fff6e6;border:1px solid #f0c674}}
 .card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin:14px 0;
   box-shadow:0 1px 2px rgba(0,0,0,.04)}}
 .card h2{{font-size:17px;margin:0 0 2px}} .rank{{color:#a0aec0;font-weight:600;margin-right:6px}}
 .kw{{color:#718096;font-size:12px;margin:0 0 10px}}
 .metrics{{display:flex;flex-wrap:wrap;gap:18px;margin:8px 0 12px}}
 .metric{{min-width:84px}} .metric .v{{font-size:17px;font-weight:600}}
 .metric .l{{font-size:11px;color:#718096;text-transform:uppercase;letter-spacing:.04em}}
 .badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;font-weight:600}}
 .b-yes{{background:#e6f4ea;color:#1e7e34}} .b-no{{background:#fdeaea;color:#b23b3b}}
 .pill{{display:inline-block;background:#eef0f3;border-radius:10px;padding:1px 8px;margin:2px 3px 0 0;font-size:12px}}
 .ta{{background:#e6f4ea;color:#1e7e34}} .sm{{background:#fff6e6;color:#9a6b00}}
 table{{border-collapse:collapse;width:100%;font-size:12px;margin-top:8px}}
 th,td{{border-bottom:1px solid #edf0f3;padding:3px 6px;text-align:right}}
 th:first-child,td:first-child{{text-align:left}}
 details summary{{cursor:pointer;color:#555;font-size:12px;margin-top:8px}}
 code{{background:#eef0f3;padding:1px 4px;border-radius:4px}}
</style></head><body><div class="wrap">""")

    p.append("<h1>Theme Discovery &mdash; jury convergence (intermediate diagnostic)</h1>")
    p.append(f'<p class="sub">Generated {html.escape(generated_at)} UTC</p>')
    p.append(
        '<div class="banner info"><b>Discovered, not hand-seeded.</b> '
        f'{s.get("n_discovered", 0)} themes from the convergence of independent expert juries; '
        f'{s.get("n_discovered_measured", 0)} measured on the diffusion engine. '
        f'Median &beta;_spec &mdash; discovered <b>{_fmt(s.get("median_discovered_beta_spec"))}</b> '
        f'vs hand-seeded baseline <b>{_fmt(s.get("median_seed_beta_spec"))}</b>.</div>')
    p.append('<div class="banner warn"><b>Parameters unfit (&#9881;).</b> Convergence/nascency/membership '
             "thresholds are informational defaults, calibrated only at the panel back-test. This is a "
             "research read of the instrument, not a trading signal.</div>")

    for i, t in enumerate(themes, 1):
        c = t.get("corpus")
        p.append('<div class="card">')
        p.append(f'<h2><span class="rank">#{i}</span>{html.escape(t["label"])}</h2>')
        juries = ", ".join(html.escape(j) for j in t.get("juries", [])) or "none"
        p.append(f'<p class="kw">juries: {juries} &middot; horizon ~{_fmt(t.get("refined_horizon_years"), 1)}y'
                 f' &middot; {t.get("n_signals", 0)} signals</p>')
        p.append('<div class="metrics">'
                 f'<div class="metric"><div class="v">{_fmt(t.get("combined_score"), 2)}</div>'
                 '<div class="l">combined</div></div>'
                 f'<div class="metric"><div class="v">{_fmt(t.get("rank_score"), 2)}</div>'
                 '<div class="l">jury rank</div></div>'
                 f'<div class="metric"><div class="v">{t.get("n_leading_juries", 0)}</div>'
                 '<div class="l">lead juries</div></div>'
                 f'<div class="metric"><div class="v">{_fmt(t.get("beta_jury"), 2)}</div>'
                 '<div class="l">&beta;_jury</div></div>'
                 f'<div class="metric"><div class="v">{_fmt(t.get("recency"), 2)}</div>'
                 '<div class="l">recency</div></div>')
        if c:
            p.append(f'<div class="metric"><div class="v">{_fmt(c["beta_spec"])}</div>'
                     '<div class="l">&beta;_spec</div></div>'
                     f'<div class="metric"><div class="v">{_fmt(c["p_main"])}</div>'
                     '<div class="l">p_main</div></div>'
                     f'<div class="metric"><div class="v">{_badge(c["nascency_gate"])}</div>'
                     '<div class="l">nascency</div></div>')
        else:
            p.append('<div class="metric"><div class="v" style="color:#a0aec0">n/a</div>'
                     '<div class="l">corpus (run radar)</div></div>')
        p.append('</div>')

        ta = t.get("track_a", [])
        if ta:
            pills = "".join(f'<span class="pill ta">{html.escape(x)}</span>' for x in ta)
            p.append(f'<div><b style="font-size:12px">Track A (investable):</b> {pills}</div>')
        sm = t.get("smart_money", [])
        if sm:
            pills = "".join(
                f'<span class="pill sm">{html.escape(x["ticker"])} ({_fmt(x["smart_money_score"], 1)})</span>'
                for x in sm)
            p.append(f'<div style="margin-top:4px"><b style="font-size:12px">Smart-money new buys:</b> '
                     f'{pills}</div>')
        tb = t.get("track_b", [])
        if tb:
            names = ", ".join(html.escape(x) for x in tb[:40])
            more = f" &hellip; (+{len(tb) - 40})" if len(tb) > 40 else ""
            p.append(f'<details><summary>Track B watchlist ({len(tb)} private, EDGAR listing-watch)'
                     f'</summary><div class="kw">{names}{more}</div></details>')
        p.append('</div>')

    if seeds:
        rows = "".join(
            f'<tr><td>{html.escape(x["label"])}</td><td>{_fmt(x["corpus"]["beta_spec"])}</td>'
            f'<td>{_fmt(x["corpus"]["p_main"])}</td><td>{_badge(x["corpus"]["nascency_gate"], "Y", "n")}</td></tr>'
            for x in seeds)
        p.append('<div class="card"><h2>Hand-seeded baseline (corpus diffusion)</h2>'
                 '<p class="kw">the curated themes, same instrument &mdash; the comparison reference</p>'
                 '<table><tr><th>theme</th><th>&beta;_spec</th><th>p_main</th><th>nascency</th></tr>'
                 f'{rows}</table></div>')

    p.append("</div></body></html>")
    out = "".join(p)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(out)
    return out_path
