"""Seed-eval validation harness (spec §13) — the trust gate for the whole screen.

Score the labeled seed set (``config/seed_labels.csv``: known positives, negatives, and borderline
AI-discovery names) through the funnel and report **precision / recall** + **per-stage survival**. The
cardinal worry is a known positive lost *invisibly* — so for every seed we report exactly where it
stands (deleted at Stage 0 with reason / kept / tagged / has evidence / scored) and we flag loudly if
a positive was DELETED at the hard cut (a spec-level failure to fix before trusting the run).

This is **read-only and free**: it evaluates whatever scores already exist in the store (no API). A
positive that is in-band and retained but simply not scored yet is reported as ``unscored`` (not a
failure) — re-run Stage 4 on its tier to score it. Precision/recall are computed over the
positive/negative seeds that HAVE a composite; borderline seeds are reported separately (they are
deliberately ambiguous and never counted as pos/neg). Metrics persist to ``run_meta.metrics_json``.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import tiering
from .store import Store, now_iso, today_iso

log = logging.getLogger(__name__)

VALID_LABELS = ("positive", "negative", "borderline")


@dataclass(frozen=True)
class SeedLabel:
    ticker: str
    label: str
    name: str = ""
    note: str = ""


def load_seed_labels(path: str | Path) -> list[SeedLabel]:
    """Parse the seed-labels CSV. Rows with an unknown label or no ticker are skipped (logged)."""
    out: list[SeedLabel] = []
    p = Path(path)
    if not p.exists():
        log.warning("seed labels not found: %s", p)
        return out
    with open(p, "r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            tkr = (row.get("ticker") or "").strip().upper()
            label = (row.get("label") or "").strip().lower()
            if not tkr or label not in VALID_LABELS:
                if tkr or label:
                    log.warning("skipping seed row (ticker=%r label=%r)", tkr, label)
                continue
            out.append(SeedLabel(ticker=tkr, label=label,
                                 name=(row.get("name") or "").strip(),
                                 note=(row.get("note") or "").strip()))
    return out


# ── per-seed status resolution ────────────────────────────────────────────────

def _deletion_for_ticker(store: Store, ticker: str) -> Optional[dict]:
    """Find the most recent 'deleted' audit row whose detail.ticker matches (the company is gone from
    the companies table, so its fate lives only in the audit log). Returns {reason, stage, detail}."""
    rows = store.conn.execute(
        "SELECT stage, reason, detail_json FROM audit_log WHERE action='deleted' ORDER BY ts DESC"
    ).fetchall()
    for r in rows:
        detail = json.loads(r["detail_json"]) if r["detail_json"] else {}
        if (detail.get("ticker") or "").strip().upper() == ticker:
            return {"reason": r["reason"], "stage": r["stage"], "detail": detail}
    return None


def _disposition(deletion: dict, config: dict) -> str:
    """Classify a Stage-0 deletion so an ACCEPTED outcome isn't flagged as a failure.

    A positive deleted for outgrowing the **ceiling** is ``graduated`` (expected — the band targets the
    early "small & young" window by design, D7); deleted below the **floor** is ``below_floor`` and
    ``not_live`` is ``not_live`` — both genuine losses. ``unknown`` when the cap/band aren't recorded
    (conservative: treated as a genuine loss upstream).
    """
    reason = deletion.get("reason")
    if reason == "not_live":
        return "not_live"
    if reason != "mktcap_out_of_band":
        return "unknown"
    detail = deletion.get("detail") or {}
    cap = detail.get("mktcap_usd_fd")
    band = detail.get("band")
    if band is None:
        mc = config.get("market_cap", {}) or {}
        band = [mc.get("min_usd"), mc.get("max_usd")]
    lo, hi = (band or [None, None])[:2]
    if cap is None or hi is None or lo is None:
        return "unknown"
    if cap > hi:
        return "graduated"      # above the ceiling — accepted (out-of-scope graduate)
    if cap < lo:
        return "below_floor"    # genuine loss — too small to have been cut
    return "unknown"


def _has_evidence(store: Store, company_id: str) -> bool:
    return store.conn.execute(
        "SELECT 1 FROM evidence WHERE company_id=? LIMIT 1", (company_id,)).fetchone() is not None


def _seed_status(store: Store, seed: SeedLabel, config: dict, scores: dict,
                 by_ticker: dict) -> dict:
    """Resolve one seed's position in the funnel."""
    c = by_ticker.get(seed.ticker)
    base = {"ticker": seed.ticker, "label": seed.label, "name": seed.name or (c.name if c else "")}
    if c is None:
        deletion = _deletion_for_ticker(store, seed.ticker)
        if deletion:
            detail = deletion.get("detail") or {}
            return {**base, "status": "deleted", "stage": deletion["stage"],
                    "reason": deletion["reason"], "disposition": _disposition(deletion, config),
                    "deleted_cap": detail.get("mktcap_usd_fd"),
                    "in_store": False, "scored": False, "composite": None, "tier": None}
        return {**base, "status": "absent", "stage": None, "reason": "never_entered_universe",
                "disposition": "absent", "in_store": False, "scored": False,
                "composite": None, "tier": None}
    sc = scores.get(c.company_id)
    composite = sc.composite if sc else None
    return {
        **base, "in_store": True, "company_id": c.company_id,
        "tier": tiering.compute_tier(c, config),
        "tagged": bool(c.ta_tags), "stage1_excluded": bool(c.stage1_excluded),
        "has_evidence": _has_evidence(store, c.company_id),
        "scored": sc is not None, "composite": composite,
        "confidence": (sc.confidence if sc else None),
        "status": "scored" if sc else "unscored",
    }


