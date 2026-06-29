"""Run summary / observability (spec §15) — a per-run Markdown digest of the whole funnel.

Read-only: it reconstructs the run from the audit log + run_meta + scores (no API, no writes). Emits
``Outputs/run_<run_id>_summary.md`` plus a stable ``Outputs/run_summary.md``: universe size at each
stage, tier breakdown, the ranked shortlist, **top movers vs the previous score run**, seed-validation
precision/recall, and cost. Designed for the weekly-monitor cadence (the spec §15 "top movers" view).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import seed_eval, stage5, tiering
from .store import Store, now_iso, today_iso


def _latest(store: Store, stage: str, reason: str) -> dict:
    row = store.conn.execute(
        "SELECT detail_json FROM audit_log WHERE stage=? AND reason=? ORDER BY ts DESC LIMIT 1",
        (stage, reason)).fetchone()
    return json.loads(row["detail_json"]) if row and row["detail_json"] else {}


def movers(store: Store, limit: int = 8) -> dict[str, list[dict]]:
    """Per-company composite delta between its two most recent score runs. Returns {up, down}
    (each a list of {ticker, name, prev, latest, delta}), biggest moves first."""
    rows = store.conn.execute(
        "SELECT company_id, run_id, composite FROM scores WHERE composite IS NOT NULL "
        "ORDER BY company_id, run_id").fetchall()
    by_co: dict[str, list[tuple]] = {}
    for r in rows:
        by_co.setdefault(r["company_id"], []).append((r["run_id"], r["composite"]))
    out: list[dict] = []
    for cid, hist in by_co.items():
        if len(hist) < 2:
            continue
        prev, latest = hist[-2][1], hist[-1][1]
        co = store.get_company(cid)
        if co is None:
            continue
        out.append({"ticker": co.primary_ticker, "name": co.name,
                    "prev": prev, "latest": latest, "delta": round(latest - prev, 4)})
    ups = sorted([m for m in out if m["delta"] > 0], key=lambda m: -m["delta"])[:limit]
    downs = sorted([m for m in out if m["delta"] < 0], key=lambda m: m["delta"])[:limit]
    return {"up": ups, "down": downs}


def _fmt(v, nd=3):
    return "—" if v is None else f"{v:.{nd}f}"


def build_run_summary(store: Store, config: Optional[dict] = None, *,
                      run_id: Optional[str] = None, today=None) -> str:
    config = config or {}
    rid = run_id or now_iso()
    s0a = _latest(store, "stage0a", "universe_assembled")
    s0b = _latest(store, "stage0b", "hard_cuts_done")
    s1 = _latest(store, "stage1", "tagging_done")
    s2 = _latest(store, "stage2", "harvest_done")
    s4 = _latest(store, "stage4", "scoring_done")
    ranked = stage5.rank(store, config, today=today)
    live = [c for c in store.all_companies() if c.is_live]
    bd = tiering.tier_breakdown(live, config, today=today)
    mv = movers(store)
    try:
        seed = seed_eval.evaluate(store, config)["metrics"]
    except Exception:                                   # seed_labels.csv absent → skip the section
        seed = None
    top_n = int((config.get("stage5", {}) or {}).get("shortlist_top", 25))

    p: list[str] = []
    p.append("# Acrivon-Pattern Screener — Run Summary\n")
    p.append(f"*Run `{rid}` · generated {today_iso()}.*\n")

    p.append("## Funnel\n")
    p.append("| Stage | Metric | Value |")
    p.append("|:--|:--|--:|")
    p.append(f"| 0a universe | records in → admitted | {s0a.get('records_in','—')} → {s0a.get('admitted','—')} |")
    p.append(f"| 0b hard cuts | kept | {s0b.get('kept','—')} |")
    p.append(f"| 0b hard cuts | deleted (mktcap / not-live) | {s0b.get('deleted_mktcap_out_of_band',0)} / {s0b.get('deleted_not_live',0)} |")
    p.append(f"| 0b hard cuts | flagged unknown cap / near-band | {s0b.get('flagged_mktcap_unknown',0)} / {s0b.get('near_band_review',0)} |")
    if s1:
        p.append(f"| 1 tagging | tagged / no-TA-tag | {s1.get('tagged','—')} / {s1.get('no_ta_tag','—')} |")
    if s2:
        p.append(f"| 2 harvest | companies / harvested | {s2.get('companies','—')} / {s2.get('harvested','—')} |")
    if s4:
        p.append(f"| 4 scoring | scored / triage-killed | {s4.get('scored','—')} / {s4.get('triage_killed','—')} |")
        p.append(f"| 4 scoring | web searches / cost | {s4.get('web_searches','—')} / ${s4.get('cost_usd','—')} |")
    p.append(f"| 5 export | shortlist rows | {len(ranked)} |")
    p.append("")

    p.append("## Tier breakdown (live universe)\n")
    p.append("| " + " | ".join(tiering.tier_label(t) for t in tiering.ALL_TIERS) + " |")
    p.append("|" + "--:|" * len(tiering.ALL_TIERS))
    p.append("| " + " | ".join(str(bd.get(t, 0)) for t in tiering.ALL_TIERS) + " |")
    p.append("")

    p.append(f"## Shortlist (top {min(top_n, len(ranked))})\n")
    if ranked:
        p.append("| # | Ticker | Composite | Moat | Substance | Name |")
        p.append("|--:|:--|--:|:--|:--|:--|")
        for e in ranked[:top_n]:
            r = e["rubric"]
            moat = (r.get("moat_location") or {}).get("data_vs_architecture")
            subst = (r.get("substance_check") or {}).get("verdict")
            p.append(f"| {e['rank']} | **{e['company'].primary_ticker or '—'}** | "
                     f"{_fmt(e['composite'])} | {moat or '—'} | {subst or '—'} | {e['company'].name} |")
    else:
        p.append("*No scored companies yet.*")
    p.append("")

    p.append("## Top movers vs previous run\n")
    if mv["up"] or mv["down"]:
        p.append("| Ticker | Prev | Latest | Δ |")
        p.append("|:--|--:|--:|--:|")
        for m in mv["up"] + mv["down"]:
            sign = "+" if m["delta"] > 0 else ""
            p.append(f"| **{m['ticker'] or '—'}** | {_fmt(m['prev'])} | {_fmt(m['latest'])} "
                     f"| {sign}{_fmt(m['delta'])} |")
    else:
        p.append("*No company has two scored runs yet — movers appear after a re-score.*")
    p.append("")

    if seed is not None:
        p.append("## Seed validation (§13)\n")
        p.append(f"- Precision {_fmt(seed['precision'])} · Recall {_fmt(seed['recall'])} · "
                 f"F1 {_fmt(seed['f1'])} (TP={seed['tp']} FP={seed['fp']} FN={seed['fn']} TN={seed['tn']})")
        if seed.get("spec_failure"):
            p.append(f"- ⛔ SPEC FAILURE — positives lost: {seed['stage0_deleted_positives']}")
        if seed.get("graduated_positives"):
            p.append(f"- ℹ️ Graduated (accepted): {seed['graduated_positives']}")
        p.append("")
    return "\n".join(p)


def write_run_summary(store: Store, out_dir: str | Path = "Outputs",
                      config: Optional[dict] = None, *, run_id: Optional[str] = None) -> Path:
    """Write Outputs/run_<id>_summary.md + the stable Outputs/run_summary.md. Returns the stamped path."""
    rid = run_id or now_iso()
    md = build_run_summary(store, config, run_id=rid)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = rid.replace(":", "").replace("+", "")
    stamped = out_dir / f"run_{safe}_summary.md"
    stamped.write_text(md, encoding="utf-8")
    (out_dir / "run_summary.md").write_text(md, encoding="utf-8")
    return stamped
