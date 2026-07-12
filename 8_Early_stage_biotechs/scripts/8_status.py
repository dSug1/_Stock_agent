"""Module 8 — pipeline status + interactive digest (zero-spend), ONE self-contained HTML file.

Supersedes the old `8_render.py`/`digest.html` (obsoleted). Top: a status dashboard (universe +
geographic coverage, signal coverage, scoring funnel). Bottom: the full INTERACTIVE digest ported from
the old render — acknowledge check-boxes (localStorage), collapsible per-candidate panels with the Claude
conviction output (mechanism, convergence, base-rate, caveats) and the raw evidence (independent
citations, specialist funds, founders, clinical stage, FDA designations), plus search / flag / new-only /
cold-only filters. All data is INLINED (`window.__DATA`) so it's a single file — no sidecar, works under
file://. Read-only on the store; all dynamic text is escaped (client-side `esc` + server-side html.escape);
no external resources. Theme-aware (light/dark).

Run:  PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_status.py
"""

from __future__ import annotations

import html
import json
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


def _scored_pv(store, cfg) -> str:
    """The prompt version to show in the digest: the configured one if it has scores, else the version
    with the most scores (so a fresh rubric-version bump doesn't blank the digest before a re-score)."""
    if _q(store.conn, "SELECT COUNT(*) FROM score WHERE prompt_version=?", cfg.scoring_prompt_version):
        return cfg.scoring_prompt_version
    row = store.conn.execute(
        "SELECT prompt_version FROM score GROUP BY prompt_version ORDER BY COUNT(*) DESC LIMIT 1").fetchone()
    return row[0] if row else cfg.scoring_prompt_version


def gather(store: Store, cfg) -> dict:
    c = store.conn
    active = "is_live=1 AND below_floor=0 AND above_ceiling=0"
    pv = cfg.scoring_prompt_version
    sig_types = ["capital_markets", "ownership_crossing", "literature", "clinical_trial",
                 "regulatory_designation"]
    digest_pv = _scored_pv(store, cfg)
    rows = []
    for r in store.top_scores(digest_pv, limit=500):
        j = r.get("json") or {}
        rows.append({
            "entity_id": r["entity_id"], "ticker": r["ticker_primary"], "name": r["legal_name"],
            "jurisdiction": r["jurisdiction"], "market_cap_usd": r["market_cap_usd"],
            "in_existing_universe": bool(r["in_existing_universe"]),
            "flag": r["conviction_flag"], "score": r["conviction_score"],
            "mechanism_summary": j.get("mechanism_summary"),
            "independent_validation": j.get("independent_validation_status"),
            "dimensions": j.get("stack_convergence_dimensions") or [],
            "base_rate": j.get("base_rate_context"), "caveats": j.get("confidence_caveats") or [],
            "evidence": store.evidence_summary(r["entity_id"]),
        })
    return {
        "total": _q(c, "SELECT COUNT(*) FROM entity WHERE is_live=1"),
        "active": _q(c, f"SELECT COUNT(*) FROM entity WHERE {active}"),
        "foreign_active": _q(c, f"SELECT COUNT(*) FROM entity WHERE {active} AND jurisdiction NOT IN ('US','CA')"),
        "by_country": [(r[0] or "?", r[1]) for r in c.execute(
            f"SELECT jurisdiction, COUNT(*) FROM entity WHERE {active} GROUP BY jurisdiction ORDER BY 2 DESC")],
        "signals": {t: _q(c, "SELECT COUNT(*) FROM signal WHERE signal_type=?", t) for t in sig_types},
        "sig_entities": {t: _q(c, "SELECT COUNT(DISTINCT entity_id) FROM signal WHERE signal_type=?", t) for t in sig_types},
        "extracted": _q(c, "SELECT COUNT(*) FROM entity WHERE founder_prompt_version IS NOT NULL"),
        "cleared_cit": len(store.scoring_candidates(pv, min_independent=1, force=True)),
        "cleared_clin": len(store.scoring_candidates(pv, min_independent=1, force=True,
                                                     clinical_min_phase=cfg.prefilter_clinical_min_phase)),
        "des_types": {r[0]: r[1] for r in c.execute(
            "SELECT json_extract(raw_payload_json,'$.designation'), COUNT(*) FROM signal "
            "WHERE signal_type='regulatory_designation' GROUP BY 1 ORDER BY 2 DESC")},
        "digest_pv": digest_pv, "cfg_pv": pv, "model": cfg.scoring_model,
        "floor_usd": cfg.mktcap_floor_usd, "ceiling_usd": cfg.mktcap_ceiling_usd,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "rows": rows,
    }