# ── evaluation ────────────────────────────────────────────────────────────────

def evaluate(store: Store, config: dict, labels: Optional[list[SeedLabel]] = None, *,
             threshold: Optional[float] = None) -> dict:
    """Resolve every seed's funnel position, then compute precision/recall over scored pos/neg seeds
    + per-stage survival. Returns {metrics, per_seed, survival}."""
    se = config.get("seed_eval", {}) or {}
    if labels is None:
        labels = load_seed_labels(se.get("labels_path", "config/seed_labels.csv"))
    if threshold is None:
        threshold = float(se.get("decision_threshold", 0.6))

    scores = store.latest_scores()
    by_ticker = {}
    for c in store.all_companies():
        if c.primary_ticker:
            by_ticker.setdefault(c.primary_ticker.strip().upper(), c)

    per_seed = [_seed_status(store, s, config, scores, by_ticker) for s in labels]

    def grp(lbl):
        return [x for x in per_seed if x["label"] == lbl]
    pos, neg, bord = grp("positive"), grp("negative"), grp("borderline")

    def predicted_positive(x):
        return x["composite"] is not None and x["composite"] >= threshold

    # confusion matrix over SCORED positive/negative seeds
    tp = sum(1 for x in pos if predicted_positive(x))
    fn = sum(1 for x in pos if x["scored"] and not predicted_positive(x))
    fp = sum(1 for x in neg if predicted_positive(x))
    tn = sum(1 for x in neg if x["scored"] and not predicted_positive(x))
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)
          if precision and recall else None)

    # positives that didn't reach scoring. Split ACCEPTED graduates (re-rated above the ceiling — D7)
    # from GENUINE losses (deleted below floor / not_live / absent / unknown). Only genuine DELETIONS
    # of a positive trip the spec-failure flag (§13's load-bearing check).
    graduated_positives = [x for x in pos if x.get("disposition") == "graduated"]
    deleted_positives = [x for x in pos if x["status"] == "deleted"]
    genuine_deletion_losses = [x for x in deleted_positives
                               if x.get("disposition") != "graduated"]
    lost_positives = [x for x in pos if x["status"] in ("deleted", "absent")
                      and x.get("disposition") != "graduated"]
    stage0_deleted_positives = genuine_deletion_losses

    def survival(group):
        return {
            "n": len(group),
            "in_store": sum(1 for x in group if x.get("in_store")),
            "deleted": sum(1 for x in group if x["status"] == "deleted"),
            "absent": sum(1 for x in group if x["status"] == "absent"),
            "tagged": sum(1 for x in group if x.get("tagged")),
            "with_evidence": sum(1 for x in group if x.get("has_evidence")),
            "scored": sum(1 for x in group if x.get("scored")),
            "predicted_positive": sum(1 for x in group if predicted_positive(x)),
        }

    metrics = {
        "threshold": threshold,
        "n_positive": len(pos), "n_negative": len(neg), "n_borderline": len(bord),
        "n_positive_scored": sum(1 for x in pos if x["scored"]),
        "n_negative_scored": sum(1 for x in neg if x["scored"]),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
        "positives_lost_pre_scoring": len(lost_positives),
        "stage0_deleted_positives": [x["ticker"] for x in stage0_deleted_positives],
        "graduated_positives": [x["ticker"] for x in graduated_positives],
        "spec_failure": bool(genuine_deletion_losses),   # genuine loss of a positive at the hard cut
    }
    survival_by_group = {"positive": survival(pos), "negative": survival(neg),
                         "borderline": survival(bord)}
    return {"metrics": metrics, "per_seed": per_seed, "survival": survival_by_group}


# ── report + orchestrator ─────────────────────────────────────────────────────

def _fmt(v, nd=3):
    return "—" if v is None else f"{v:.{nd}f}"


