"""Stage 1 — TA / mechanism tagging (deterministic, logged, REVERSIBLE) (spec §5.3).

Narrow on judgment-adjacent but still deterministic signals — WITHOUT deleting anything:
  * tag each company against the taxonomy (§4) over name + business description + sector/industry
    (and, once harvested in M4, MeSH/OpenAlex concepts). Matches → ``ta_tags``.
  * a company with NO mechanism tag is flagged ``no_ta_tag``, routed to ``review_queue``, and (if
    ``require_ta_tag``) marked ``stage1_excluded=true`` with a logged reason — **kept in the store**,
    only left out of the *default* Stage-2 harvest set. A config flag ``include_excluded`` re-admits
    it without re-harvesting (the reversibility guarantee — honored at Stage 2).

Recall posture: protected. Borderline → review_queue. Nothing deleted (the cardinal rule holds — this
stage never calls ``delete_company``). Re-running after evidence lands (M4) re-tags and can un-exclude
a company that now matches; tagging is idempotent.

Dev-stage tagging (platform / preclinical / phase 1–3) needs ClinicalTrials.gov + filing text, which
arrive with Stage-2 evidence (M4) — deferred to then; this stage does the mechanism TA filter.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from .store import Store
from .taxonomy import TaxonomyTagger

log = logging.getLogger(__name__)


def company_text(company, evidence: dict | None = None) -> str:
    """The text Stage 1 tags over: company fields + (when harvested) trial conditions. Trial
    conditions are diseases but occasionally carry mechanism cues. (OpenAlex concepts were dropped
    with the D9 OpenAlex dismissal.) ``evidence`` maps source -> payload dict."""
    parts = [company.name, company.business_description, company.sector, company.industry]
    if evidence:
        ct = evidence.get("ctgov") or {}
        parts.extend(ct.get("top_conditions") or [])
    return " ".join(p for p in parts if p)


def _evidence_for(store: Store, company_id: str) -> dict:
    out: dict = {}
    for src in ("ctgov",):
        ev = store.get_evidence(company_id, src)
        if ev and ev.get("payload"):
            out[src] = ev["payload"]
    return out


def run(store: Store, tagger: TaxonomyTagger, config: dict, *, run_id: str | None = None) -> dict:
    """Tag every company; route untagged ones to review (reversibly). Returns a summary dict."""
    s1 = config.get("stage1_filters", {}) or {}
    require = s1.get("require_ta_tag", True)

    tagged = no_tag = readmitted = 0
    branch_counts: dict[str, int] = {}

    for company in store.all_companies():
        hits = tagger.tag(company_text(company, _evidence_for(store, company.company_id)))
        ids = [h["id"] for h in hits]
        if ids:
            # tag, and clear any prior no-tag exclusion (reversibility — a now-matching company
            # rejoins the default harvest set)
            store.upsert_company(replace(company, ta_tags=ids, stage1_excluded=False),
                                 run_id=run_id)
            store.audit(stage="stage1", action="flagged", company_id=company.company_id,
                        reason="ta_tagged", run_id=run_id,
                        detail={"tags": ids, "terms": {h["id"]: h["terms"] for h in hits}})
            tagged += 1
            if company.stage1_excluded:
                readmitted += 1
            for h in hits:
                b = h.get("branch") or "?"
                branch_counts[b] = branch_counts.get(b, 0) + 1
        else:
            no_tag += 1
            store.add_to_review_queue(company.company_id, reason="no_ta_tag")
            if require:
                # KEEP + flag stage1_excluded (reversible); never delete
                store.exclude_stage1(company.company_id, reason="no_ta_tag", run_id=run_id)

    summary = {"tagged": tagged, "no_ta_tag": no_tag, "readmitted": readmitted,
               "require_ta_tag": require, "branch_hits": branch_counts}
    store.audit(stage="stage1", action="flagged", reason="tagging_done", run_id=run_id,
                detail=summary)
    log.info("stage1: %s", summary)
    return summary
