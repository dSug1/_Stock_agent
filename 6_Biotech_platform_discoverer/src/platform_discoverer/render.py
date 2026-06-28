"""Self-contained HTML report of the screener store (read-only diagnostic).

Repo convention: a read-only diagnostic is a single self-contained inline-styled HTML (the analogue
of 5_Hype's ``render_radar`` / ``render_discovery``), NOT a template+sidecar split (that split is for
reports the user edits). Everything is ``html.escape``d; there are no external resources or hrefs, so
nothing here can execute injected markup.

At the current build (Stages 0–0b) the report is the **universe funnel**: counts at each stage, the
kept companies, exactly what was deleted and why (the cardinal-rule audit), and the review queue.
A ``scores`` section renders automatically once Stage 4 populates it — until then it says so.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Optional

from .store import Store, now_iso


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt_cap(usd: Optional[float]) -> str:
    if usd is None:
        return "<span class='muted'>unknown</span>"
    if usd >= 1e9:
        return f"${usd / 1e9:.2f}B"
    if usd >= 1e6:
        return f"${usd / 1e6:.1f}M"
    return f"${usd:,.0f}"


def _latest_summary(store: Store, stage: str, reason: str) -> dict:
    row = store.conn.execute(
        "SELECT detail_json FROM audit_log WHERE stage=? AND reason=? ORDER BY ts DESC LIMIT 1",
        (stage, reason)).fetchone()
    if row and row["detail_json"]:
        return json.loads(row["detail_json"])
    return {}


def _deletions(store: Store) -> list[dict]:
    rows = store.conn.execute(
        "SELECT ts, company_id, reason, detail_json FROM audit_log WHERE action='deleted' "
        "ORDER BY reason, ts").fetchall()
    out = []
    for r in rows:
        d = json.loads(r["detail_json"]) if r["detail_json"] else {}
        out.append({"ts": r["ts"], "company_id": r["company_id"], "reason": r["reason"],
                    "name": d.get("name"), "ticker": d.get("ticker"),
                    "exchange": d.get("exchange"), "mktcap_usd_fd": d.get("mktcap_usd_fd")})
    return out


_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0d1117;color:#c9d1d9;font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:28px}
h1{font-size:22px;margin:0 0 4px}
h2{font-size:16px;margin:30px 0 10px;color:#e6edf3;border-bottom:1px solid #21262d;padding-bottom:6px}
.sub{color:#8b949e;font-size:12px;margin-bottom:18px}
.cards{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}
.card{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 16px;min-width:120px}
.card .n{font-size:24px;font-weight:600;color:#e6edf3}
.card .l{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.04em}
.card.good .n{color:#3fb950}.card.cut .n{color:#f85149}.card.flag .n{color:#d29922}
table{border-collapse:collapse;width:100%;margin:6px 0 4px;font-size:13px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid #21262d;vertical-align:top}
th{color:#8b949e;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
tr:hover td{background:#11161d}
.tag{display:inline-block;background:#1f6feb22;color:#58a6ff;border:1px solid #1f6feb44;border-radius:10px;padding:0 8px;margin:1px 2px;font-size:11px}
.tag.flag{background:#d2992222;color:#d29922;border-color:#d2992244}
.muted{color:#6e7681}
.reason{font-family:ui-monospace,monospace;font-size:12px;color:#f85149}
.note{background:#161b22;border:1px solid #21262d;border-left:3px solid #3fb950;border-radius:6px;padding:10px 14px;color:#8b949e;font-size:12px;margin:10px 0}
.empty{color:#6e7681;font-style:italic;padding:8px 0}
"""


