"""§9 validation harness — back-test the conviction output against a labeled set of known cases.

The §9 discipline (overall spec): the independence/novelty/scoring calls are *unverified against a
labeled dataset*; before trusting a flag at face value, back-test against known cases (Satellos=MSLE
included) where the right answer is already known, to get precision/recall. This module is that
harness. It is **zero-spend**: it reads only what the pipeline has already scored — no Claude calls,
no network — and joins a hand-labeled ground-truth set (``validation/known_cases.yaml``) against the
store.

It answers three questions, kept deliberately separate:

  * **FUNNEL / coverage** — of the labeled cases, how many entered the universe, cleared the §5.5
    pre-filter, and got a conviction score. This makes the §9 *survivorship* bias measurable: if
    positives are lost upstream of the model (never in the universe, or filtered out), end-to-end
    recall is capped there no matter how good the scoring call is.
  * **CLASSIFICATION** — over the *scored subset only*, precision / recall / F1 / confusion of the
    conviction call vs the gold label (deep-dive-candidate ⇒ predicted-positive by default, or a
    ``conviction_score`` threshold).
  * **THRESHOLD SWEEP** — precision/recall across ``conviction_score`` cutoffs, since D3 ranks the
    digest on the score, not just the flag.

Two recalls are reported and MUST NOT be conflated:
  - ``funnel_recall`` = scored positives / all positives  (end-to-end; *includes* survivorship loss)
  - ``model_recall``  = TP / (TP+FN) over the scored subset (the call's discrimination, given it saw
    the case)

A report also carries a ``sufficient`` flag: with only a handful of scored cases the metrics are not
yet trustworthy — the harness says so loudly rather than over-claiming a precision computed on n=2.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .config import Config
from .models import Entity
from .store import Store

log = logging.getLogger(__name__)

# Minimum scored cases per class before classification metrics are considered trustworthy (§9 honesty).
MIN_SUFFICIENT_POS = 3
MIN_SUFFICIENT_NEG = 2

# Corporate-form suffix tokens dropped in name matching. Deliberately EXCLUDES industry words
# ("therapeutics", "bio", "pharma") — dropping those would collapse distinct companies and cost
# precision. Aliases in the labeled set carry the rest.
_NAME_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "lp",
    "plc", "holdings", "holding", "group", "ag", "sa", "nv", "ab", "oyj", "asa", "spa", "as",
    "kk", "gmbh", "the",
}
_PUNCT = re.compile(r"[^\w\s]")

# Funnel stages, shallow→deep. The ordinal doubles as "how far the case travelled".
FUNNEL_STAGES = ("not_in_universe", "inactive", "below_floor", "above_ceiling",
                 "in_universe", "prefilter_cleared", "scored")
_STAGE_ORDINAL = {s: i for i, s in enumerate(FUNNEL_STAGES)}

_DEEP_DIVE = "deep-dive-candidate"


def _norm_name(s: str | None) -> str:
    if not s:
        return ""
    s = _PUNCT.sub(" ", s.lower())
    toks = [t for t in s.split() if t and t not in _NAME_SUFFIXES]
    return " ".join(toks)


# ── labeled ground-truth set ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class Case:
    """One hand-labeled known case. ``label`` is the gold class; ``expected_flag`` is what a
    well-calibrated pipeline *should* emit (context/QA, not used in the binary math)."""

    name: str
    label: str                                  # "positive" | "negative"
    ticker: Optional[str] = None
    aliases: tuple[str, ...] = ()
    cik: Optional[str] = None
    jurisdiction: Optional[str] = None
    rerated: Optional[bool] = None              # observed outcome (did the mechanism re-rate)
    expected_flag: Optional[str] = None
    mechanism: str = ""
    verified: bool = False                      # has the operator fact-checked this label?
    source: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_positive(self) -> bool:
        return self.label.strip().lower() == "positive"


def load_cases(path: Path) -> list[Case]:
    """Parse ``known_cases.yaml`` (``yaml.safe_load`` only — repo security discipline). Accepts either
    a top-level list or a mapping with a ``cases:`` key. Raises on a malformed label."""
    with Path(path).open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or []
    if isinstance(data, dict):
        data = data.get("cases") or []
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of cases (or a 'cases:' mapping)")
    cases: list[Case] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"{path}: case #{i} is not a mapping")
        label = str(row.get("label", "")).strip().lower()
        if label not in ("positive", "negative"):
            raise ValueError(f"{path}: case #{i} ({row.get('name')!r}) has invalid label {label!r}")
        aliases = row.get("aliases") or []
        cases.append(Case(
            name=str(row.get("name", "")).strip(),
            label=label,
            ticker=(str(row["ticker"]).strip() if row.get("ticker") else None),
            aliases=tuple(str(a).strip() for a in aliases if str(a).strip()),
            cik=(str(row["cik"]).strip() if row.get("cik") else None),
            jurisdiction=(str(row["jurisdiction"]).strip() if row.get("jurisdiction") else None),
            rerated=row.get("rerated"),
            expected_flag=(str(row["expected_flag"]).strip() if row.get("expected_flag") else None),
            mechanism=str(row.get("mechanism", "")).strip(),
            verified=bool(row.get("verified", False)),
            source=str(row.get("source", "")).strip(),
            raw=row,
        ))
    return cases


# ── matching a labeled case to a stored entity ───────────────────────────────────

def match_case(store: Store, case: Case) -> tuple[Optional[Entity], Optional[str]]:
    """Resolve a labeled case to a stored entity, highest-precision key first: CIK → ticker (primary
    or alias) → normalized legal/common name. Returns (entity, matched_by) or (None, None)."""
    if case.cik:
        e = store.find_entity_by_key(cik=case.cik)
        if e:
            return e, "cik"
    for tk in (case.ticker, *case.aliases):
        if not tk:
            continue
        # try the raw ticker and an exchange-suffix-stripped form ("MSLE.V" → "MSLE")
        for variant in {tk, tk.split(".")[0]}:
            hits = store.entities_by_ticker(variant)
            if hits:
                return hits[0], "ticker"
    targets = {_norm_name(x) for x in (case.name, *case.aliases)}
    targets.discard("")
    if targets:
        for e in store.all_entities(live_only=False):
            if _norm_name(e.legal_name) in targets or _norm_name(e.common_name) in targets:
                return e, "name"
    return None, None


def _funnel_stage(entity: Optional[Entity], scored: bool, cleared: bool) -> str:
    if entity is None:
        return "not_in_universe"
    if not entity.is_live:
        return "inactive"
    if entity.below_floor:
        return "below_floor"
    if entity.above_ceiling:
        return "above_ceiling"
    if scored:
        return "scored"
    if cleared:
        return "prefilter_cleared"
    return "in_universe"


@dataclass
class CaseEval:
    case: Case
    entity: Optional[Entity]
    matched_by: Optional[str]
    stage: str
    predicted_flag: Optional[str] = None
    predicted_score: Optional[float] = None

    @property
    def scored(self) -> bool:
        return self.predicted_flag is not None


# ── metrics ──────────────────────────────────────────────────────────────────────

def _confusion(pairs: list[tuple[bool, bool]]) -> tuple[int, int, int, int]:
    """(gold, predicted) → (tp, fp, fn, tn)."""
    tp = sum(1 for g, p in pairs if g and p)
    fp = sum(1 for g, p in pairs if not g and p)
    fn = sum(1 for g, p in pairs if g and not p)
    tn = sum(1 for g, p in pairs if not g and not p)
    return tp, fp, fn, tn


def _metrics(tp: int, fp: int, fn: int, tn: int) -> dict[str, Any]:
    """Precision/recall/F1/accuracy — None (not 0) where undefined, so an empty cell never reads as a
    real zero. Never divides by zero."""
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
    total = tp + fp + fn + tn
    acc = (tp + tn) / total if total else None
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": total,
            "precision": prec, "recall": rec, "f1": f1, "accuracy": acc}


def _predict_positive(ev: CaseEval, *, rule: str, threshold: float) -> bool:
    if rule == "score":
        return (ev.predicted_score or 0) >= threshold
    return ev.predicted_flag == _DEEP_DIVE


@dataclass
class Report:
    prompt_version: str
    rule: str
    threshold: float
    evals: list[CaseEval]

    # populated by _compute
    matched: int = 0
    unmatched: int = 0
    funnel: dict[str, dict[str, int]] = field(default_factory=dict)   # stage → {positive,negative,total}
    scored_positives: int = 0
    scored_negatives: int = 0
    total_positives: int = 0
    total_negatives: int = 0
    classification: dict[str, Any] = field(default_factory=dict)      # metrics over the SCORED subset
    funnel_recall: Optional[float] = None                             # scored positives / all positives
    sweep: list[dict[str, Any]] = field(default_factory=list)
    sufficient: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_version": self.prompt_version, "rule": self.rule, "threshold": self.threshold,
            "cases": len(self.evals), "matched": self.matched, "unmatched": self.unmatched,
            "totals": {"positive": self.total_positives, "negative": self.total_negatives},
            "scored": {"positive": self.scored_positives, "negative": self.scored_negatives},
            "funnel": self.funnel, "funnel_recall": self.funnel_recall,
            "classification": self.classification, "sweep": self.sweep,
            "sufficient": self.sufficient,
            "case_detail": [
                {"name": e.case.name, "label": e.case.label, "verified": e.case.verified,
                 "matched_by": e.matched_by, "entity_id": e.entity.entity_id if e.entity else None,
                 "stage": e.stage, "predicted_flag": e.predicted_flag,
                 "predicted_score": e.predicted_score, "expected_flag": e.case.expected_flag}
                for e in self.evals],
        }


def evaluate(store: Store, cfg: Config, cases: list[Case], *, rule: str = "flag",
             threshold: float = 70.0, sweep_step: int = 10) -> Report:
    """Join labeled cases against the store and compute funnel + classification + sweep metrics.

    ``rule="flag"`` → deep-dive-candidate counts as a positive prediction; ``rule="score"`` →
    ``conviction_score >= threshold``. Zero-spend and read-only."""
    pv = cfg.scoring_prompt_version
    # All rule-passers (force=True ⇒ includes already-scored) — computed once, not per case.
    cleared_ids = {e.entity_id for e in store.scoring_candidates(
        pv, min_independent=cfg.prefilter_min_independent, force=True)}

    evals: list[CaseEval] = []
    for case in cases:
        entity, matched_by = match_case(store, case)
        score_row = store.get_score(entity.entity_id, pv) if entity else None
        stage = _funnel_stage(entity, scored=bool(score_row),
                              cleared=bool(entity and entity.entity_id in cleared_ids))
        ev = CaseEval(case=case, entity=entity, matched_by=matched_by, stage=stage)
        if score_row:
            ev.predicted_flag = score_row.get("conviction_flag")
            ev.predicted_score = score_row.get("conviction_score")
        evals.append(ev)

    rep = Report(prompt_version=pv, rule=rule, threshold=threshold, evals=evals)
    rep.matched = sum(1 for e in evals if e.entity is not None)
    rep.unmatched = len(evals) - rep.matched
    rep.total_positives = sum(1 for e in evals if e.case.is_positive)
    rep.total_negatives = len(evals) - rep.total_positives

    # funnel breakdown by stage × label
    for stage in FUNNEL_STAGES:
        rep.funnel[stage] = {"positive": 0, "negative": 0, "total": 0}
    for e in evals:
        cell = rep.funnel[e.stage]
        cell["positive" if e.case.is_positive else "negative"] += 1
        cell["total"] += 1

    scored = [e for e in evals if e.scored]
    rep.scored_positives = sum(1 for e in scored if e.case.is_positive)
    rep.scored_negatives = len(scored) - rep.scored_positives
    rep.funnel_recall = (rep.scored_positives / rep.total_positives) if rep.total_positives else None

    # classification over the SCORED subset only (the model's discrimination given it saw the case)
    pairs = [(e.case.is_positive, _predict_positive(e, rule=rule, threshold=threshold)) for e in scored]
    rep.classification = _metrics(*_confusion(pairs))

    # precision/recall sweep over conviction_score cutoffs (D3 ranks on the score)
    for t in range(0, 101, max(1, sweep_step)):
        sp = [(e.case.is_positive, (e.predicted_score or 0) >= t) for e in scored]
        m = _metrics(*_confusion(sp))
        rep.sweep.append({"threshold": t, "precision": m["precision"], "recall": m["recall"],
                          "f1": m["f1"], "tp": m["tp"], "fp": m["fp"], "fn": m["fn"], "tn": m["tn"]})

    rep.sufficient = (rep.scored_positives >= MIN_SUFFICIENT_POS
                      and rep.scored_negatives >= MIN_SUFFICIENT_NEG)
    return rep


# ── report rendering ─────────────────────────────────────────────────────────────

def _pct(x: Optional[float]) -> str:
    return "—" if x is None else f"{x * 100:.0f}%"


def write_report(rep: Report, out_path) -> None:
    """Human-readable Markdown report → a .md file (raw Markdown; if an HTML render is ever added it
    must html.escape at that boundary, per repo discipline)."""
    L: list[str] = []
    L.append("# §9 validation — conviction back-test vs known cases")
    L.append(f"\n*prompt {rep.prompt_version} · decision rule: {rep.rule}"
             f"{f' (score ≥ {rep.threshold:.0f})' if rep.rule == 'score' else ' (deep-dive-candidate ⇒ positive)'} · "
             f"{len(rep.evals)} labeled cases*\n")

    if not rep.sufficient:
        L.append("> ⚠️ **INSUFFICIENT DATA — metrics below are indicative, not trustworthy.** "
                 f"Only {rep.scored_positives} positive / {rep.scored_negatives} negative labeled cases "
                 f"have been scored (need ≥{MIN_SUFFICIENT_POS}/{MIN_SUFFICIENT_NEG}). Score more of the "
                 "labeled universe before relying on precision/recall. See the *unscored positives* "
                 "list below for the concrete work-list.\n")

    # funnel — the survivorship view
    L.append("## Funnel (survivorship view)")
    L.append("\nHow far each labeled case travelled. Losses in early stages cap end-to-end recall "
             "*upstream of the scoring call* — no model quality can recover them.\n")
    L.append("| Stage | Positives | Negatives | Total |")
    L.append("|---|--:|--:|--:|")
    for stage in FUNNEL_STAGES:
        c = rep.funnel[stage]
        if c["total"]:
            L.append(f"| {stage} | {c['positive']} | {c['negative']} | {c['total']} |")
    L.append(f"\n**Matched to a stored entity:** {rep.matched}/{len(rep.evals)}  ·  "
             f"**End-to-end funnel recall** (scored positives / all positives): "
             f"{_pct(rep.funnel_recall)} ({rep.scored_positives}/{rep.total_positives})\n")

    # classification
    m = rep.classification
    L.append("## Classification (scored subset only)")
    L.append("\nThe scoring call's discrimination, computed *only* over cases that reached it.\n")
    L.append(f"- Precision: **{_pct(m.get('precision'))}**   Recall: **{_pct(m.get('recall'))}**   "
             f"F1: **{_pct(m.get('f1'))}**   Accuracy: **{_pct(m.get('accuracy'))}**")
    L.append(f"- Confusion: TP={m.get('tp', 0)}  FP={m.get('fp', 0)}  "
             f"FN={m.get('fn', 0)}  TN={m.get('tn', 0)}  (n={m.get('n', 0)})\n")

    # sweep
    if any(s["tp"] or s["fp"] or s["fn"] for s in rep.sweep):
        L.append("## Conviction-score threshold sweep")
        L.append("\n| score ≥ | precision | recall | F1 | TP | FP | FN |")
        L.append("|--:|--:|--:|--:|--:|--:|--:|")
        for s in rep.sweep:
            L.append(f"| {s['threshold']} | {_pct(s['precision'])} | {_pct(s['recall'])} | "
                     f"{_pct(s['f1'])} | {s['tp']} | {s['fp']} | {s['fn']} |")
        L.append("")

    # actionable work-lists
    unscored_pos = [e for e in rep.evals if e.case.is_positive and not e.scored]
    if unscored_pos:
        L.append("## Unscored positives (the work-list)")
        L.append("\nKnown winners not yet scored — the concrete gap between the pipeline and a "
                 "trustworthy back-test. `not_in_universe` = survivorship (universe expansion needed); "
                 "`in_universe`/`prefilter_cleared` = just run the extract→literature→score sweep.\n")
        for e in unscored_pos:
            where = f"entity {e.entity.entity_id}" if e.entity else "no store match"
            L.append(f"- **{e.case.name}** ({e.case.ticker or '?'}) — {e.stage} · {where}")
        L.append("")

    # per-case detail
    L.append("## Per-case detail")
    L.append("\n| Case | Label | Matched by | Stage | Predicted | Score | Expected |")
    L.append("|---|---|---|---|---|--:|---|")
    for e in sorted(rep.evals, key=lambda x: (-_STAGE_ORDINAL[x.stage], x.case.name)):
        vflag = "" if e.case.verified else " ⚠︎"
        L.append(f"| {e.case.name}{vflag} | {e.case.label} | {e.matched_by or '—'} | {e.stage} | "
                 f"{e.predicted_flag or '—'} | {e.predicted_score if e.predicted_score is not None else '—'} | "
                 f"{e.case.expected_flag or '—'} |")
    L.append("\n*⚠︎ = label not yet operator-verified; treat with caution.*")

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(L), encoding="utf-8")
