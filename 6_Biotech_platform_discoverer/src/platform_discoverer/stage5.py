"""Stage 5 — rank, dedup, persist, export the shortlist (spec §5.7, §10, §11, §15).

The terminal stage. It does NOT call any API and NEVER deletes a company (cardinal rule §0.2): it
reads the latest persisted ``scores``, collapses duplicate company-ids at the **presentation layer**,
ranks, refreshes the review queue, records run metadata, and writes the analyst-house-format shortlist
to ``Outputs/``.

Three pieces:
  * ``lifecycle_weight`` — the optional, transparent company-age multiplier (decisions.md D2/D4,
    ``stage5.lifecycle``, OFF by default). ``rank_score = composite × weight``. It NEVER alters the
    Claude composite (auditable) and never removes a company — it only reorders.
  * ``rank`` — latest score per company → dedup by ISIN/normalized-name (keep the best-scored
    representative; the others are recorded as ``merged_ids``, not deleted) → sort by rank_score.
  * ``run`` — refresh ``review_queue`` (§11: marketing/mixed, low-confidence-high-composite), persist
    ``run_meta`` metrics (§15), and export the shortlist Markdown.

Dedup here is a SAFETY NET for the seed-CSV-name vs SEC-name double-id case (e.g. ACRV/RXRX): the
primary ADR/dual-listing resolution happens earlier in ``dedup.collapse`` (before company rows exist).
Collapsing at Stage 5 is a ranking/export concern, so it is done WITHOUT deleting rows — honoring the
guardrail while still presenting one row per real company.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Optional

from .listings import normalize_name
from .models import Company, Score
from .store import Store, now_iso, today_iso
from .tiering import age_years as _age_years

log = logging.getLogger(__name__)


# ── lifecycle / company-age multiplier (decisions.md D2/D4) ──────────────────


def lifecycle_weight(company: Company, config: dict, *, today: Optional[date] = None) -> float:
    """Transparent age multiplier in [floor, 1.0] (spec §5.7, decisions.md D2).

    OFF by default → always 1.0. Enabled, the curve favours the "early but real" window and
    deprioritizes companies where the ship has sailed:

      * age ≤ ``old_threshold_years``      → 1.0   (the young are NOT penalized — rule 3a)
      * ``old_threshold`` … ``hard_old``   → linear decay 1.0 → ``floor``
      * age ≥ ``hard_old_years``           → ``floor``   (>20 yr / ship sailed — rule 3b)

    Unknown age (no ``ipo_date`` captured yet) → 1.0 (recall-safe: never penalize for missing data).
    """
    lc = (config.get("stage5", {}) or {}).get("lifecycle", {}) or {}
    if not lc.get("enabled", False):
        return 1.0
    age = _age_years(company.ipo_date, today or date.today())
    if age is None:
        return 1.0
    old = float(lc.get("old_threshold_years", 15))
    hard = float(lc.get("hard_old_years", 20))
    floor = float(lc.get("floor", 0.5))
    if age <= old:
        return 1.0
    if age >= hard or hard <= old:
        return round(floor, 4)
    frac = (age - old) / (hard - old)          # 0 at old_threshold → 1 at hard_old
    return round(1.0 - frac * (1.0 - floor), 4)


# ── dedup + ranking ──────────────────────────────────────────────────────────

# Legal-entity suffixes only (NOT industry words like "therapeutics"/"pharmaceuticals" — stripping
# those would falsely merge distinct companies). Used for the name-only dedup fallback.
_CORP_SUFFIX = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|co|limited|ltd|plc|llc|lp|"
    r"holdings|holding|group|ab|asa|oyj|a\s?s|sa|nv|ag|se)\b")


def _strip_suffix(norm: str) -> str:
    return re.sub(r"\s+", " ", _CORP_SUFFIX.sub(" ", norm)).strip()


def _fold_accents(s: str) -> str:
    """Strip diacritics so a Wikidata 'Galápagos' matches an SEC 'Galapagos' (NFKD + drop combining)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def _dedup_name(name: str) -> str:
    """Accent-folded, suffix-stripped normalized name — the cross-source / cross-listing merge key."""
    return _strip_suffix(normalize_name(_fold_accents(name)))


