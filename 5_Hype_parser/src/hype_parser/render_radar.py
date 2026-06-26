"""Render the Wave-1 diffusion-radar diagnostic to a single self-contained HTML file.

Intermediate diagnostic (Protocol section 6: intermediates -> _intermediate_outputs/, inline-styled
for portability). Shows, per seed theme, the specialist (N_spec) vs mainstream (N_main) monthly
series, beta_spec / p_main / diffusion_ratio, the (informational) nascency gate, and top member
docs. All thresholds are unfit -> the report is for reading the instrument, not for trading.
"""

import html
from datetime import datetime, timezone


def _spark(values, width=320, height=40, color="#2b6cb0"):
    vals = [float(v) for v in values]
    if not vals or max(vals) == min(vals):
        base = height // 2
        return (f'<svg width="{width}" height="{height}">'
                f'<line x1="0" y1="{base}" x2="{width}" y2="{base}" '
                f'stroke="#ccc" stroke-width="1"/></svg>')
    lo, hi = min(vals), max(vals)
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = (i / (n - 1)) * (width - 4) + 2 if n > 1 else width / 2
        y = height - 2 - ((v - lo) / (hi - lo)) * (height - 4)
        pts.append(f"{x:.1f},{y:.1f}")
    return (f'<svg width="{width}" height="{height}">'
            f'<polyline fill="none" stroke="{color}" stroke-width="1.5" '
            f'points="{" ".join(pts)}"/></svg>')


def _fmt(x, nd=3):
    if x is None:
        return "-"
    if x == float("inf"):
        return "inf"
    return f"{x:.{nd}f}"


