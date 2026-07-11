"""Interactive HTML digest of the scored candidates — template + data-sidecar split.

Repo convention (memory: html-template-data-split): a stable, hash-versioned ``digest.html`` template
(CSS + JS + DOM skeleton) plus a ``digest_data.js`` sidecar (``window.__DATA = …``) rewritten every
run. The HTML loads the sidecar via a sibling ``<script src>`` so it works under ``file://``
(double-click from Explorer) — no local server needed.

The UI mirrors the house reports (3_Biopharm / 6_Biotech):
  * **conviction sections** — candidates grouped by flag (deep-dive-candidate → surveil → deprioritize),
    deep-dive first, sorted by conviction_score.
  * **green highlight + acknowledge checkbox** — every candidate starts "new" (pale-green); ticking it
    (persisted in ``localStorage``) clears the highlight.
  * **collapsible detail** — click a card to expand the mechanism summary, convergence dimensions,
    base-rate context, caveats, and the raw evidence (independent citations, crossing funds, founders).
  * **filters** — search, flag filter, new-only, and **cold-discovery-only** (the under-recognized end).

Security (memory: security-by-default): the sidecar is JSON (no HTML), the template escapes everything
client-side (``escapeHtml``), and there are no external resources or hrefs — nothing here can execute
injected markup. yfinance/OpenAlex/Claude-derived text is rendered as text only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import Config
from .store import Store, now_iso


# ── data sidecar ──────────────────────────────────────────────────────────────

def _sidecar_data(store: Store, cfg: Config) -> dict:
    rows = []
    for r in store.top_scores(cfg.scoring_prompt_version, limit=500):
        j = r.get("json") or {}
        rows.append({
            "entity_id": r["entity_id"],
            "ticker": r["ticker_primary"],
            "name": r["legal_name"],
            "jurisdiction": r["jurisdiction"],
            "market_cap_usd": r["market_cap_usd"],
            "in_existing_universe": bool(r["in_existing_universe"]),
            "flag": r["conviction_flag"],
            "score": r["conviction_score"],
            "mechanism_summary": j.get("mechanism_summary"),
            "independent_validation": j.get("independent_validation_status"),
            "dimensions": j.get("stack_convergence_dimensions") or [],
            "base_rate": j.get("base_rate_context"),
            "caveats": j.get("confidence_caveats") or [],
            "evidence": store.evidence_summary(r["entity_id"]),
        })
    return {
        "meta": {"prompt": cfg.scoring_prompt_version, "model": cfg.scoring_model,
                 "generated": now_iso(), "count": len(rows),
                 "floor_usd": cfg.mktcap_floor_usd, "ceiling_usd": cfg.mktcap_ceiling_usd},
        "rows": rows,
    }


# ── template (CSS + skeleton + JS) — stable across runs, hash-versioned ─────────

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#1a2230;--muted:#5b6675;--line:#e4e8ee;--accent:#2a6df0;
 --deep:#0e7a4b;--deep-bg:#e7f6ee;--surv:#8a6d1a;--surv-bg:#fbf3dd;--depri:#7a2323;--depri-bg:#fbe9e9;
 --new:#f0fbf3;--cold:#eef4ff;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{padding:20px 24px;background:var(--card);border-bottom:1px solid var(--line)}
h1{margin:0 0 4px;font-size:19px}
.sub{color:var(--muted);font-size:12.5px}
.wrap{max-width:980px;margin:0 auto;padding:18px 24px 60px}
.controls{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:14px 0}
.controls input[type=search],.controls select{padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:#fff;font-size:13px}
.controls label{display:inline-flex;gap:5px;align-items:center;color:var(--muted);font-size:12.5px}
.tally{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 4px}
.pill{border:1px solid var(--line);background:#fff;border-radius:999px;padding:3px 10px;font-size:12px;color:var(--muted)}
.sec{margin:22px 0 8px;font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:10px 0;overflow:hidden}
.card.new{background:var(--new)}
.head{display:flex;align-items:center;gap:12px;padding:12px 14px;cursor:pointer}
.tick{font-weight:700;font-size:15px;min-width:64px}
.nm{flex:1;min-width:0}
.nm .co{font-weight:600}
.nm .meta{color:var(--muted);font-size:12px;margin-top:1px}
.score{font-weight:700;font-size:17px;min-width:34px;text-align:right}
.badge{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;white-space:nowrap}
.b-deep{background:var(--deep-bg);color:var(--deep)}
.b-surv{background:var(--surv-bg);color:var(--surv)}
.b-depri{background:var(--depri-bg);color:var(--depri)}
.b-cold{background:var(--cold);color:var(--accent)}
.b-star{background:#f4f1ea;color:#7a6a3a}
.ack{margin-left:2px}
.panel{display:none;padding:2px 16px 16px;border-top:1px solid var(--line);background:#fff}
.card.open .panel{display:block}
.panel h4{margin:14px 0 5px;font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted)}
.panel p{margin:0 0 6px}
.dims li{margin:2px 0}
.ev{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px}
.ev .k{color:var(--muted)}
.ev b{color:var(--deep)}
.founder{font-size:12.5px;color:var(--muted)}
.founder b{color:var(--ink)}
#no-data{display:none;color:var(--muted);padding:40px 0;text-align:center}
.foot{color:var(--muted);font-size:11.5px;margin-top:24px}
"""

