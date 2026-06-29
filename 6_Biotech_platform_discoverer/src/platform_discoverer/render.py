"""Interactive HTML report of the screener — template + data-sidecar split.

Repo convention (memory: html-template-data-split): a stable, hash-versioned ``screener_report.html``
template (CSS + JS + DOM skeleton) plus a ``screener_report_data.js`` sidecar (``window.__DATA = …``)
rewritten every run. The HTML loads the sidecar via a sibling ``<script src>`` so it works under
``file://`` (double-click from Explorer) — no local server needed.

The UI mirrors ``3_Biopharmcatalyst_parser``'s catalyst-scores report features:
  * **tier tabs** — companies grouped by the market-cap × age tiers (``tiering``); Tier 1 first.
  * **green highlight + acknowledge checkbox** — every company starts "new" (pale-green); the user
    ticks it (persisted in ``localStorage``) once reviewed and the highlight clears.
  * **collapsible Claude results** — click a row to expand its rubric memo / axes / moat / mechanisms.
  * **new-items filter** — show only new (unacknowledged) or only acknowledged rows.
  * **triaged-out rows (D20)** — companies Haiku killed at triage (no rubric) render RED and sink to the
    bottom of every tier, with the kill reason in the expand panel; distinct from never-scored names.

Security: the sidecar is JSON (no HTML), the template escapes everything client-side (``escapeHtml``),
and there are no external resources or hrefs — nothing here can execute injected markup.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from . import stage5, tiering
from .scoring.rubric import AXES
from .store import Store, now_iso


# ── data sidecar ──────────────────────────────────────────────────────────────

def _latest_summary(store: Store, stage: str, reason: str) -> dict:
    row = store.conn.execute(
        "SELECT detail_json FROM audit_log WHERE stage=? AND reason=? ORDER BY ts DESC LIMIT 1",
        (stage, reason)).fetchone()
    if row and row["detail_json"]:
        return json.loads(row["detail_json"])
    return {}


def _triaged_out(store: Store) -> dict[str, str]:
    """{company_id: reason} for every company killed at Haiku triage (audit stage4/cut/triage_kill).
    Latest kill wins. Used to render triaged-out companies red + at the bottom (vs never-scored)."""
    out: dict[str, str] = {}
    for r in store.conn.execute(
            "SELECT company_id, detail_json FROM audit_log "
            "WHERE stage='stage4' AND action='cut' AND reason='triage_kill' ORDER BY ts"):
        d = json.loads(r["detail_json"]) if r["detail_json"] else {}
        out[r["company_id"]] = d.get("why") or "killed at Haiku triage (no rubric run)"
    return out


def _row_for(company, score, merged_ids, config, today) -> dict:
    rubric = (score.json if score else {}) or {}
    row: dict[str, Any] = {
        "company_id": company.company_id,
        "ticker": company.primary_ticker,
        "name": company.name,
        "exchange": company.exchange,
        "country": company.country,
        "mktcap_usd_fd": company.mktcap_usd_fd,
        "ipo_date": company.ipo_date,
        "age_years": tiering.age_years(company.ipo_date, today),
        "tier": tiering.compute_tier(company, config, today=today),
        "sector": company.sector,
        "industry": company.industry,
        "ta_tags": company.ta_tags or [],
        "mktcap_unknown": bool(company.mktcap_unknown),
        "stage1_excluded": bool(company.stage1_excluded),
        "merged_ids": merged_ids,
        "scored": score is not None,
        "triaged_out": False,        # set in build_data for companies Haiku killed before the rubric
    }
    if score is not None:
        moat = rubric.get("moat_location") or {}
        subst = rubric.get("substance_check") or {}
        dmech = rubric.get("D_mechanism") or {}
        a = rubric.get("A_proprietary_data") or {}
        row.update({
            "composite": score.composite,
            "confidence": score.confidence,
            "model": score.model,
            "lifecycle_weight": stage5.lifecycle_weight(company, config, today=today),
            "axes": {k[0]: (rubric.get(k) or {}).get("score") for k in AXES},
            "moat": moat.get("data_vs_architecture"),
            "moat_rationale": moat.get("rationale"),
            "substance": subst.get("verdict"),
            "disconfirming": subst.get("disconfirming_evidence"),
            "mechanism_ids": dmech.get("mechanism_ids") or [],
            "modality": a.get("modality"),
            "scale_evidence": a.get("scale_evidence"),
            "memo": rubric.get("memo"),
        })
    return row


def build_data(store: Store, config: Optional[dict] = None, *, today=None) -> dict:
    """Assemble the ``window.__DATA`` sidecar payload: meta + funnel + tier breakdown + one row per
    real company (duplicate company-ids collapsed via the Stage-5 shared-signal union)."""
    config = config or {}
    review_ids = {r["company_id"] for r in store.review_queue_dump()}
    scores = store.latest_scores()
    triaged = _triaged_out(store)            # company_id -> kill reason (Haiku triage, no rubric)
    live = [c for c in store.all_companies() if c.is_live]
    entries = [{"company": c, "score": scores.get(c.company_id)} for c in live]

    rows: list[dict] = []
    for members in stage5._group_by_signals(entries):
        scored = sorted((m for m in members if m["score"] is not None),
                        key=lambda m: (m["score"].composite or 0.0), reverse=True)
        if scored:
            rep = scored[0]
        else:
            rep = sorted(members, key=lambda m: ((m["company"].mktcap_usd_fd or 0.0),
                                                 1 if m["company"].primary_ticker else 0),
                         reverse=True)[0]
        merged = [m["company"].company_id for m in members if m is not rep]
        row = _row_for(rep["company"], rep["score"], merged, config, today)
        row["review"] = rep["company"].company_id in review_ids or any(
            m["company"].company_id in review_ids for m in members)
        # A company evaluated by Haiku triage and killed (no rubric) — show it red, at the bottom, so the
        # work done is visible and it's distinct from a never-scored name. Scored takes precedence.
        if not row["scored"]:
            hit = next((m["company"].company_id for m in members
                        if m["company"].company_id in triaged), None)
            if hit:
                row["triaged_out"] = True
                row["triage_reason"] = triaged[hit]
        rows.append(row)

    s0b = _latest_summary(store, "stage0b", "hard_cuts_done")
    band = (config.get("market_cap", {}) or {})
    tcfg = (config.get("tiers", {}) or {})
    breakdown = {str(t): 0 for t in tiering.ALL_TIERS}
    for r in rows:
        breakdown[str(r["tier"])] += 1
    return {
        "generated_at": now_iso(),
        "band": {"min": band.get("min_usd"), "max": band.get("max_usd")},
        "tier_thresholds": {
            "mktcap_usd": tcfg.get("mktcap_threshold_usd", 400_000_000),
            "ipo_age_years": tcfg.get("ipo_age_threshold_years", 20),
        },
        "tier_labels": {str(t): tiering.tier_label(t) for t in tiering.ALL_TIERS},
        "funnel": {
            "live": len(live),
            "companies_shown": len(rows),
            "scored": sum(1 for r in rows if r["scored"]),
            "triaged_out": sum(1 for r in rows if r.get("triaged_out")),
            "deleted_mktcap": s0b.get("deleted_mktcap_out_of_band", 0),
            "deleted_not_live": s0b.get("deleted_not_live", 0),
            "flagged_unknown_cap": s0b.get("flagged_mktcap_unknown", 0),
        },
        "tier_breakdown": breakdown,
        "rows": rows,
    }


# ── template (CSS + JS + skeleton) ────────────────────────────────────────────

_CSS = """
:root{--bg:#0d1117;--elev:#161b22;--rowhi:#11161d;--border:#21262d;--text:#c9d1d9;
  --dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--amber:#d29922;--red:#f85149}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
  font:13.5px/1.45 -apple-system,Segoe UI,Roboto,sans-serif;padding:20px 22px 60px}
