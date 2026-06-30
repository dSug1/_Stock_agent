"""Self-contained HTML report of a run (read-only diagnostic) — v0.3 hybrid blend + Claude reasoning.

Each ticker is an expandable card: the ranked summary line (p_final / p_claude / p_model / disagreement /
confidence / review) plus, on expand, the **Claude analysis** (memo + per-dimension reads) read from the
`scores` table — or a "model-only" notice when no Claude call was made (a DRY run). All dynamic text is
``html.escape``d; no external/unescaped hrefs (repo report-security invariant). Native <details> — no JS.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .config import outputs_dir
from .store import Store

_CSS = """
body{font:14px/1.55 system-ui,sans-serif;margin:24px;color:#1a1a1a;background:#fafafa;max-width:1100px}
h1{font-size:20px;margin-bottom:2px} .sub{color:#777;margin:0 0 16px}
details{background:#fff;border:1px solid #e7e7e7;border-radius:8px;margin:6px 0}
summary{padding:9px 12px;cursor:pointer;list-style:none;display:grid;
  grid-template-columns:30px 70px repeat(5,1fr) 90px;gap:8px;align-items:center}
summary::-webkit-details-marker{display:none}
summary:hover{background:#f7fbff} .tk{font-weight:600}
.pf{font-weight:700} .up{color:#0a7d33} .dn{color:#b00020} .mut{color:#888}
.hdr{font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.04em;padding:0 12px;
  display:grid;grid-template-columns:30px 70px repeat(5,1fr) 90px;gap:8px}
.body{padding:4px 14px 12px;border-top:1px solid #f0f0f0;color:#333}
.claude{background:#f6f8fb;border-left:3px solid #4a78c2;padding:8px 10px;border-radius:4px;margin-top:6px}
.dim{display:inline-block;margin-right:10px;color:#555} .badge{color:#b8860b;font-weight:600}
code{background:#eef;padding:1px 4px;border-radius:3px}
"""


def _pct(x):
    return f"{x:.0%}" if x is not None else "—"


def render(store: Store, cfg: dict, run_id: str | None = None) -> Path:
    run_id = run_id or store.latest_run_id() or ""
    rows = store.predictions_for_run(run_id)
    cards, any_claude = [], False

    for i, r in enumerate(rows, 1):
        t, asof = r["ticker"], r["asof"]
        d = r["disagreement"]
        ret_cls = "up" if (r["expected_return"] or 0) >= 0 else "dn"
        review = "<span class='badge'>⚠ review</span>" if r["review"] else ""
        summary = (
            f"<summary><span class='mut'>{i}</span><span class='tk'>{html.escape(t)}</span>"
            f"<span class='pf'>{r['p_up']:.0%}</span><span>{_pct(r['p_claude'])}</span>"
            f"<span>{_pct(r['p_model'])}</span><span>{(f'{d:.2f}' if d is not None else '—')}</span>"
            f"<span class='{ret_cls}'>{(r['expected_return'] or 0):+.2%}</span>"
            f"<span>{r['confidence']:.2f} {review}</span></summary>"
        )

        fired = ", ".join(s["name"] for s in json.loads(r["signals_json"] or "[]") if s.get("fired")) or "—"
        sc = store.latest_score(t, asof)
        if sc and sc["p_up"] is not None and sc["memo"]:
            any_claude = True
            dims = json.loads(sc["dimensions_json"] or "{}")
            dim_html = "".join(
                f"<span class='dim'>{html.escape(k)} <b>{float(v):+.2f}</b></span>"
                for k, v in dims.items() if isinstance(v, (int, float)))
            claude = (f"<div class='claude'><b>Claude analysis</b> "
                      f"<span class='mut'>(tier: {html.escape(str(sc['tier']))})</span><br>"
                      f"{html.escape(sc['memo'])}<br><div style='margin-top:6px'>{dim_html}</div></div>")
        else:
            claude = ("<div class='claude mut'>Model-only — no Claude call this run "
                      "(a DRY run, or this name wasn't scored). Run <code>--dispatch</code> to add "
                      "Claude's reasoning (memo + per-dimension reads).</div>")

        body = (f"<div class='body'>Exp. return <b class='{ret_cls}'>{(r['expected_return'] or 0):+.2%}</b> · "
                f"composite {r['composite']:+.2f} · fired: <span class='mut'>{html.escape(fired)}</span>"
                f"{claude}</div>")
        cards.append(f"<details>{summary}{body}</details>")

    note = ("" if any_claude else
            "<p class='sub'><b>This was a model-only run</b> — no Claude analysis was produced "
            "(DRY run = no spend). Run the live pipeline (<code>--dispatch</code>, capped at "
            "<code>max_usd_per_run</code>) to see Claude's reasoning per ticker.</p>")
    header_cols = ("<div class='hdr'><span>#</span><span>Ticker</span><span>p_final</span>"
                   "<span>p_claude</span><span>p_model</span><span>Δ</span><span>exp.ret</span>"
                   "<span>conf</span></div>")
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Momentum signals — {html.escape(run_id)}</title><style>{_CSS}</style></head><body>"
        f"<h1>Momentum signals <span class='mut'>· {len(rows)} tickers · LONG-ONLY · INDICATIVE</span></h1>"
        f"<p class='sub'>Run {html.escape(run_id)} · P(up) = P(forward-5d return &gt; +0.5×σ_week) = blended "
        "<b>p_final</b> = w·p_claude + (1−w)·p_model. Click a row for the Claude reasoning.</p>"
        f"{note}{header_cols}{''.join(cards)}</body></html>"
    )
    path = outputs_dir(cfg) / "momentum_report.html"
    path.write_text(doc, encoding="utf-8")
    return path