_SKELETON = """
<header>
  <h1>Early-detection digest <span class="sub" id="hmeta"></span></h1>
  <div class="sub">Stack-convergence conviction across independent scientific validation, capital-markets
   signals, and founder lineage. Ranked deep-dive first. Indicative — not investment advice.</div>
</header>
<div class="wrap">
  <div class="controls">
    <input type="search" id="q" placeholder="Search ticker or name…">
    <select id="fflag"><option value="">All convictions</option>
      <option value="deep-dive-candidate">Deep-dive</option>
      <option value="surveil">Surveil</option>
      <option value="deprioritize">Deprioritize</option></select>
    <label><input type="checkbox" id="fnew"> New only</label>
    <label><input type="checkbox" id="fcold"> Cold-discovery only</label>
    <span style="flex:1"></span>
    <button id="ackall" class="pill" style="cursor:pointer">Acknowledge all</button>
  </div>
  <div class="tally" id="tally"></div>
  <div id="list"></div>
  <div id="no-data">No scored candidates yet. Run the founder → literature → score pipeline.</div>
  <div class="foot" id="foot"></div>
</div>
"""

_JS = r"""
const D=window.__DATA||{rows:[],meta:{}};
const AKEY='ed8_ack_v1';
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const acked=()=>{try{return JSON.parse(localStorage.getItem(AKEY)||'{}')}catch(e){return{}}};
const setAck=(id,v)=>{const a=acked();if(v)a[id]=1;else delete a[id];localStorage.setItem(AKEY,JSON.stringify(a))};
const cap=v=>v==null?'cap unknown':('$'+(v>=1e9?(v/1e9).toFixed(1)+'B':(v/1e6).toFixed(0)+'M'));
const FLAGS=[['deep-dive-candidate','Deep-dive candidates','b-deep'],['surveil','Surveil','b-surv'],['deprioritize','Deprioritize','b-depri']];
function badgeFlag(f){const m={'deep-dive-candidate':'b-deep','surveil':'b-surv','deprioritize':'b-depri'};return '<span class="badge '+(m[f]||'')+'">'+esc(f)+'</span>'}
function tierBadge(r){return r.in_existing_universe?'<span class="badge b-star">★ existing-universe</span>':'<span class="badge b-cold">cold-discovery</span>'}
function passes(r){
  const q=document.getElementById('q').value.trim().toLowerCase();
  if(q&&!((r.ticker||'').toLowerCase().includes(q)||(r.name||'').toLowerCase().includes(q)))return false;
  const ff=document.getElementById('fflag').value; if(ff&&r.flag!==ff)return false;
  if(document.getElementById('fnew').checked&&acked()[r.entity_id])return false;
  if(document.getElementById('fcold').checked&&r.in_existing_universe)return false;
  return true;
}
function ev(r){
  const e=r.evidence||{};const L=e.literature||{};
  const funds=(e.specialist_fund_crossings||[]);
  const founders=(e.founders||[]);
  let h='<h4>Evidence</h4><div class="ev">';
  h+='<span class="k">Independent-lab citations</span> <b>'+(L.independent_citations||0)+'</b>';
  h+='<span class="k">same-institution</span> '+(L.same_institution_citations||0);
  h+='<span class="k">recent pubs</span> '+(L.recent_publications||0);
  if(funds.length)h+='<span class="k">funds</span> '+esc(funds.join(', '));
  h+='</div>';
  if(founders.length){h+='<h4>Founders</h4>';founders.forEach(f=>{
    h+='<div class="founder"><b>'+esc(f.name)+'</b> — '+esc(f.role||'')+(f.institution?' · '+esc(f.institution):'')+
       (f.openalex_author_id?' · <span style="color:var(--deep)">resolved</span>':'')+'</div>';});}
  return h;
}
function card(r){
  const isNew=!acked()[r.entity_id];
  let h='<div class="card'+(isNew?' new':'')+'" data-id="'+esc(r.entity_id)+'">';
  h+='<div class="head">';
  h+='<div class="tick">'+esc(r.ticker||'—')+'</div>';
  h+='<div class="nm"><div class="co">'+esc(r.name)+'</div><div class="meta">'+esc(r.jurisdiction||'')+' · '+cap(r.market_cap_usd)+' · '+badgeFlag(r.flag)+' '+tierBadge(r)+' · <span class="k">'+esc(r.independent_validation||'')+'</span></div></div>';
  h+='<div class="score">'+(r.score==null?'':r.score)+'</div>';
  h+='<input type="checkbox" class="ack" title="Acknowledge"'+(isNew?'':' checked')+'>';
  h+='</div><div class="panel">';
  if(r.mechanism_summary)h+='<h4>Mechanism</h4><p>'+esc(r.mechanism_summary)+'</p>';
  if(r.dimensions&&r.dimensions.length){h+='<h4>Stack convergence</h4><ul class="dims">';r.dimensions.forEach(d=>h+='<li>'+esc(d)+'</li>');h+='</ul>';}
  if(r.base_rate)h+='<h4>Base rate</h4><p>'+esc(r.base_rate)+'</p>';
  h+=ev(r);
  if(r.caveats&&r.caveats.length){h+='<h4>Caveats</h4><ul class="dims">';r.caveats.forEach(c=>h+='<li>'+esc(c)+'</li>');h+='</ul>';}
  h+='</div></div>';
  return h;
}
function draw(){
  const rows=(D.rows||[]).filter(passes);
  const list=document.getElementById('list');
  document.getElementById('no-data').style.display=(D.rows||[]).length?'none':'';
  let h='';
  FLAGS.forEach(([f,label])=>{
    const g=rows.filter(r=>r.flag===f).sort((a,b)=>(b.score||0)-(a.score||0));
    if(!g.length)return;
    h+='<div class="sec">'+esc(label)+' ('+g.length+')</div>';
    g.forEach(r=>h+=card(r));
  });
  list.innerHTML=h||'<div class="foot">No candidates match the filters.</div>';
}
function tally(){
  const rows=D.rows||[];const by={};rows.forEach(r=>by[r.flag]=(by[r.flag]||0)+1);
  const cold=rows.filter(r=>!r.in_existing_universe).length;
  let h='<span class="pill">'+rows.length+' scored</span>';
  FLAGS.forEach(([f,l,c])=>{if(by[f])h+='<span class="pill"><span class="badge '+c+'">'+by[f]+'</span> '+esc(l)+'</span>';});
  h+='<span class="pill"><span class="badge b-cold">'+cold+'</span> cold-discovery</span>';
  document.getElementById('tally').innerHTML=h;
}
document.addEventListener('click',e=>{
  const ck=e.target.closest('.ack');
  if(ck){const c=e.target.closest('.card');setAck(c.dataset.id,ck.checked);c.classList.toggle('new',!ck.checked);e.stopPropagation();return;}
  const hd=e.target.closest('.head');
  if(hd){hd.parentElement.classList.toggle('open');}
});
['q','fflag'].forEach(id=>document.getElementById(id).addEventListener('input',draw));
['fnew','fcold'].forEach(id=>document.getElementById(id).addEventListener('change',draw));
document.getElementById('ackall').addEventListener('click',()=>{(D.rows||[]).forEach(r=>setAck(r.entity_id,true));draw();});
document.getElementById('hmeta').textContent='· '+(D.meta.count||0)+' candidates · model '+(D.meta.model||'')+' · '+((D.meta.generated||'').replace('T',' ').replace('+00:00','Z'));
document.getElementById('foot').textContent='Active universe $'+((D.meta.floor_usd||0)/1e6).toFixed(0)+'M–$'+((D.meta.ceiling_usd||0)/1e9).toFixed(1)+'B · acknowledgements stored locally in your browser.';
tally();draw();
"""

_HTML = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
         "<meta name=viewport content='width=device-width,initial-scale=1'>"
         "<title>Early-detection digest</title><style>{css}</style></head><body>"
         "{skeleton}"
         "<script src=\"digest_data.js\"></script><script>{js}</script>"
         "</body></html>")


def write_report(store: Store, cfg: Config, path: str | Path) -> Path:
    """Write the HTML template + the ``digest_data.js`` sidecar (rewritten every run). Returns the
    HTML path. Read-only w.r.t. the store; no network."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _sidecar_data(store, cfg)
    sidecar = path.with_name(path.stem + "_data.js")
    sidecar.write_text("window.__DATA = " + json.dumps(data, default=str) + ";\n", encoding="utf-8")
    html = _HTML.format(css=_CSS, skeleton=_SKELETON, js=_JS)
    # hash-versioned comment so the stable template is diff-visible when CSS/JS/skeleton change
    version = hashlib.sha1((_CSS + _JS + _SKELETON).encode()).hexdigest()[:12]
    html = html.replace("<body>", f"<body><!-- template {version} -->")
    path.write_text(html, encoding="utf-8")
    return path