h1{font-size:20px;margin:0 0 4px}
.meta{color:var(--dim);font-size:12px;margin-bottom:14px}
.meta code{background:var(--elev);padding:2px 6px;border-radius:4px;color:var(--accent);font-size:11.5px}
.note{background:var(--elev);border:1px solid var(--border);border-left:3px solid var(--green);
  border-radius:6px;padding:9px 13px;color:var(--dim);font-size:12px;margin:10px 0 16px}
.cards{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 18px}
.card{background:var(--elev);border:1px solid var(--border);border-radius:8px;padding:9px 14px;min-width:110px}
.card .n{font-size:21px;font-weight:600;color:#e6edf3}
.card .l{font-size:10.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
.card.good .n{color:var(--green)}.card.cut .n{color:var(--red)}.card.flag .n{color:var(--amber)}
.tabs{display:flex;gap:4px;margin-bottom:12px;border-bottom:1px solid var(--border);flex-wrap:wrap}
.tab{background:transparent;color:var(--dim);border:1px solid transparent;border-bottom:none;
  padding:8px 14px;border-radius:6px 6px 0 0;cursor:pointer;font-size:13px}
.tab:hover{color:var(--text)}
.tab.active{background:var(--elev);color:var(--text);border-color:var(--border);position:relative;top:1px}
.tab .count{background:var(--rowhi);color:var(--dim);margin-left:6px;padding:1px 7px;
  border-radius:999px;font-size:11px}
.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:center;background:var(--elev);
  border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:12px}
.filters label{color:var(--dim);font-size:12px}
.filters input,.filters select{background:var(--bg);color:var(--text);border:1px solid var(--border);
  border-radius:5px;padding:4px 8px;font-size:12.5px}
.filters input[type=text]{min-width:150px}
.filters .reset{background:var(--rowhi);color:var(--dim);border:1px solid var(--border);
  border-radius:5px;padding:4px 10px;cursor:pointer}
.filters .reset:hover{color:var(--text)}
.filters .vis{color:var(--dim);font-size:12px;margin-left:auto}
table{width:100%;border-collapse:collapse;table-layout:fixed}
thead th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.03em;color:var(--dim);
  border-bottom:1px solid var(--border);padding:6px 6px;cursor:pointer;user-select:none;
  white-space:normal;line-height:1.2;vertical-align:bottom;position:sticky;top:0;background:var(--bg);z-index:1}