def _dedup_signals(company: Company) -> set[str]:
    """Identity signals that make two company-ids the SAME real company (spec §5.7).

    Two records merge if they share ANY signal (transitively): ISIN, ticker+country, OR
    accent-folded suffix-stripped name. The NAME signal (D24) is now emitted for EVERY record, not
    only ticker-less ones, so the same company enumerated via two nets — an ADR row ``PRGO@US`` and a
    Wikidata row ``PRGO@Ireland``, or a dual listing ``ZEAL.CO``/``ZEAL`` — collapses to one. Legal
    suffixes are stripped (inc/plc/ab/sa/nv/ag/…) but industry words (therapeutics/pharmaceuticals)
    are NOT, so genuinely distinct companies sharing a stem stay separate. Ticker is still keyed WITH
    country so a cross-exchange symbol clash (e.g. a German vs US ``MRK``) does NOT merge on ticker —
    and won't on name either, since the names differ. ISIN remains the strongest signal.
    """
    sigs: set[str] = set()
    isin = (company.isin or "").strip().upper()
    if isin:
        sigs.add(f"isin:{isin}")
    ticker = (company.primary_ticker or "").strip().upper()
    if ticker:
        sigs.add(f"tkr:{ticker}@{(company.country or '').strip().upper()}")
    name_sig = _dedup_name(company.name)
    if name_sig:
        sigs.add(f"name:{name_sig}")
    return sigs


def _group_by_signals(entries: list[dict]) -> list[list[dict]]:
    """Union entries that share any identity signal (transitive). Order-preserving by first sight."""
    parent: dict[int, int] = {}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    sig_owner: dict[str, int] = {}
    for idx, e in enumerate(entries):
        parent[idx] = idx
        for sig in _dedup_signals(e["company"]):
            if sig in sig_owner:
                union(idx, sig_owner[sig])
            else:
                sig_owner[sig] = idx

    groups: dict[int, list[dict]] = {}
    order: list[int] = []
    for idx, e in enumerate(entries):
        root = find(idx)
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(e)
    return [groups[root] for root in order]


def _rep_sort_key(entry: dict) -> tuple:
    """Pick the representative within a dedup group: best composite, then confidence, then has-ticker."""
    return (entry["composite"] or 0.0, entry["confidence"] or 0.0,
            1 if entry["company"].primary_ticker else 0)


def rank(store: Store, config: dict, *, today: Optional[date] = None) -> list[dict]:
    """Ranked, deduped shortlist from the latest persisted scores. Pure read (no writes).

    Each entry: {company, score, rubric, composite, confidence, lifecycle_weight, rank_score,
    age_years, merged_ids}. Sorted by ``rank_score`` desc. ``merged_ids`` lists the other
    company-ids collapsed into this representative (recorded, never deleted).
    """
    today = today or date.today()
    scores: dict[str, Score] = store.latest_scores()
    companies = {c.company_id: c for c in store.all_companies()}

    entries: list[dict] = []
    for cid, sc in scores.items():
        company = companies.get(cid)
        if company is None:            # scored row whose company was later (legally) deleted
            continue
        entries.append({
            "company": company, "score": sc, "rubric": sc.json or {},
            "composite": sc.composite, "confidence": sc.confidence,
            "age_years": _age_years(company.ipo_date, today),
        })

    # dedup at the presentation layer (no row deletion — cardinal rule §0.2)
    ranked: list[dict] = []
    for members in _group_by_signals(entries):
        members.sort(key=_rep_sort_key, reverse=True)
        rep = members[0]
        rep["merged_ids"] = [m["company"].company_id for m in members[1:]]
        w = lifecycle_weight(rep["company"], config, today=today)
        rep["lifecycle_weight"] = w
        rep["rank_score"] = round((rep["composite"] or 0.0) * w, 4)
        ranked.append(rep)

    ranked.sort(key=lambda e: e["rank_score"], reverse=True)
    for i, e in enumerate(ranked, 1):
        e["rank"] = i
    return ranked