def _bar(label, n, total, sub=""):
    pct = (n / total * 100) if total else 0
    return (f'<div class="bar"><div class="bl">{_E(label)}<span class="bn">{n:,}{_E(sub)}</span></div>'
            f'<div class="bt"><div class="bf" style="width:{pct:.1f}%"></div></div></div>')


def _dashboard(d: dict) -> str:
    tiles = [
        ("Live entities", f"{d['total']:,}", "universe"),
        ("Active universe", f"{d['active']:,}", "$10M–$3B, in-band"),
        ("Countries", str(len(d["by_country"])), "jurisdictions"),
        ("Foreign active", f"{d['foreign_active']:,}", "non-US/CA (Nordic·EU·CA)"),
        ("Clear pre-filter", f"{d['cleared_clin']:,}", f"{d['cleared_cit']} on citations + clinical stage"),
        ("Scored (digest)", f"{len(d['rows'])}", f"conviction calls · prompt {d['digest_pv']}"),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="tv">{_E(v)}</div><div class="tl">{_E(l)}</div>'
        f'<div class="ts">{_E(s)}</div></div>' for l, v, s in tiles)

    maxc = max((n for _, n in d["by_country"]), default=1)
    geo = "".join(_bar(cc, n, maxc) for cc, n in d["by_country"])

    sig_labels = {"capital_markets": "Capital-markets (EDGAR incl. FPI)",
                  "ownership_crossing": "Ownership crossings (13D/G)",
                  "literature": "Literature / citations (OpenAlex)",
                  "clinical_trial": "Clinical trials (CT.gov)",
                  "regulatory_designation": "FDA designations (EDGAR FTS)"}
    maxs = max(d["signals"].values() or [1])
    sigs = "".join(_bar(sig_labels[t], d["signals"][t], maxs, sub=f"  ·  {d['sig_entities'][t]:,} entities")
                   for t in sig_labels)
    des = "  ".join(f'<span class="chip">{_E(k)} {v:,}</span>' for k, v in d["des_types"].items())

    steps = [("Active universe", d["active"]), ("Founder-extracted", d["extracted"]),
             ("Clear pre-filter (cit)", d["cleared_cit"]),
             ("Clear pre-filter (+clinical)", d["cleared_clin"]), ("Scored → digest", len(d["rows"]))]
    fmax = max(n for _, n in steps)
    funnel = "".join(_bar(l, n, fmax) for l, n in steps)

    return (
        f'<h1>Early-detection pipeline</h1>'
        f'<div class="meta">Disruptive-mechanism biotech screener · snapshot {_E(d["generated"])} · '
        f'digest prompt {_E(d["digest_pv"])} · {_E(d["model"])}</div>'
        f'<div class="tiles">{tile_html}</div>'
        f'<h2>Scoring funnel</h2><div class="pcard">{funnel}</div>'
        f'<div class="grid2">'
        f'<div><h2>Geographic coverage · active universe</h2><div class="pcard">{geo}</div></div>'
        f'<div><h2>Signal coverage</h2><div class="pcard">{sigs}'
        f'<div style="margin-top:10px;font-size:12px;color:var(--sub)">Designation types &nbsp;{des}</div>'
        f'</div></div></div>'
    )


_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--sub:#5b636e;--line:#e6e9ee;--accent:#2f6f6a;--bar:#d7dde4;
 --dd:#1f7a4d;--sv:#8a6d1f;--dp:#8a3b3b;--ddbg:#e7f6ee;--svbg:#fbf3dd;--dpbg:#fbe9e9;--new:#eefaf2;--cold:#eef4ff}
