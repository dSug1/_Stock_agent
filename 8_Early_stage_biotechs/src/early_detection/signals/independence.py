"""§5.2 independence refinement — co-authorship-graph classification (zero-LLM).

M10's string heuristic calls a citation "independent" whenever it's from a different institution. But a
founder's former **co-authors / trainees / collaborators** who moved elsewhere are NOT independent
validators. OpenAlex gives us the co-authorship graph for free — a more reliable test than a Claude
guess (it's a graph fact, not a judgment). This pass re-classifies each citation of a founder's
foundational paper into:

    self · collaborator · same_institution · industry · independent

and computes a recency-decayed ``independence_score`` = Σ over genuinely-independent citations of
``0.5 ** (age_years / half_life)``. The refined ``independence`` overwrites the coarse M10 tag on the
stored citation signal, so ``evidence_summary``'s ``independent_citations`` (and therefore the scoring
pre-filter + packet) automatically tighten to the TRUE independent count.

Fail-soft per founder; persists per founder; idempotent (skips founders already refined).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from ..clients import _net, openalex
from ..config import Config
from ..models import SignalRecord
from ..store import Store, now_iso
from .literature import SIGNAL_TYPE, SOURCE, _norm, _sid

log = logging.getLogger(__name__)

_LIMITER = _net.RateLimiter(per_sec=8.0)
_HALF_LIFE_YEARS = 5.0


@dataclass
class IndependenceResult:
    founders: int = 0
    refined: int = 0
    citations: int = 0
    by_relationship: dict = field(default_factory=lambda: {
        "self": 0, "collaborator": 0, "same_institution": 0, "industry": 0, "independent": 0})


def classify(*, founder_author_id: str, coauthors: set, hints: list[str], citing: dict) -> str:
    """Relationship of one citing work to the founder. Precedence: self > collaborator >
    same_institution > industry > independent."""
    cauth = set(citing.get("author_ids") or [])
    if founder_author_id in cauth:
        return "self"
    if cauth & coauthors:
        return "collaborator"
    cinsts = [_norm(i) for i in citing.get("institutions") or []]
    for h in hints:
        nh = _norm(h)
        if nh and any(nh in ci or ci in nh for ci in cinsts):
            return "same_institution"
    if "company" in (citing.get("institution_types") or []):
        return "industry"
    return "independent"


def _decay(year: Optional[int], now_year: int) -> float:
    if not year:
        return 0.5
    return 0.5 ** (max(0, now_year - int(year)) / _HALF_LIFE_YEARS)


def refine_independence(store: Store, cfg: Config, *, limit: int | None = None,
                        today_year: Optional[int] = None,
                        coauthor_ids: Callable = openalex.coauthor_ids,
                        citing_works: Callable = openalex.citing_works) -> IndependenceResult:
    """Re-classify citations using the co-authorship graph and store an independence_score per founder.
    The two OpenAlex calls are injectable for offline tests. Persists per founder."""
    mailto = cfg.openalex_mailto or _env_email()
    year = today_year or date.today().year
    todo = store.founders_for_independence(limit=limit, only_missing=True)
    res = IndependenceResult(founders=len(todo))
    log.info("independence: %d resolved founders to refine", len(todo))

    for f in todo:
        aid = f["openalex_author_id"]
        found_id = f["foundational_work_id"]
        hints = [h for h in (f.get("institution"), f.get("company_name")) if h]
        try:
            coauthors = coauthor_ids(aid, mailto=mailto, limiter=_LIMITER) or set()
            citing = citing_works(found_id, mailto=mailto,
                                  from_year=year - cfg.literature_citation_years,
                                  max_results=cfg.literature_max_citing, limiter=_LIMITER) or []
        except Exception as exc:  # noqa: BLE001 — fail-soft per founder
            log.warning("independence: fetch failed for %s: %s", f["name"], exc)
            store.set_founder_independence(f["id"], 0.0)
            continue

        score = 0.0
        for cw in citing:
            rel = classify(founder_author_id=aid, coauthors=coauthors, hints=hints, citing=cw)
            res.by_relationship[rel] = res.by_relationship.get(rel, 0) + 1
            res.citations += 1
            if rel == "independent":
                score += _decay(cw.get("year"), year)
            # overwrite the coarse M10 citation signal with the refined relationship (same signal_id)
            sid = _sid(f["entity_id"], cw.get("id") or "", "citation", found_id)
            payload = {"kind": "citation", "founder": f["name"], "work_id": cw.get("id"),
                       "title": cw.get("title"), "year": cw.get("year"),
                       "cited_by_count": cw.get("cited_by_count"),
                       "foundational_work_id": found_id, "independence": rel, "relationship": rel,
                       "citing_institutions": cw.get("institutions")}
            store.insert_signal(SignalRecord(
                signal_id=sid, entity_id=f["entity_id"], signal_type=SIGNAL_TYPE, source=SOURCE,
                raw_payload=payload, detected_at=now_iso(), event_date=cw.get("date"), language="en"),
                commit=False)
        store.set_founder_independence(f["id"], round(score, 2))   # commits
        res.refined += 1

    log.info("independence done: refined=%d citations=%d by=%s",
             res.refined, res.citations, res.by_relationship)
    return res


def _env_email() -> str:
    import os
    import re
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", os.getenv("USER_AGENT", ""))
    return m.group(0) if m else ""