def render(theme_blocks, *, embedder_name, semantic, params, out_path,
           registry_version=None, generated_at=None):
    generated_at = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts = []
    parts.append(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hype Parser - Diffusion Radar (Wave 1)</title>
<style>
 body{{font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
   margin:0;background:#f6f7f9;color:#1a202c}}
 .wrap{{max-width:980px;margin:0 auto;padding:20px 16px 60px}}
 h1{{font-size:22px;margin:0 0 4px}}
 .sub{{color:#555;margin:0 0 16px}}
 .banner{{padding:10px 12px;border-radius:8px;margin:10px 0;font-size:13px}}
 .warn{{background:#fff6e6;border:1px solid #f0c674}}
 .info{{background:#eef4ff;border:1px solid #c7d6f5}}
 .card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px;margin:14px 0;
   box-shadow:0 1px 2px rgba(0,0,0,.04)}}
 .card h2{{font-size:17px;margin:0 0 2px}}
 .kw{{color:#718096;font-size:12px;margin:0 0 10px}}
 .metrics{{display:flex;flex-wrap:wrap;gap:18px;margin:8px 0 12px}}
 .metric{{min-width:96px}}
 .metric .v{{font-size:18px;font-weight:600}}
 .metric .l{{font-size:11px;color:#718096;text-transform:uppercase;letter-spacing:.04em}}
 .badge{{display:inline-block;padding:2px 8px;border-radius:12px;font-size:12px;font-weight:600}}
 .b-yes{{background:#e6f4ea;color:#1e7e34}} .b-no{{background:#fdeaea;color:#b23b3b}}
 .series{{display:flex;gap:24px;flex-wrap:wrap;align-items:center;margin:6px 0}}
 .series .lab{{font-size:12px;color:#555;width:170px}}
 table{{border-collapse:collapse;width:100%;font-size:12px;margin-top:8px}}
 th,td{{border-bottom:1px solid #edf0f3;padding:3px 6px;text-align:right}}
 th:first-child,td:first-child{{text-align:left}}
 .docs{{font-size:12px;margin-top:10px}}
 .docs a{{color:#2b6cb0;text-decoration:none}}
 details summary{{cursor:pointer;color:#555;font-size:12px;margin-top:8px}}
 code{{background:#eef0f3;padding:1px 4px;border-radius:4px}}
</style></head><body><div class="wrap">""")

    parts.append(f"<h1>Diffusion Radar &mdash; Wave 1 (intermediate diagnostic)</h1>")
    parts.append(f'<p class="sub">Generated {html.escape(generated_at)} UTC'
                 + (f" &middot; registry {html.escape(str(registry_version))}"
                    if registry_version else "") + "</p>")

    if not semantic:
        parts.append(
            '<div class="banner warn"><b>Placeholder embedder.</b> Running '
            f'<code>{html.escape(embedder_name)}</code> (lexical word/bigram hashing, '
            "NOT semantic). Membership cosines are lexical only. Install "
            "<code>sentence-transformers</code> for the production semantic encoder "
            "(one-line swap; the embed model is a frozen per-registry parameter).</div>")
    else:
        parts.append(f'<div class="banner info">Embedder: '
                     f'<code>{html.escape(embedder_name)}</code> (semantic).</div>')

    parts.append(
        '<div class="banner info"><b>Parameters are unfit (&#9881;).</b> '
        f"tau_member={params.get('tau_member')}, L={params.get('L')}, "
        f"beta_min={params.get('beta_min')}, p_max={params.get('p_max')}. "
        "These are informational defaults (Features section 5); the nascency gate below is "
        "NOT a trading signal until fit on the labeled panel (Protocol section 3).</div>")

    for blk in theme_blocks:
        t = blk["theme"]
        s = blk["summary"]
        series = blk["series"]
        members = blk["members"]
        gate = s["nascency_gate"]
        parts.append('<div class="card">')
        parts.append(f'<h2>{html.escape(t["label"])}</h2>')
        src_counts = blk.get("source_counts") or {}
        src_str = ", ".join(f"{k} {v}" for k, v in src_counts.items()) or "none"
        parts.append(f'<p class="kw">{html.escape(t.get("keywords_str", ""))} '
                     f'&middot; member docs by source: {html.escape(src_str)} '
                     f'&middot; candidates: {blk.get("n_docs", 0)}</p>')
        if not series:
            parts.append(
                '<div class="banner warn">No data for this theme &mdash; the Wave-1 specialist '
                "source (arXiv) has little/no coverage of it. This is the expected source-domain "
                "gap for biomedical sub-themes; it is handled by Wave 2 (bioRxiv/medRxiv + "
                "ClinicalTrials.gov, per decisions D6), not a pipeline error.</div></div>")
            continue
        parts.append('<div class="metrics">'
                     f'<div class="metric"><div class="v">{_fmt(s["beta_spec"])}</div>'
                     '<div class="l">&beta;_spec (slope)</div></div>'
                     f'<div class="metric"><div class="v">{_fmt(s["p_main"], 3)}</div>'
                     '<div class="l">p_main</div></div>'
                     f'<div class="metric"><div class="v">{_fmt(s["diffusion_ratio"], 2)}</div>'
                     '<div class="l">diffusion_ratio</div></div>'
                     f'<div class="metric"><div class="v">{s["n_spec_total"]}</div>'
                     '<div class="l">N_spec total</div></div>'
                     f'<div class="metric"><div class="v">{s["n_main_latest"]}</div>'
                     '<div class="l">N_main latest</div></div>'
                     f'<div class="metric"><div class="v">'
                     f'<span class="badge {"b-yes" if gate else "b-no"}">'
                     f'{"PASS" if gate else "no"}</span></div>'
                     '<div class="l">nascency (info)</div></div>'
                     '</div>')
        n_spec = [r["n_spec"] for r in series]
        n_main = [r["n_main"] for r in series]
        wiki = [r["wiki_views"] for r in series]
        parts.append('<div class="series"><span class="lab">N_spec (arXiv membership)</span>'
                     + _spark(n_spec, color="#2b6cb0") + "</div>")
        parts.append('<div class="series"><span class="lab">N_main (GDELT news)</span>'
                     + _spark(n_main, color="#b23b3b") + "</div>")
        if any(wiki):
            parts.append('<div class="series"><span class="lab">Wikipedia views</span>'
                         + _spark(wiki, color="#2f855a") + "</div>")
        edgar_yearly = blk.get("edgar_yearly") or []
        if any(r.get("n_filings", 0) for r in edgar_yearly):
            yrs = edgar_yearly[0]["year"], edgar_yearly[-1]["year"]
            total = sum(r["n_filings"] for r in edgar_yearly)
            parts.append('<div class="series"><span class="lab">EDGAR filings/yr '
                         f'({yrs[0]}-{yrs[1]}, {total} total)</span>'
                         + _spark([r["n_filings"] for r in edgar_yearly], color="#805ad5")
                         + "</div>")

        tickers = blk.get("tickers") or []
        if tickers:
            tk_str = ", ".join(f'{html.escape(t["ticker"])} ({t["n_mentions"]})' for t in tickers)
            parts.append(f'<div class="docs"><b>Tickers mentioning in SEC filings (EDGAR):</b> '
                         f'{tk_str}</div>')

        if members:
            parts.append('<div class="docs"><b>Top member docs (by cosine):</b><ul>')
            for m in members:
                url = html.escape(m["url"] or "")
                title = html.escape(m["title"] or m["doc_id"])
                parts.append(f'<li>{_fmt(m["cosine"], 2)} &middot; '
                             f'<a href="{url}" target="_blank">{title}</a> '
                             f'<span style="color:#999">{html.escape(m["published_month"] or "")}</span></li>')
            parts.append("</ul></div>")

        # raw series table (collapsed)
        if series:
            rows = "".join(
                f"<tr><td>{html.escape(r['period'])}</td><td>{r['n_spec']}</td>"
                f"<td>{r['n_main']}</td><td>{r['wiki_views']}</td></tr>" for r in series)
            parts.append('<details><summary>monthly series</summary>'
                         '<table><tr><th>month</th><th>N_spec</th><th>N_main</th>'
                         f'<th>wiki</th></tr>{rows}</table></details>')
        parts.append("</div>")

    parts.append("</div></body></html>")
    out = "".join(parts)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(out)
    return out_path
