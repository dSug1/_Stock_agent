"""Module 8 — pipeline status render (zero-spend). A self-contained HTML snapshot of where the whole
pipeline stands: universe + geographic coverage, signal coverage, the scoring funnel, and the current
ranked digest. Reads the store read-only; writes `Outputs/pipeline_status.html` (no external resources,
all dynamic text html.escaped — repo security discipline; opens under file://).

Run:  PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_status.py
"""

from __future__ import annotations

import html
import sys
from datetime import datetime, timezone
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from early_detection.config import COMPONENT_ROOT, load_config
from early_detection.store import Store

_OUT = COMPONENT_ROOT / "Outputs" / "pipeline_status.html"
_E = html.escape


def _q(c, sql, *a):
    return c.execute(sql, a).fetchone()[0]


def gather(store: Store, cfg) -> dict:
    c = store.conn
    active = "is_live=1 AND below_floor=0 AND above_ceiling=0"
    pv = cfg.scoring_prompt_version
    sig_types = ["capital_markets", "ownership_crossing", "literature", "clinical_trial",
                 "regulatory_designation"]
    return {
        "total": _q(c, "SELECT COUNT(*) FROM entity WHERE is_live=1"),
        "active": _q(c, f"SELECT COUNT(*) FROM entity WHERE {active}"),
        "above_ceiling": _q(c, "SELECT COUNT(*) FROM entity WHERE is_live=1 AND above_ceiling=1"),
        "unknown_cap": _q(c, f"SELECT COUNT(*) FROM entity WHERE {active} AND mktcap_unknown=1"),
        "foreign_active": _q(c, f"SELECT COUNT(*) FROM entity WHERE {active} AND jurisdiction NOT IN ('US','CA')"),
        "by_country": [(r[0] or "?", r[1]) for r in c.execute(
            f"SELECT jurisdiction, COUNT(*) FROM entity WHERE {active} GROUP BY jurisdiction ORDER BY 2 DESC")],
        "signals": {t: _q(c, "SELECT COUNT(*) FROM signal WHERE signal_type=?", t) for t in sig_types},
        "sig_entities": {t: _q(c, "SELECT COUNT(DISTINCT entity_id) FROM signal WHERE signal_type=?", t) for t in sig_types},
        "founders": _q(c, "SELECT COUNT(*) FROM founder"),
        "founders_resolved": _q(c, "SELECT COUNT(*) FROM founder WHERE openalex_author_id IS NOT NULL"),
        "extracted": _q(c, "SELECT COUNT(*) FROM entity WHERE founder_prompt_version IS NOT NULL"),
        "cleared_cit": len(store.scoring_candidates(pv, min_independent=1, force=True)),
        "cleared_clin": len(store.scoring_candidates(pv, min_independent=1, force=True, clinical_min_phase=2)),
        "scored": _q(c, "SELECT COUNT(*) FROM score WHERE prompt_version=?", pv),
        "digest": [dict(r) for r in c.execute(
            "SELECT s.conviction_flag f, s.conviction_score sc, e.legal_name nm, e.ticker_primary tk, "
            "e.jurisdiction j FROM score s JOIN entity e ON e.entity_id=s.entity_id WHERE s.prompt_version=? "
            "ORDER BY CASE s.conviction_flag WHEN 'deep-dive-candidate' THEN 0 WHEN 'surveil' THEN 1 ELSE 2 "
            "END, s.conviction_score DESC", (pv,))],
        "des_types": {r[0]: r[1] for r in c.execute(
            "SELECT json_extract(raw_payload_json,'$.designation'), COUNT(*) FROM signal "
            "WHERE signal_type='regulatory_designation' GROUP BY 1 ORDER BY 2 DESC")},
        "pv": pv, "model": cfg.scoring_model,
    }


def _bar(label, n, total, sub=""):
    pct = (n / total * 100) if total else 0
    return (f'<div class="bar"><div class="bl">{_E(label)}<span class="bn">{n:,}{_E(sub)}</span></div>'
            f'<div class="bt"><div class="bf" style="width:{pct:.1f}%"></div></div></div>')