thead th:hover{color:var(--text)}
thead th.sa::after{content:" \\25B2";color:var(--accent)}
thead th.sd::after{content:" \\25BC";color:var(--accent)}
tbody tr{border-bottom:1px solid var(--border);cursor:pointer}
tbody tr:hover{background:var(--rowhi)}
tbody tr.expanded{background:var(--rowhi)}
tbody tr.unack{background:rgba(63,185,80,.08)}
tbody tr.unack:hover,tbody tr.unack.expanded{background:rgba(63,185,80,.14)}
tr.expand-row.unack>td{background:rgba(63,185,80,.06)}
tbody tr.triaged{background:rgba(248,81,73,.09)}
tbody tr.triaged:hover,tbody tr.triaged.expanded{background:rgba(248,81,73,.16)}
tbody tr.triaged .ticker{color:var(--red)}
tbody tr.triaged td{color:var(--dim)}
tr.expand-row.triaged>td{background:rgba(248,81,73,.06)}
tbody td{padding:7px 6px;vertical-align:top;overflow-wrap:anywhere}
th:nth-child(1),td:nth-child(1){width:3%}
th:nth-child(2),td:nth-child(2){width:7%}
th:nth-child(3),td:nth-child(3){width:24%}
th:nth-child(4),td:nth-child(4){width:11%}
th:nth-child(5),td:nth-child(5){width:9%}
th:nth-child(6),td:nth-child(6){width:6%}
th:nth-child(7),td:nth-child(7){width:8%}
th:nth-child(8),td:nth-child(8){width:9%}
th:nth-child(9),td:nth-child(9){width:9%}
th:nth-child(10),td:nth-child(10){width:7%}
td .ticker{font-weight:600;color:var(--accent);font-family:ui-monospace,Consolas,monospace}
td .num{font-variant-numeric:tabular-nums}
.score{display:inline-block;min-width:42px;text-align:right;padding:2px 7px;border-radius:4px;
  font-variant-numeric:tabular-nums;font-size:12px}