# ── review-queue refresh (§11) ────────────────────────────────────────────────

def _refresh_review_queue(store: Store, ranked: list[dict], config: dict, run_id: str) -> int:
    """Route borderline scored names back to the review queue (§11). Returns count added."""
    rv = (config.get("stage5", {}) or {}).get("review", {}) or {}
    low_conf = float(rv.get("low_confidence_max", 0.5))
    high_comp = float(rv.get("high_composite_min", 0.6))
    flag_mixed = bool(rv.get("flag_marketing_mixed", True))
    added = 0
    for e in ranked:
        verdict = (e["rubric"].get("substance_check") or {}).get("verdict")
        comp = e["composite"] or 0.0
        conf = e["confidence"] or 0.0
        reason = None
        if flag_mixed and verdict in ("marketing", "mixed"):
            reason = f"substance_check={verdict} (composite {comp:.2f})"
        elif comp >= high_comp and conf <= low_conf:
            reason = f"high_composite_low_confidence (composite {comp:.2f}, confidence {conf:.2f})"
        if reason:
            store.add_to_review_queue(e["company"].company_id, reason)
            added += 1
    if added:
        store.audit(stage="stage5", action="flagged", reason="review_refresh", run_id=run_id,
                    detail={"added": added})
    return added


# ── export (analyst house format, spec §5.7 / §15) ───────────────────────────

def _label(s: Optional[str]) -> str:
    return s if s else "—"


def _axis_line(rubric: dict, axis: str) -> str:
    a = rubric.get(axis) or {}
    return f"{a.get('score', '—')}/5"