def render(d: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tiles = [
        ("Live entities", f"{d['total']:,}", "universe"),
        ("Active universe", f"{d['active']:,}", "$10M–$3B, in-band"),
        ("Countries", str(len(d["by_country"])), "jurisdictions"),
        ("Foreign active", f"{d['foreign_active']:,}", "non-US/CA (Nordic·EU·CA)"),
        ("Clear pre-filter", f"{d['cleared_cit']:,}", "on citations · 236 w/ clinical"),
        ("Scored (digest)", f"{d['scored']:,}", "Claude conviction calls"),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="tv">{_E(v)}</div><div class="tl">{_E(l)}</div>'
        f'<div class="ts">{_E(s)}</div></div>' for l, v, s in tiles)

    # geographic coverage bars
    maxc = max((n for _, n in d["by_country"]), default=1)
    geo = "".join(_bar(cc, n, maxc) for cc, n in d["by_country"])

    # signal coverage
    sig_labels = {"capital_markets": "Capital-markets (EDGAR incl. FPI)",
                  "ownership_crossing": "Ownership crossings (13D/G)",
                  "literature": "Literature / citations (OpenAlex)",
                  "clinical_trial": "Clinical trials (CT.gov)",
                  "regulatory_designation": "FDA designations (EDGAR FTS)"}
    maxs = max(d["signals"].values() or [1])
    sigs = "".join(_bar(sig_labels[t], d["signals"][t], maxs, sub=f"  ·  {d['sig_entities'][t]:,} entities")
                   for t in sig_labels)

    des = "  ".join(f'<span class="chip">{_E(k)} {v:,}</span>' for k, v in d["des_types"].items())

    # scoring funnel
    steps = [("Active universe", d["active"]), ("Founder-extracted", d["extracted"]),
             ("≥1 independent citation", d["cleared_cit"]),
             ("Cleared pre-filter (cit)", d["cleared_cit"]), ("Scored → digest", d["scored"])]
    fmax = max(n for _, n in steps)
    funnel = "".join(_bar(l, n, fmax) for l, n in steps)

    # digest rows
    def _fclass(f):
        return {"deep-dive-candidate": "dd", "surveil": "sv"}.get(f, "dp")
    rows = "".join(
        f'<tr><td class="tk">{_E(r["tk"] or "—")}</td><td>{_E(r["nm"])}</td>'
        f'<td class="ctry">{_E(r["j"] or "—")}</td>'
        f'<td><span class="flag {_fclass(r["f"])}">{_E(r["f"])}</span></td>'
        f'<td class="sc">{r["sc"] if r["sc"] is not None else "—"}</td></tr>'
        for r in d["digest"])

    return f"""<title>Early-detection pipeline — status</title>
<style>
 :root{{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--sub:#5b636e;--line:#e6e9ee;--accent:#2f6f6a;--bar:#d7dde4;--dd:#1f7a4d;--sv:#8a6d1f;--dp:#8a3b3b}}
 @media(prefers-color-scheme:dark){{:root{{--bg:#14171b;--card:#1c2026;--ink:#e8ebef;--sub:#9aa3ad;--line:#2a2f37;--accent:#5fb3ab;--bar:#2c333c;--dd:#4cc088;--sv:#d6b24a;--dp:#e0817f}}}}
 :root[data-theme=dark]{{--bg:#14171b;--card:#1c2026;--ink:#e8ebef;--sub:#9aa3ad;--line:#2a2f37;--accent:#5fb3ab;--bar:#2c333c;--dd:#4cc088;--sv:#d6b24a;--dp:#e0817f}}
 :root[data-theme=light]{{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--sub:#5b636e;--line:#e6e9ee;--accent:#2f6f6a;--bar:#d7dde4;--dd:#1f7a4d;--sv:#8a6d1f;--dp:#8a3b3b}}
 *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;padding:28px}}
 .wrap{{max-width:1000px;margin:0 auto}}h1{{font-size:22px;margin:0 0 2px}}.meta{{color:var(--sub);font-size:13px;margin-bottom:22px}}
 h2{{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:var(--sub);margin:28px 0 12px;font-weight:600}}
 .tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}
 .tile{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
 .tv{{font-size:26px;font-weight:700;letter-spacing:-.02em}}.tl{{font-size:13px;margin-top:2px}}.ts{{font-size:12px;color:var(--sub);margin-top:2px}}
 .card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
 .grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}@media(max-width:720px){{.grid2{{grid-template-columns:1fr}}}}
 .bar{{margin:9px 0}}.bl{{display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px}}.bn{{color:var(--sub);font-variant-numeric:tabular-nums}}
 .bt{{height:7px;background:var(--bar);border-radius:4px;overflow:hidden}}.bf{{height:100%;background:var(--accent);border-radius:4px}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}th{{text-align:left;color:var(--sub);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em;padding:6px 8px;border-bottom:1px solid var(--line)}}
 td{{padding:8px;border-bottom:1px solid var(--line)}}.tk{{font-weight:600;font-variant-numeric:tabular-nums}}.ctry{{color:var(--sub)}}.sc{{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}}
 .flag{{font-size:12px;padding:2px 8px;border-radius:20px;font-weight:600}}.dd{{background:color-mix(in srgb,var(--dd) 18%,transparent);color:var(--dd)}}.sv{{background:color-mix(in srgb,var(--sv) 18%,transparent);color:var(--sv)}}.dp{{background:color-mix(in srgb,var(--dp) 18%,transparent);color:var(--dp)}}
 .chip{{display:inline-block;background:var(--bar);border-radius:20px;padding:3px 10px;font-size:12px;margin:2px 0}}
 .note{{background:color-mix(in srgb,var(--accent) 8%,transparent);border:1px solid color-mix(in srgb,var(--accent) 25%,transparent);border-radius:10px;padding:12px 14px;font-size:13px;color:var(--ink)}}
</style>
<div class="wrap">
 <h1>Early-detection pipeline — status</h1>
 <div class="meta">Disruptive-mechanism biotech screener · snapshot {ts} · scoring prompt {_E(d['pv'])} · {_E(d['model'])}</div>
 <div class="tiles">{tile_html}</div>

 <h2>Scoring funnel</h2>
 <div class="card">{funnel}</div>

 <div class="grid2">
  <div><h2>Geographic coverage · active universe</h2><div class="card">{geo}</div></div>
  <div><h2>Signal coverage</h2><div class="card">{sigs}
   <div style="margin-top:10px;font-size:12px;color:var(--sub)">Designation types &nbsp;{des}</div>
  </div></div>
 </div>

 <h2>Current digest · {len(d['digest'])} scored candidates</h2>
 <div class="card" style="padding:6px 10px"><table>
  <tr><th>Ticker</th><th>Company</th><th>Country</th><th>Conviction</th><th>Score</th></tr>{rows}
 </table></div>

 <h2>Pending (not run this pass)</h2>
 <div class="note"><b>Claude steps deferred</b> (no-spend pass): founder-extraction + scoring. <b>{d['cleared_cit']:,} names now clear the pre-filter on citations</b> (up from 12) and <b>236 with clinical widening</b> — only {d['scored']} are scored, so the digest is the Claude bottleneck, not the pipeline. <b>OpenAlex</b> is throttled (~9h lockout); the literature re-run banked 42 resolved founders / 27 independent-citation entities and resumes losslessly at 5/s after cooldown. Next paid step: score the {d['cleared_cit'] - d['scored']}+ newly-cleared names.</div>
</div>
"""


def main() -> int:
    cfg = load_config()
    store = Store(cfg.db_path)
    d = gather(store, cfg)
    store.close()
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(render(d), encoding="utf-8")
    print(f"wrote {_OUT}  (active={d['active']} countries={len(d['by_country'])} "
          f"cleared={d['cleared_cit']} scored={d['scored']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