@media(prefers-color-scheme:dark){:root{--bg:#14171b;--card:#1c2026;--ink:#e8ebef;--sub:#9aa3ad;--line:#2a2f37;
 --accent:#5fb3ab;--bar:#2c333c;--dd:#4cc088;--sv:#d6b24a;--dp:#e0817f;--ddbg:#14301e;--svbg:#31280f;--dpbg:#341a1a;--new:#15241b;--cold:#162232}}
:root[data-theme=light]{--bg:#f6f7f9;--card:#fff;--ink:#1a1d21;--sub:#5b636e;--line:#e6e9ee;--accent:#2f6f6a;--bar:#d7dde4;--dd:#1f7a4d;--sv:#8a6d1f;--dp:#8a3b3b;--ddbg:#e7f6ee;--svbg:#fbf3dd;--dpbg:#fbe9e9;--new:#eefaf2;--cold:#eef4ff}
:root[data-theme=dark]{--bg:#14171b;--card:#1c2026;--ink:#e8ebef;--sub:#9aa3ad;--line:#2a2f37;--accent:#5fb3ab;--bar:#2c333c;--dd:#4cc088;--sv:#d6b24a;--dp:#e0817f;--ddbg:#14301e;--svbg:#31280f;--dpbg:#341a1a;--new:#15241b;--cold:#162232}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;padding:28px}
.wrap{max-width:1000px;margin:0 auto}h1{font-size:22px;margin:0 0 2px}.meta{color:var(--sub);font-size:13px;margin-bottom:22px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.05em;color:var(--sub);margin:28px 0 12px;font-weight:600}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tv{font-size:26px;font-weight:700;letter-spacing:-.02em}.tl{font-size:13px;margin-top:2px}.ts{font-size:12px;color:var(--sub);margin-top:2px}
.pcard{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:720px){.grid2{grid-template-columns:1fr}}
.bar{margin:9px 0}.bl{display:flex;justify-content:space-between;font-size:13px;margin-bottom:3px}.bn{color:var(--sub);font-variant-numeric:tabular-nums}
.bt{height:7px;background:var(--bar);border-radius:4px;overflow:hidden}.bf{height:100%;background:var(--accent);border-radius:4px}
.chip{display:inline-block;background:var(--bar);border-radius:20px;padding:3px 10px;font-size:12px;margin:2px 0}
/* digest */
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
.controls input[type=search],.controls select{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);font-size:13px}
.controls label{display:inline-flex;gap:5px;align-items:center;color:var(--sub);font-size:12.5px}
.tally{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0}
.pill{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--sub)}
.sec{margin:20px 0 6px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--sub)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:10px 0;overflow:hidden}
.card.new{background:var(--new)}
.head{display:flex;align-items:center;gap:12px;padding:12px 14px;cursor:pointer}
.tick{font-weight:700;font-size:15px;min-width:64px;font-variant-numeric:tabular-nums}
.nm{flex:1;min-width:0}.nm .co{font-weight:600}.nm .m{color:var(--sub);font-size:12px;margin-top:1px}
.score{font-weight:700;font-size:17px;min-width:34px;text-align:right;font-variant-numeric:tabular-nums}
.badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;white-space:nowrap}
.b-deep{background:var(--ddbg);color:var(--dd)}.b-surv{background:var(--svbg);color:var(--sv)}.b-depri{background:var(--dpbg);color:var(--dp)}
.b-cold{background:var(--cold);color:var(--accent)}.b-star{background:var(--bar);color:var(--sub)}
.ack{margin-left:2px;cursor:pointer}
.panel{display:none;padding:2px 16px 16px;border-top:1px solid var(--line)}
.card.open .panel{display:block}
.panel h4{margin:14px 0 5px;font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--sub)}
.panel p{margin:0 0 6px}.dims{margin:4px 0 6px;padding-left:18px}.dims li{margin:2px 0}
.ev{display:flex;gap:8px 16px;flex-wrap:wrap;font-size:12.5px}.ev .k{color:var(--sub)}.ev b{color:var(--dd)}
.founder{font-size:12.5px;color:var(--sub);margin:2px 0}.founder b{color:var(--ink)}
#no-data{display:none;color:var(--sub);padding:30px 0;text-align:center}
.foot{color:var(--sub);font-size:11.5px;margin-top:22px}
"""

_DIGEST = """
<h2 style="margin-top:34px">Digest · interactive</h2>
<div class="controls">
 <input type="search" id="q" placeholder="Search ticker or name…">
 <select id="fflag"><option value="">All convictions</option>
  <option value="deep-dive-candidate">Deep-dive</option><option value="surveil">Surveil</option>
  <option value="deprioritize">Deprioritize</option></select>
 <label><input type="checkbox" id="fnew"> New only</label>
 <label><input type="checkbox" id="fcold"> Cold-discovery only</label>
 <span style="flex:1"></span>
 <button id="ackall" class="pill" style="cursor:pointer">Acknowledge all</button>