def build_report_md(result: dict, *, run_id: str) -> str:
    m = result["metrics"]
    p: list[str] = []
    p.append("# Acrivon-Pattern Screener — Seed Validation (§13)\n")
    p.append(f"*Run `{run_id}` · generated {today_iso()} · decision threshold "
             f"composite ≥ {m['threshold']}.*\n")
    if m["spec_failure"]:
        p.append(f"> ⛔ **SPEC-LEVEL FAILURE:** known positive(s) genuinely lost at the hard cut "
                 f"(below floor / not-live): **{', '.join(m['stage0_deleted_positives'])}**. "
                 f"Fix before trusting the run.\n")
    if m.get("graduated_positives"):
        p.append(f"> ℹ️ **Graduated (accepted, D7):** positive(s) re-rated ABOVE the ceiling and cut "
                 f"by design: **{', '.join(m['graduated_positives'])}** — out-of-scope graduates, "
                 f"not a failure.\n")
    p.append("## Headline\n")
    p.append(f"- **Precision** {_fmt(m['precision'])} · **Recall** {_fmt(m['recall'])} · "
             f"**F1** {_fmt(m['f1'])}  *(over scored positive/negative seeds, "
             f"TP={m['tp']} FP={m['fp']} FN={m['fn']} TN={m['tn']})*")
    p.append(f"- Positives: {m['n_positive']} ({m['n_positive_scored']} scored) · "
             f"Negatives: {m['n_negative']} ({m['n_negative_scored']} scored) · "
             f"Borderline: {m['n_borderline']}")
    p.append(f"- Positives lost before scoring: {m['positives_lost_pre_scoring']}\n")

    p.append("## Per-stage survival\n")
    p.append("| Group | n | in store | deleted | tagged | w/ evidence | scored | pred. positive |")
    p.append("|:--|--:|--:|--:|--:|--:|--:|--:|")
    for g in ("positive", "negative", "borderline"):
        s = result["survival"][g]
        p.append(f"| {g} | {s['n']} | {s['in_store']} | {s['deleted']} | {s['tagged']} | "
                 f"{s['with_evidence']} | {s['scored']} | {s['predicted_positive']} |")
    p.append("")

    p.append("## Per-seed detail\n")
    p.append("| Ticker | Label | Status | Tier | Composite | Stage/Reason | Name |")
    p.append("|:--|:--|:--|--:|--:|:--|:--|")
    order = {"positive": 0, "borderline": 1, "negative": 2}
    for x in sorted(result["per_seed"], key=lambda r: (order.get(r["label"], 9), r["ticker"])):
        sr = x.get("reason") or ""
        if x.get("stage"):
            sr = f"{x['stage']} · {sr}"
        disp = x.get("disposition")
        if disp in ("graduated", "below_floor") and x.get("deleted_cap"):
            sr += f" ({disp} ${x['deleted_cap']/1e6:,.0f}M)"
        tier = x.get("tier")
        p.append(f"| **{x['ticker']}** | {x['label']} | {x['status']} | "
                 f"{tier if tier is not None else '—'} | {_fmt(x.get('composite'))} | {sr} | "
                 f"{x.get('name','')} |")
    p.append("")
    return "\n".join(p)


def run(store: Store, config: dict, *, run_id: Optional[str] = None,
        out_path: str | Path = "Outputs/seed_eval.md", threshold: Optional[float] = None,
        labels: Optional[list[SeedLabel]] = None, persist_labels: bool = False) -> dict:
    """Evaluate the seed set, write the report, persist metrics to run_meta. Returns the metrics dict.

    ``labels`` defaults to the config CSV. ``persist_labels`` also writes positive/negative labels into
    the store's ``seed_labels`` table (borderline is CSV-only — the table accepts only pos|neg)."""
    rid = run_id or now_iso()
    if labels is None:
        labels = load_seed_labels((config.get("seed_eval", {}) or {}).get(
            "labels_path", "config/seed_labels.csv"))
    result = evaluate(store, config, labels, threshold=threshold)
    if persist_labels:
        for s in labels:
            if s.label in ("positive", "negative"):
                c = next((x for x in store.all_companies()
                          if (x.primary_ticker or "").upper() == s.ticker), None)
                if c:
                    store.set_seed_label(c.company_id, s.label)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report_md(result, run_id=rid), encoding="utf-8")

    store.record_run_meta(rid, started=rid, finished=now_iso(), metrics=result["metrics"])
    store.audit(stage="seed_eval", action="scored", reason="seed_eval_done", run_id=rid,
                detail={**result["metrics"], "report_path": str(out)})
    log.info("seed_eval: %s", result["metrics"])
    return {"mode": "seed_eval", **result["metrics"], "report_path": str(out)}