.score.s80{background:rgba(63,185,80,.22);color:#4ade80}
.score.s60{background:rgba(101,163,13,.22);color:#a3e635}
.score.s40{background:rgba(210,153,34,.22);color:#facc15}
.score.s20{background:rgba(194,65,12,.22);color:#fb923c}
.score.s00{background:rgba(248,81,73,.22);color:var(--red)}
.score.zero{color:var(--dim);background:transparent}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;line-height:1.5;margin:1px 1px}
.tag.tier{background:rgba(88,166,255,.16);color:var(--accent)}
.tag.tier0{background:rgba(139,148,158,.18);color:var(--dim)}
.tag.flag{background:rgba(210,153,34,.16);color:var(--amber)}
.tag.data{background:rgba(63,185,80,.16);color:var(--green)}
.tag.architecture{background:rgba(248,81,73,.16);color:var(--red)}
.tag.mixed{background:rgba(210,153,34,.16);color:var(--amber)}
.tag.substantive{background:rgba(63,185,80,.16);color:var(--green)}
.tag.marketing{background:rgba(248,81,73,.16);color:var(--red)}
.muted{color:var(--dim)}
.expand-panel{background:var(--elev);padding:12px 14px;border-top:1px solid var(--border)}
.expand-panel h4{margin:12px 0 6px;font-size:11.5px;color:var(--dim);text-transform:uppercase;
  letter-spacing:.04em}
.expand-panel h4:first-of-type{margin-top:0}
.expand-panel .kv{display:grid;grid-template-columns:180px 1fr;gap:4px 12px;font-size:12.5px}
.expand-panel .kv .k{color:var(--dim)}
.expand-panel .memo{background:var(--bg);border:1px solid var(--border);border-radius:5px;
  padding:9px 11px;font-size:12.5px;color:var(--text);white-space:pre-wrap;margin-top:4px}
.expand-panel .disc{border-left:3px solid var(--red)}
.ack-toggle{display:inline-flex;align-items:center;gap:6px;padding:4px 8px;margin:0 0 10px;
  font-size:12px;color:var(--dim);cursor:pointer;user-select:none;border-radius:4px;
  background:rgba(63,185,80,.12)}
.ack-toggle:hover{background:rgba(63,185,80,.2)}
.ack-toggle.acked{background:transparent;opacity:.65}
.ack-toggle input{cursor:pointer;margin:0}
.no-rows{color:var(--dim);padding:24px;text-align:center;font-style:italic}
.no-data{background:var(--elev);border:1px dashed var(--border);border-radius:8px;padding:24px;
  color:var(--dim);text-align:center;margin:30px 0}
"""

_JS = r"""
(function(){
  const FKEY='pd_screener_filters_v1';
  const AKEY='pd_screener_acknowledged_v1';
  function loadFilters(){try{return JSON.parse(localStorage.getItem(FKEY))||{}}catch{return{}}}
  function saveFilters(f){try{localStorage.setItem(FKEY,JSON.stringify(f))}catch{}}

  // acknowledged ("seen") set — PK = company_id, persisted across sessions. Every company starts
  // NEW (unacknowledged → pale-green) until the user ticks it.
  const ack=new Set();
  try{const raw=localStorage.getItem(AKEY);if(raw)JSON.parse(raw).forEach(k=>ack.add(k))}catch{}
  function saveAck(){try{localStorage.setItem(AKEY,JSON.stringify(Array.from(ack)))}catch{}}
  function isNew(r){return !ack.has(r.company_id)}

  const state=Object.assign({tab:'1',minComposite:0,tickerFilter:'',reviewStatus:'any',
    scoredOnly:false,sortCol:'composite',sortDir:'desc'},loadFilters());
  state.expandedId=null;

  function esc(s){if(s==null)return '';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
  function scoreClass(s){if(s==null)return 'zero';const v=s*100;if(v>=80)return 's80';
    if(v>=60)return 's60';if(v>=40)return 's40';if(v>=20)return 's20';return 's00'}
  function fmtScore(s){if(s==null)return '<span class="score zero">—</span>';
    return '<span class="score '+scoreClass(s)+'">'+s.toFixed(3)+'</span>'}
  function fmtMcap(v){if(v==null)return '<span class="muted">—</span>';
    if(v>=1e9)return '$'+(v/1e9).toFixed(2)+'B';if(v>=1e6)return '$'+(v/1e6).toFixed(0)+'M';
    return '$'+Math.round(v).toLocaleString()}
  function fmtAge(v){return v==null?'<span class="muted">—</span>':v.toFixed(0)+'y'}
  function tierTag(t){const cls=t===0?'tier0':'tier';
    return '<span class="tag '+cls+'">'+(t===0?'untiered':('T'+t))+'</span>'}

  const TABS=['1','2','3','4','0','all'];
  function tabLabel(t){if(t==='all')return 'All';
    const lbls=(window.__DATA&&window.__DATA.tier_labels)||{};return lbls[t]||('Tier '+t)}
  function rowInTab(r){return state.tab==='all'?true:r.tier===Number(state.tab)}

  function matches(r){
    if(!rowInTab(r))return false;
    if(state.scoredOnly&&!r.scored)return false;
    if(state.minComposite>0&&(r.composite==null||r.composite<state.minComposite))return false;
    if(state.reviewStatus==='new'&&!isNew(r))return false;
    if(state.reviewStatus==='ack'&&isNew(r))return false;
    if(state.tickerFilter){const t=state.tickerFilter.toLowerCase();
      if(!String(r.ticker||'').toLowerCase().includes(t)&&
         !String(r.name||'').toLowerCase().includes(t))return false}
    return true;
  }
  function sortRows(rows){const c=state.sortCol,d=state.sortDir==='asc'?1:-1;
    return rows.slice().sort((a,b)=>{
      // triaged-out companies always sink to the bottom, regardless of the active sort column
      if(!!a.triaged_out!==!!b.triaged_out)return a.triaged_out?1:-1;
      let av=a[c],bv=b[c];
      if(av==null&&bv==null)return 0;if(av==null)return 1;if(bv==null)return -1;
      if(typeof av==='string'&&typeof bv==='string')return av.localeCompare(bv)*d;
      return (av-bv)*d})}

  function renderTabs(){
    const bd=(window.__DATA&&window.__DATA.tier_breakdown)||{};
    const total=(window.__DATA&&window.__DATA.rows||[]).length;
    document.querySelectorAll('.tab').forEach(el=>{const t=el.dataset.tab;
      el.classList.toggle('active',t===state.tab);
      const ce=el.querySelector('.count');if(ce)ce.textContent=(t==='all')?total:(bd[t]||0)})}

  function buildPanel(r){
    let h='<div class="expand-panel">';
    const isn=isNew(r);
    h+='<label class="ack-toggle '+(isn?'':'acked')+'" title="Tick to mark this company as reviewed; '
      +'the green highlight clears.">'
      +'<input type="checkbox" data-ack="'+esc(r.company_id)+'" '+(isn?'':'checked')+'>'
      +'<span>'+(isn?'NEW — click to acknowledge as reviewed':'Acknowledged as reviewed')+'</span></label>';
    h+='<h4>Identity</h4><div class="kv">';
    h+='<div class="k">Exchange / country</div><div>'+esc(r.exchange||'—')+' · '+esc(r.country||'—')+'</div>';
    h+='<div class="k">Market cap</div><div>'+fmtMcap(r.mktcap_usd_fd)+'</div>';
    h+='<div class="k">IPO date / age</div><div>'+esc(r.ipo_date||'—')
      +(r.age_years!=null?(' · ~'+r.age_years.toFixed(1)+' yr'):'')+'</div>';
    h+='<div class="k">Tier</div><div>'+tierTag(r.tier)+' '+esc((window.__DATA.tier_labels||{})[r.tier]||'')+'</div>';
    if((r.ta_tags||[]).length)h+='<div class="k">Mechanism tags</div><div>'
      +r.ta_tags.map(t=>'<span class="tag tier">'+esc(t)+'</span>').join(' ')+'</div>';
    const flags=[];if(r.mktcap_unknown)flags.push('mktcap_unknown');
    if(r.stage1_excluded)flags.push('stage1_excluded');if(r.review)flags.push('review');
    if(flags.length)h+='<div class="k">Flags</div><div>'
      +flags.map(f=>'<span class="tag flag">'+esc(f)+'</span>').join(' ')+'</div>';
    if((r.merged_ids||[]).length)h+='<div class="k">Merged duplicate ids</div><div class="muted">'
      +r.merged_ids.map(esc).join(', ')+'</div>';
    h+='</div>';
    if(r.scored){
      h+='<h4>Claude score ('+esc(r.model||'')+')</h4><div class="kv">';
      const ax=r.axes||{};
      h+='<div class="k">Composite</div><div>'+(r.composite!=null?r.composite.toFixed(3):'—')
        +' · confidence '+(r.confidence!=null?r.confidence.toFixed(2):'—')+'</div>';
      h+='<div class="k">Axes A·B·C·D·E</div><div>'
        +['A','B','C','D','E'].map(k=>ax[k]!=null?ax[k]:'—').join(' · ')+' (0–5)</div>';
      h+='<div class="k">Moat</div><div><span class="tag '+esc(r.moat||'')+'">'+esc(r.moat||'—')+'</span> '
        +esc(r.moat_rationale||'')+'</div>';
      h+='<div class="k">Substance</div><div><span class="tag '+esc(r.substance||'')+'">'
        +esc(r.substance||'—')+'</span></div>';
      if((r.mechanism_ids||[]).length)h+='<div class="k">Mechanisms</div><div>'
        +r.mechanism_ids.map(m=>'<span class="tag tier">'+esc(m)+'</span>').join(' ')+'</div>';
      if(r.modality)h+='<div class="k">Data modality</div><div>'+esc(r.modality)
        +(r.scale_evidence?(' — '+esc(r.scale_evidence)):'')+'</div>';
      h+='</div>';
      if(r.memo)h+='<h4>Memo</h4><div class="memo">'+esc(r.memo)+'</div>';
      if(r.disconfirming)h+='<h4>Disconfirming evidence</h4><div class="memo disc">'
        +esc(r.disconfirming)+'</div>';
    }else if(r.triaged_out){
      h+='<h4>Claude triage</h4><div class="memo disc">Killed at Haiku triage — evaluated by the cheap '
        +'recall-safe triage pass and NOT advanced to the full Sonnet rubric (it did not look like an '
        +'Acrivon-pattern platform match).'+(r.triage_reason?(' Reason: '+esc(r.triage_reason)):'')
        +' Re-run Stage 4 with --force-rescore to re-evaluate.</div>';
    }else{
      h+='<h4>Claude score</h4><div class="muted">Not scored yet — this company is in a tier that '
        +'was not selected for the Claude run (or scoring has not run). Re-run Stage 4 selecting its tier.</div>';
    }
    h+='</div>';return h;
  }

  function renderTable(){
    const rows=(window.__DATA&&window.__DATA.rows)||[];
    const filtered=rows.filter(matches);const sorted=sortRows(filtered);
    document.getElementById('vis').textContent=filtered.length+' of '+rows.length+' companies';
    const tb=document.getElementById('rows-body');
    if(!sorted.length){tb.innerHTML='<tr><td colspan="10" class="no-rows">No companies match the current filters.</td></tr>';return}
    tb.innerHTML=sorted.map((r,i)=>{
      // triaged-out rows render RED (not the green "new" highlight) and carry a 'triaged' class
      const cls=[(isNew(r)&&!r.triaged_out)?'unack':'',r.triaged_out?'triaged':''].filter(Boolean).join(' ');
      const statusCell=r.scored?'<span class="tag substantive">scored</span>'
        :r.triaged_out?'<span class="tag marketing">triaged out</span>'
        :(isNew(r)?'<span class="muted">new</span>':'<span class="muted">—</span>');
      return '<tr data-id="'+esc(r.company_id)+'" class="'+cls+'">'
        +'<td class="num">'+(i+1)+'</td>'
        +'<td><span class="ticker">'+esc(r.ticker||'—')+'</span></td>'
        +'<td>'+esc(r.name||'')+'</td>'
        +'<td>'+tierTag(r.tier)+'</td>'
        +'<td class="num">'+fmtMcap(r.mktcap_usd_fd)+'</td>'
        +'<td class="num">'+fmtAge(r.age_years)+'</td>'
        +'<td>'+fmtScore(r.composite)+'</td>'
        +'<td>'+(r.moat?('<span class="tag '+esc(r.moat)+'">'+esc(r.moat)+'</span>'):'<span class="muted">—</span>')+'</td>'
        +'<td>'+(r.substance?('<span class="tag '+esc(r.substance)+'">'+esc(r.substance)+'</span>'):'<span class="muted">—</span>')+'</td>'
        +'<td>'+statusCell+'</td></tr>';
    }).join('');
    if(state.expandedId!=null){
      const tr=tb.querySelector('tr[data-id="'+CSS.escape(state.expandedId)+'"]');
      if(tr){const r=rows.find(x=>x.company_id===state.expandedId);
        const tr2=document.createElement('tr');
        tr2.className='expand-row'+(tr.classList.contains('unack')?' unack':'')
          +(tr.classList.contains('triaged')?' triaged':'');
        tr2.innerHTML='<td colspan="10">'+buildPanel(r)+'</td>';
        tr.classList.add('expanded');tr.parentNode.insertBefore(tr2,tr.nextSibling);
      }else state.expandedId=null;
    }
  }
  function renderSort(){document.querySelectorAll('thead th').forEach(th=>{
    th.classList.remove('sa','sd');
    if(th.dataset.col===state.sortCol)th.classList.add(state.sortDir==='asc'?'sa':'sd')})}

  function toggleRow(tr){
    const id=tr.dataset.id;const rows=window.__DATA.rows;const r=rows.find(x=>x.company_id===id);
    const nx=tr.nextElementSibling;
    if(nx&&nx.classList.contains('expand-row')){nx.remove();tr.classList.remove('expanded');
      state.expandedId=null;return}
    document.querySelectorAll('tr.expand-row').forEach(e=>e.remove());
    document.querySelectorAll('tr.expanded').forEach(e=>e.classList.remove('expanded'));
    const tr2=document.createElement('tr');
    tr2.className='expand-row'+(tr.classList.contains('unack')?' unack':'')
      +(tr.classList.contains('triaged')?' triaged':'');
    tr2.innerHTML='<td colspan="10">'+buildPanel(r)+'</td>';
    tr.classList.add('expanded');tr.parentNode.insertBefore(tr2,tr.nextSibling);
    state.expandedId=id;
  }

  function renderMeta(){
    const d=window.__DATA;
    document.getElementById('meta').innerHTML=
      'generated <code>'+esc(d.generated_at)+'</code> · band <code>$'
      +(d.band.min||0).toLocaleString()+'–$'+(d.band.max||0).toLocaleString()+'</code> · '
      +'tiers split at <code>$'+(d.tier_thresholds.mktcap_usd).toLocaleString()+'</code> cap / <code>'
      +d.tier_thresholds.ipo_age_years+'y</code> since IPO';
    const f=d.funnel||{};
    document.getElementById('cards').innerHTML=
      card(f.live,'live companies','good')+card(f.scored,'Claude-scored','good')
      +card(f.triaged_out,'triaged out','cut')
      +card(f.companies_shown,'rows (deduped)')+card(f.deleted_mktcap,'del · mkt cap','cut')
      +card(f.deleted_not_live,'del · not live','cut')+card(f.flagged_unknown_cap,'flag · cap?','flag');
  }
  function card(n,l,c){return '<div class="card '+(c||'')+'"><div class="n">'+esc(n==null?'—':n)
    +'</div><div class="l">'+esc(l)+'</div></div>'}

  function bind(){
    document.querySelectorAll('.tab').forEach(el=>el.addEventListener('click',()=>{
      state.tab=el.dataset.tab;saveFilters(state);renderTabs();renderTable()}));
    document.getElementById('f-ticker').addEventListener('input',e=>{
      state.tickerFilter=e.target.value;saveFilters(state);renderTable()});
    document.getElementById('f-mincomp').addEventListener('input',e=>{
      state.minComposite=Number(e.target.value)||0;saveFilters(state);renderTable()});
    document.getElementById('f-review').addEventListener('change',e=>{
      state.reviewStatus=e.target.value;saveFilters(state);renderTable()});
    document.getElementById('f-scored').addEventListener('change',e=>{
      state.scoredOnly=e.target.checked;saveFilters(state);renderTable()});
    document.getElementById('f-reset').addEventListener('click',()=>{
      state.minComposite=0;state.tickerFilter='';state.reviewStatus='any';state.scoredOnly=false;
      saveFilters(state);restoreForm();renderTable()});
    document.querySelectorAll('thead th').forEach(th=>{if(!th.dataset.col)return;
      th.addEventListener('click',()=>{
        if(state.sortCol===th.dataset.col)state.sortDir=state.sortDir==='asc'?'desc':'asc';
        else{state.sortCol=th.dataset.col;state.sortDir='desc'}
        saveFilters(state);renderSort();renderTable()})});
    document.getElementById('rows-body').addEventListener('click',e=>{
      if(e.target.closest('.ack-toggle'))return;
      const tr=e.target.closest('tr[data-id]');if(tr&&!tr.classList.contains('expand-row'))toggleRow(tr)});
    document.getElementById('rows-body').addEventListener('change',e=>{
      const box=e.target.closest('.ack-toggle input[type=checkbox]');if(!box)return;
      const id=box.dataset.ack;if(box.checked)ack.add(id);else ack.delete(id);
      saveAck();renderTabs();renderTable()});
  }
  function restoreForm(){
    document.getElementById('f-ticker').value=state.tickerFilter||'';
    document.getElementById('f-mincomp').value=state.minComposite||0;
    document.getElementById('f-review').value=state.reviewStatus||'any';
    document.getElementById('f-scored').checked=!!state.scoredOnly;
  }

  function init(){
    if(!window.__DATA||!window.__DATA.rows){document.getElementById('no-data').style.display='';return}
    document.getElementById('no-data').style.display='none';
    if(!TABS.includes(state.tab))state.tab='1';
    renderMeta();renderTabs();bind();restoreForm();renderSort();renderTable();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
"""

_SKELETON = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="template-version" content="__VERSION__">
<title>Acrivon-Pattern Screener</title>
<style>__CSS__</style></head><body>
<h1>Acrivon-Pattern Listed-Biotech Screener</h1>
<div class="meta" id="meta">loading…</div>
<div class="note"><b>Cardinal rule (§0.2):</b> a company is only ever deleted for
<code>mktcap_out_of_band</code> or <code>not_live</code>; every other narrowing is a reversible flag.
Rows are grouped by <b>market-cap × age tier</b>; companies start <b>NEW</b> (green) until you tick the
acknowledge box in their expanded panel.</div>
<div id="no-data" class="no-data" style="display:none"><b>No data loaded.</b><br>
This report expects a sibling <code>screener_report_data.js</code>. Run
<code>scripts/6_render.py</code> to generate it, then refresh.</div>
<div class="cards" id="cards"></div>
<div class="tabs">
  <button class="tab" data-tab="1">Tier 1 · small &amp; young<span class="count"></span></button>
  <button class="tab" data-tab="2">Tier 2 · large &amp; young<span class="count"></span></button>
  <button class="tab" data-tab="3">Tier 3 · large &amp; old<span class="count"></span></button>
  <button class="tab" data-tab="4">Tier 4 · small &amp; old<span class="count"></span></button>
  <button class="tab" data-tab="0">Untiered<span class="count"></span></button>
  <button class="tab" data-tab="all">All<span class="count"></span></button>
</div>
<div class="filters">
  <label>Search <input type="text" id="f-ticker" placeholder="ticker or name"></label>
  <label>Min composite <input type="number" id="f-mincomp" min="0" max="1" step="0.05" style="width:70px"></label>
  <label>Review <select id="f-review">
    <option value="any">any</option><option value="new">new only</option>
    <option value="ack">acknowledged only</option></select></label>
  <label><input type="checkbox" id="f-scored"> scored only</label>
  <button class="reset" id="f-reset">reset</button>
  <span class="vis" id="vis"></span>
</div>
<table>
<thead><tr>
  <th>#</th>
  <th data-col="ticker">Ticker</th>
  <th data-col="name">Name</th>
  <th data-col="tier">Tier</th>
  <th data-col="mktcap_usd_fd">Mkt cap</th>
  <th data-col="age_years">Age</th>
  <th data-col="composite">Composite</th>
  <th data-col="moat">Moat</th>
  <th data-col="substance">Substance</th>
  <th>Status</th>
</tr></thead>
<tbody id="rows-body"></tbody>
</table>
<script src="screener_report_data.js"></script>
</body></html>
"""


def build_template() -> str:
    # The sidecar <script src> loads window.__DATA first; the app JS is appended inline right after.
    body = _SKELETON.replace("__CSS__", _CSS).replace(
        '<script src="screener_report_data.js"></script>',
        f'<script src="screener_report_data.js"></script>\n<script>{_JS}</script>')
    version = hashlib.sha1((_CSS + _JS + _SKELETON).encode("utf-8")).hexdigest()[:12]
    return body.replace("__VERSION__", version)


def _template_version(html: str) -> Optional[str]:
    m = re.search(r'name="template-version" content="([^"]+)"', html)
    return m.group(1) if m else None


# ── public API ────────────────────────────────────────────────────────────────

def write_report(store: Store, path: str | Path, config: Optional[dict] = None) -> Path:
    """Write the sidecar (always) + the template HTML (only when its content hash changed).

    ``path`` is the HTML file (e.g. ``Outputs/screener_report.html``); the sidecar is written next to
    it as ``<stem>_data.js``. Returns the HTML path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = build_data(store, config or {})
    sidecar = path.with_name(path.stem + "_data.js")
    sidecar.write_text("window.__DATA = " + json.dumps(data, default=str) + ";\n", encoding="utf-8")

    template = build_template()
    new_v = _template_version(template)
    existing_v = _template_version(path.read_text(encoding="utf-8")) if path.exists() else None
    if existing_v != new_v:
        path.write_text(template, encoding="utf-8")
    return path