</div>
<div class="tally" id="tally"></div>
<div id="list"></div>
<div id="no-data">No scored candidates under this prompt version yet — run the scoring step.</div>
<div class="foot" id="foot"></div>
"""

_JS = r"""
const D=window.__DATA||{rows:[],meta:{}};
const AKEY='ed8_ack_v1';
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const acked=()=>{try{return JSON.parse(localStorage.getItem(AKEY)||'{}')}catch(e){return{}}};
const setAck=(id,v)=>{const a=acked();if(v)a[id]=1;else delete a[id];localStorage.setItem(AKEY,JSON.stringify(a))};
const cap=v=>v==null?'cap unknown':('$'+(v>=1e9?(v/1e9).toFixed(1)+'B':(v/1e6).toFixed(0)+'M'));
const FLAGS=[['deep-dive-candidate','Deep-dive candidates','b-deep'],['surveil','Surveil','b-surv'],['deprioritize','Deprioritize','b-depri']];
const badgeFlag=f=>{const m={'deep-dive-candidate':'b-deep','surveil':'b-surv','deprioritize':'b-depri'};return '<span class="badge '+(m[f]||'')+'">'+esc(f)+'</span>'};
const tierBadge=r=>r.in_existing_universe?'<span class="badge b-star">★ existing</span>':'<span class="badge b-cold">cold-discovery</span>';
function passes(r){
  const q=document.getElementById('q').value.trim().toLowerCase();
  if(q&&!((r.ticker||'').toLowerCase().includes(q)||(r.name||'').toLowerCase().includes(q)))return false;
  const ff=document.getElementById('fflag').value; if(ff&&r.flag!==ff)return false;
  if(document.getElementById('fnew').checked&&acked()[r.entity_id])return false;
  if(document.getElementById('fcold').checked&&r.in_existing_universe)return false;
  return true;
}
function ev(r){
  const e=r.evidence||{},L=e.literature||{},cl=e.clinical_trials||{},dz=(e.regulatory_designations||{}).types||[];
  const funds=e.specialist_fund_crossings||[],founders=e.founders||[];
  let h='<h4>Evidence</h4><div class="ev">';
  h+='<span class="k">independent-lab citations</span> <b>'+(L.independent_citations||0)+'</b>';
  h+='<span class="k">same-institution</span> '+(L.same_institution_citations||0);
  h+='<span class="k">recent pubs</span> '+(L.recent_publications||0);
  if(cl.trial_count)h+='<span class="k">clinical</span> <b>'+esc(cl.highest_phase_as_lead||cl.highest_phase||'—')+'</b> ('+(cl.active_trials||0)+' active, '+(cl.as_lead||0)+' led'+(cl.stalled_trials?', '+cl.stalled_trials+' stalled':'')+')';
  if(dz.length)h+='<span class="k">designations</span> <b>'+esc(dz.join(', '))+'</b>';
  if(funds.length)h+='<span class="k">funds</span> '+esc(funds.join(', '));
  h+='</div>';
  if(founders.length){h+='<h4>Founders</h4>';founders.forEach(f=>{h+='<div class="founder"><b>'+esc(f.name)+'</b> — '+esc(f.role||'')+(f.institution?' · '+esc(f.institution):'')+(f.openalex_author_id?' · <span style="color:var(--dd)">resolved</span>':'')+'</div>';});}
  return h;
}
function card(r){
  const isNew=!acked()[r.entity_id];
  let h='<div class="card'+(isNew?' new':'')+'" data-id="'+esc(r.entity_id)+'"><div class="head">';
  h+='<div class="tick">'+esc(r.ticker||'—')+'</div>';
  h+='<div class="nm"><div class="co">'+esc(r.name)+'</div><div class="m">'+esc(r.jurisdiction||'')+' · '+cap(r.market_cap_usd)+' · '+badgeFlag(r.flag)+' '+tierBadge(r)+' · <span class="k">'+esc(r.independent_validation||'')+'</span></div></div>';
  h+='<div class="score">'+(r.score==null?'':r.score)+'</div>';
  h+='<input type="checkbox" class="ack" title="Acknowledge"'+(isNew?'':' checked')+'>';
  h+='</div><div class="panel">';
  if(r.mechanism_summary)h+='<h4>Mechanism</h4><p>'+esc(r.mechanism_summary)+'</p>';
  if(r.dimensions&&r.dimensions.length){h+='<h4>Stack convergence</h4><ul class="dims">';r.dimensions.forEach(d=>h+='<li>'+esc(d)+'</li>');h+='</ul>';}
  if(r.base_rate)h+='<h4>Base rate</h4><p>'+esc(r.base_rate)+'</p>';
  h+=ev(r);
  if(r.caveats&&r.caveats.length){h+='<h4>Caveats</h4><ul class="dims">';r.caveats.forEach(c=>h+='<li>'+esc(c)+'</li>');h+='</ul>';}
  h+='</div></div>';return h;
}
function draw(){
  const rows=(D.rows||[]).filter(passes),list=document.getElementById('list');
  document.getElementById('no-data').style.display=(D.rows||[]).length?'none':'';
  let h='';
  FLAGS.forEach(([f,label])=>{const g=rows.filter(r=>r.flag===f).sort((a,b)=>(b.score||0)-(a.score||0));
    if(!g.length)return;h+='<div class="sec">'+esc(label)+' ('+g.length+')</div>';g.forEach(r=>h+=card(r));});
  list.innerHTML=h||'<div class="foot">No candidates match the filters.</div>';
}
function tally(){
  const rows=D.rows||[],by={};rows.forEach(r=>by[r.flag]=(by[r.flag]||0)+1);
  const cold=rows.filter(r=>!r.in_existing_universe).length;
  let h='<span class="pill">'+rows.length+' scored</span>';
  FLAGS.forEach(([f,l,c])=>{if(by[f])h+='<span class="pill"><span class="badge '+c+'">'+by[f]+'</span> '+esc(l)+'</span>';});
  h+='<span class="pill"><span class="badge b-cold">'+cold+'</span> cold-discovery</span>';
  document.getElementById('tally').innerHTML=h;
}
document.addEventListener('click',e=>{
  const ck=e.target.closest('.ack');
  if(ck){const c=e.target.closest('.card');setAck(c.dataset.id,ck.checked);c.classList.toggle('new',!ck.checked);e.stopPropagation();return;}
  const hd=e.target.closest('.head');if(hd){hd.parentElement.classList.toggle('open');}
});
['q','fflag'].forEach(id=>document.getElementById(id).addEventListener('input',draw));
['fnew','fcold'].forEach(id=>document.getElementById(id).addEventListener('change',draw));
document.getElementById('ackall').addEventListener('click',()=>{(D.rows||[]).forEach(r=>setAck(r.entity_id,true));draw();});
document.getElementById('foot').textContent='Active universe $'+((D.meta.floor_usd||0)/1e6).toFixed(0)+'M–$'+((D.meta.ceiling_usd||0)/1e9).toFixed(1)+'B · '+(D.rows||[]).length+' scored (prompt '+(D.meta.digest_pv||'')+') · acknowledgements stored locally · indicative, not investment advice.';
tally();draw();
"""

_TEMPLATE = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
             "<meta name=viewport content='width=device-width,initial-scale=1'>"
             "<title>Early-detection pipeline — status</title><style>%%CSS%%</style></head><body>"
             "<div class=wrap>%%DASH%%%%DIGEST%%</div>"
             "<script>window.__DATA=%%DATA%%;</script><script>%%JS%%</script></body></html>")


def render(d: dict) -> str:
    data = {"rows": d["rows"], "meta": {"digest_pv": d["digest_pv"], "model": d["model"],
                                        "floor_usd": d["floor_usd"], "ceiling_usd": d["ceiling_usd"]}}
    return (_TEMPLATE.replace("%%CSS%%", _CSS).replace("%%DASH%%", _dashboard(d))
            .replace("%%DIGEST%%", _DIGEST)
            .replace("%%DATA%%", json.dumps(data, default=str))
            .replace("%%JS%%", _JS))


def main() -> int:
    cfg = load_config()
    store = Store(cfg.db_path)
    d = gather(store, cfg)
    store.close()
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(render(d), encoding="utf-8")
    print(f"wrote {_OUT}  (active={d['active']} countries={len(d['by_country'])} "
          f"cleared={d['cleared_clin']} scored={len(d['rows'])} digest_pv={d['digest_pv']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
