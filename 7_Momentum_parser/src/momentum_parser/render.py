"""Render a self-contained HTML report of the latest run (read-only diagnostic).

Single inline-styled file (analogue of 5_Hype_parser's render_radar / render_discovery). All dynamic
text is ``html.escape``d; no external/unescaped hrefs (repo report-security invariant).
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from .config import outputs_dir
from .store import Store

_CSS = """
body{font:14px/1.5 system-ui,sans-serif;margin:24px;color:#1a1a1a;background:#fafafa}
h1{font-size:20px} table{border-collapse:collapse;width:100%;background:#fff}
th,td{padding:6px 10px;border-bottom:1px solid #eee;text-align:left}
th{background:#f0f3f7;cursor:default} tr:hover td{background:#f7fbff}
.p{font-weight:600} .up{color:#0a7d33} .dn{color:#b00020} .mut{color:#888}
"""


def render(store: Store, cfg: dict, run_id: str | None = None) -> Path:
    run_id = run_id or store.latest_run_id() or ""
    rows = store.predictions_for_run(run_id)
    body = []
    for i, r in enumerate(rows, 1):
        fired = ", ".join(s["name"] for s in json.loads(r["signals_json"] or "[]") if s.get("fired")) or "—"
        cls = "up" if r["expected_return"] >= 0 else "dn"
        body.append(
            f"<tr><td>{i}</td><td><b>{html.escape(r['ticker'])}</b></td>"
            f"<td class='p'>{r['p_up']:.0%}</td>"
            f"<td class='{cls}'>{r['expected_return']:+.2%}</td>"
            f"<td>{r['confidence']:.2f}</td><td>{r['composite']:+.2f}</td>"
            f"<td class='mut'>{html.escape(fired)}</td></tr>"
        )
    doc = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>Momentum signals — {html.escape(run_id)}</title><style>{_CSS}</style></head><body>"
        f"<h1>Momentum signals <span class='mut'>· run {html.escape(run_id)} · {len(rows)} tickers</span></h1>"
        "<p class='mut'>P(up) = probability the close one week out exceeds today's. Indicative until "
        "the probability model is backtest-calibrated (see spec/decisions.md D1).</p>"
        "<table><thead><tr><th>#</th><th>Ticker</th><th>P(up)</th><th>Exp. return</th>"
        "<th>Conf.</th><th>Composite</th><th>Fired signals</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></body></html>"
    )
    path = outputs_dir(cfg) / "momentum_report.html"
    path.write_text(doc, encoding="utf-8")
    return path