def build_report(store: Store, config: Optional[dict] = None) -> str:
    s0a = _latest_summary(store, "stage0a", "universe_assembled")
    s0b = _latest_summary(store, "stage0b", "hard_cuts_done")
    s1 = _latest_summary(store, "stage1", "tagging_done")
    companies = sorted(store.all_companies(), key=lambda c: (c.primary_ticker or c.name))
    deletions = _deletions(store)
    review = store.review_queue_dump()
    review_ids = {r["company_id"] for r in review}
    scores = store.conn.execute(
        "SELECT company_id, model, composite, confidence FROM scores ORDER BY composite DESC"
    ).fetchall()

    band = (config or {}).get("market_cap", {}) if config else {}
    band_txt = (f"{_fmt_cap(band.get('min_usd'))} – {_fmt_cap(band.get('max_usd'))}"
                if band else "configured band")

    p: list[str] = []
    p.append("<!doctype html><html lang='en'><head><meta charset='utf-8'>")
    p.append("<meta name='viewport' content='width=device-width,initial-scale=1'>")
    p.append("<title>Acrivon-Pattern Screener — funnel</title>")
    p.append(f"<style>{_CSS}</style></head><body><div class='wrap'>")
    p.append("<h1>Acrivon-Pattern Listed-Biotech Screener</h1>")
    p.append(f"<div class='sub'>universe funnel diagnostic · generated {_esc(now_iso())} · "
             f"market-cap band {band_txt}</div>")

    p.append("<div class='note'><b>Cardinal rule (§0.2):</b> a company is only ever deleted for "
             "<span class='reason'>mktcap_out_of_band</span> or <span class='reason'>not_live</span>. "
             "Every other narrowing is a flag + an audit row, and is reversible. Missing data is "
             "kept and flagged, never dropped.</div>")

    # ── funnel cards ──
    p.append("<h2>Funnel</h2><div class='cards'>")

    def card(n, label, cls=""):
        p.append(f"<div class='card {cls}'><div class='n'>{_esc(n)}</div>"
                 f"<div class='l'>{_esc(label)}</div></div>")

    card(s0a.get("records_in", "—"), "0a records in")
    card(s0a.get("admitted", "—"), "0a admitted")
    card(s0b.get("kept", len(companies)), "0b kept", "good")
    card(s0b.get("deleted_mktcap_out_of_band", 0), "del · mktcap", "cut")
    card(s0b.get("deleted_not_live", 0), "del · not live", "cut")
    card(s0b.get("flagged_mktcap_unknown", 0), "flag · cap?", "flag")
    if s1:
        card(s1.get("tagged", 0), "0b · TA-tagged", "good")
        card(s1.get("no_ta_tag", 0), "1 · no TA tag", "flag")
    p.append("</div>")

    # ── kept companies ──
    p.append(f"<h2>Companies retained ({len(companies)})</h2>")
    if companies:
        p.append("<table><tr><th>Ticker</th><th>Name</th><th>Exch</th><th>Country</th>"
                 "<th>Mkt cap (FD, USD)</th><th>Nets hit</th><th>Mechanisms</th><th>Flags</th></tr>")
        for c in companies:
            nets = "".join(f"<span class='tag'>{_esc(n)}</span>" for n in c.source_nets)
            tas = ("".join(f"<span class='tag'>{_esc(t)}</span>" for t in c.ta_tags)
                   or "<span class='muted'>—</span>")
            flags = []
            if c.mktcap_unknown:
                flags.append("<span class='tag flag'>mktcap_unknown</span>")
            if c.stage1_excluded:
                flags.append("<span class='tag flag'>stage1_excluded</span>")
            if c.company_id in review_ids:
                flags.append("<span class='tag flag'>review</span>")
            p.append(f"<tr><td><b>{_esc(c.primary_ticker)}</b></td><td>{_esc(c.name)}</td>"
                     f"<td>{_esc(c.exchange)}</td><td>{_esc(c.country)}</td>"
                     f"<td>{_fmt_cap(c.mktcap_usd_fd)}</td><td>{nets}</td><td>{tas}</td>"
                     f"<td>{''.join(flags) or '<span class=muted>—</span>'}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>no companies in store — run the pipeline first.</div>")

    # ── deletions (the audit of the only allowed cuts) ──
    p.append(f"<h2>Deleted at hard cut ({len(deletions)})</h2>")
    if deletions:
        p.append("<table><tr><th>Ticker</th><th>Name</th><th>Reason</th><th>Detail</th></tr>")
        for d in deletions:
            detail = _fmt_cap(d["mktcap_usd_fd"]) if d["reason"] == "mktcap_out_of_band" else ""
            p.append(f"<tr><td>{_esc(d['ticker'])}</td><td>{_esc(d['name'])}</td>"
                     f"<td><span class='reason'>{_esc(d['reason'])}</span></td><td>{detail}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>nothing deleted.</div>")

    # ── review queue ──
    p.append(f"<h2>Review queue ({len(review)})</h2>")
    if review:
        p.append("<table><tr><th>Ticker</th><th>Name</th><th>Reason</th><th>Added</th></tr>")
        for r in review:
            c = store.get_company(r["company_id"])
            tk = c.primary_ticker if c else None
            nm = c.name if c else r["company_id"]
            p.append(f"<tr><td>{_esc(tk)}</td><td>{_esc(nm)}</td><td>{_esc(r['reason'])}</td>"
                     f"<td>{_esc(r['added_at'])}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>review queue empty.</div>")

    # ── scores (future-proof; appears once Stage 4 runs) ──
    p.append("<h2>Composite scores</h2>")
    if scores:
        p.append("<table><tr><th>Company id</th><th>Model</th><th>Composite</th>"
                 "<th>Confidence</th></tr>")
        for sc in scores:
            p.append(f"<tr><td>{_esc(sc['company_id'])}</td><td>{_esc(sc['model'])}</td>"
                     f"<td>{_esc(round(sc['composite'], 3) if sc['composite'] is not None else '—')}</td>"
                     f"<td>{_esc(round(sc['confidence'], 3) if sc['confidence'] is not None else '—')}</td></tr>")
        p.append("</table>")
    else:
        p.append("<div class='empty'>Stage 4 scoring not yet run (milestone 6). "
                 "This section fills in automatically once composites exist.</div>")

    p.append("</div></body></html>")
    return "".join(p)


def write_report(store: Store, path: str | Path, config: Optional[dict] = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(store, config), encoding="utf-8")
    return path