def build_shortlist_md(ranked: list[dict], config: dict, *, run_id: str,
                       universe_n: Optional[int] = None) -> str:
    """The house-format shortlist: a summary table + dense per-company prose (§5.7).

    ``[V]``/``[INF]`` labels and any prose the Claude memo carries are passed through verbatim
    (Markdown, not HTML — no escaping needed; this file is the analyst's to open, not a web surface).
    """
    top = int((config.get("stage5", {}) or {}).get("shortlist_top", 25))
    lc_on = ((config.get("stage5", {}) or {}).get("lifecycle", {}) or {}).get("enabled", False)
    band = config.get("market_cap", {}) or {}

    p: list[str] = []
    p.append("# Acrivon-Pattern Listed-Biotech Screener — Shortlist\n")
    p.append(f"*Run `{run_id}` · generated {today_iso()} · "
             f"market-cap band ${band.get('min_usd', 0):,}–${band.get('max_usd', 0):,} · "
             f"{len(ranked)} scored companies"
             + (f" (of {universe_n} in store)" if universe_n else "") + ".*\n")
    p.append("Composite is the code-computed weighted rubric score (auditable; §10). "
             + ("`rank_score = composite × lifecycle_weight` (age-weighted ranking ON)."
                if lc_on else
                "`rank_score = composite` (lifecycle age-weighting OFF — `stage5.lifecycle.enabled`).")
             + "\n")

    # ── summary table ──
    p.append("## Ranked shortlist\n")
    hdr = "| # | Ticker | Company | Composite | Rank | Conf | Moat | Substance | Mechanism |"
    sep = "|--:|:--|:--|--:|--:|--:|:--|:--|:--|"
    p.append(hdr)
    p.append(sep)
    for e in ranked[:top]:
        c, r = e["company"], e["rubric"]
        moat = (r.get("moat_location") or {}).get("data_vs_architecture")
        subst = (r.get("substance_check") or {}).get("verdict")
        mech = ", ".join((r.get("D_mechanism") or {}).get("mechanism_ids") or []) or "—"
        p.append(f"| {e['rank']} | **{_label(c.primary_ticker)}** | {_label(c.name)} "
                 f"| {e['composite']:.3f} | {e['rank_score']:.3f} | "
                 f"{(e['confidence'] or 0):.2f} | {_label(moat)} | {_label(subst)} | {mech} |")
    p.append("")

    # ── per-company prose ──
    p.append("## Memos\n")
    for e in ranked[:top]:
        c, r = e["company"], e["rubric"]
        p.append(f"### {e['rank']}. {_label(c.name)} ({_label(c.primary_ticker)}) "
                 f"— composite {e['composite']:.3f}\n")
        meta = [f"exchange {_label(c.exchange)}", f"country {_label(c.country)}"]
        if c.mktcap_usd_fd:
            meta.append(f"mkt cap ${c.mktcap_usd_fd/1e6:,.0f}M")
        if e.get("age_years") is not None:
            meta.append(f"~{e['age_years']:.0f} yr since IPO")
        if e["lifecycle_weight"] != 1.0:
            meta.append(f"lifecycle ×{e['lifecycle_weight']:.2f} → rank {e['rank_score']:.3f}")
        meta.append(f"confidence {(e['confidence'] or 0):.2f}")
        p.append("*" + " · ".join(meta) + "*\n")
        axes = (f"A (proprietary data) {_axis_line(r, 'A_proprietary_data')} · "
                f"B (compute) {_axis_line(r, 'B_compute_engine')} · "
                f"C (validation) {_axis_line(r, 'C_validation')} · "
                f"D (mechanism) {_axis_line(r, 'D_mechanism')} · "
                f"E (translation) {_axis_line(r, 'E_translation')}")
        p.append(f"**Axes:** {axes}\n")
        moat = r.get("moat_location") or {}
        subst = r.get("substance_check") or {}
        if moat.get("rationale"):
            p.append(f"**Moat ({_label(moat.get('data_vs_architecture'))}):** {moat['rationale']}\n")
        memo = r.get("memo")
        if memo:
            p.append(f"{memo}\n")
        if subst.get("disconfirming_evidence"):
            p.append(f"**Disconfirming:** {subst['disconfirming_evidence']}\n")
        if e.get("merged_ids"):
            p.append(f"*(merged duplicate company-ids: {', '.join(e['merged_ids'])})*\n")
        p.append("")

    if not ranked:
        p.append("*No scored companies yet — run Stage 4 (`--stage 4 --dispatch`) first.*\n")
    return "\n".join(p)


def write_shortlist(ranked: list[dict], path: str | Path, config: dict, *, run_id: str,
                    universe_n: Optional[int] = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_shortlist_md(ranked, config, run_id=run_id, universe_n=universe_n),
                    encoding="utf-8")
    return path


# ── orchestrator ──────────────────────────────────────────────────────────────

def run(store: Store, config: dict, *, run_id: Optional[str] = None,
        out_path: str | Path = "Outputs/shortlist.md", today: Optional[date] = None) -> dict:
    """Rank → dedup → refresh review queue → persist run_meta → export the shortlist.

    No API, no deletions. Returns a summary dict; writes the Markdown shortlist to ``out_path``.
    """
    rid = run_id or now_iso()
    ranked = rank(store, config, today=today)
    reviewed = _refresh_review_queue(store, ranked, config, rid)
    out = write_shortlist(ranked, out_path, config, run_id=rid,
                          universe_n=store.count_companies())

    duplicates = sum(len(e.get("merged_ids", [])) for e in ranked)
    top_n = int((config.get("stage5", {}) or {}).get("shortlist_top", 25))
    metrics = {
        "scored_companies": len(ranked) + duplicates,
        "shortlist_rows": len(ranked),
        "duplicates_collapsed": duplicates,
        "review_queue_added": reviewed,
        "top_rank_score": ranked[0]["rank_score"] if ranked else None,
        "shortlist_path": str(out),
    }
    store.record_run_meta(rid, started=rid, finished=now_iso(), metrics=metrics)
    store.audit(stage="stage5", action="scored", reason="rank_export_done", run_id=rid,
                detail=metrics)
    log.info("stage5: %s", metrics)
    return {"mode": "export", **metrics}
