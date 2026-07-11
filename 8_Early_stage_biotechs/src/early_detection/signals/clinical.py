"""Clinical-trials signal (spec §3.2) — company clinical-stage programs via ClinicalTrials.gov v2.

For each active-universe entity, query CT.gov by the company name and keep studies where the company is
the **lead sponsor** (strong: the company runs the trial) or a **collaborator** (weaker: involved but
not driving). Emits one ``clinical_trial`` signal per matched NCT, tagged with phase / status / role.

Why this matters for the thesis: clinical stage is an INDEPENDENT convergence dimension. The literature
signal only fires when a founder resolves to an OpenAlex author (the pipeline's chokepoint); a company
can be clinical-stage with no such trail. So this reaches names citations miss — and a company that has
advanced its mechanism into the clinic *and* has specialist capital behind it is a genuinely cornered
name, exactly what the scoring pre-filter is meant to surface.

Precision rails (mirror the ownership signal): the company name (corporate suffix stripped) must match
the lead sponsor or a collaborator by containment, with a length guard so a short token can't false-
match. Idempotent (signal_id = hash(entity, nct)); commits per entity; fail-soft per entity.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..clients import _net, clinicaltrials
from ..config import Config
from ..models import Entity, SignalRecord
from ..store import Store, now_iso

log = logging.getLogger(__name__)

SIGNAL_TYPE = "clinical_trial"
SOURCE = "clinicaltrials_gov"

_LIMITER = _net.RateLimiter(per_sec=5.0)
_NONALNUM = re.compile(r"[^a-z0-9]+")
# Corporate-form / industry suffix tokens stripped before matching a company name to a sponsor name.
_SUFFIX = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "llc", "lp",
           "plc", "holdings", "holding", "group", "ag", "sa", "nv", "ab", "oyj", "asa", "spa", "as",
           "therapeutics", "pharmaceuticals", "pharma", "biosciences", "bioscience", "biopharma",
           "biotechnology", "sciences", "the"}
_MIN_CORE = 4          # a company "core" (post-suffix) shorter than this is too generic to match safely


@dataclass
class ClinicalResult:
    entities: int = 0
    with_trials: int = 0             # entities matched to ≥1 of their own trials
    signals: int = 0
    by_phase: dict = field(default_factory=dict)
    by_health: dict = field(default_factory=dict)   # active / completed / stalled / unknown
    lead: int = 0                    # signals where the company is the lead sponsor


def _norm(s: Optional[str]) -> str:
    return _NONALNUM.sub(" ", (s or "").lower()).strip()


def _core(name: Optional[str]) -> str:
    """Normalized company name with corporate/industry suffix tokens dropped — the distinctive stem
    (e.g. 'Acrivon Therapeutics, Inc.' → 'acrivon'). Empty if nothing distinctive remains."""
    toks = [t for t in _norm(name).split() if t not in _SUFFIX]
    return " ".join(toks)


def _sponsor_role(cores: list[str], study: dict) -> Optional[str]:
    """'lead' if the company matches the lead sponsor, else 'collaborator' if it matches a collaborator,
    else None. Containment either way, guarded by ``_MIN_CORE`` so a short stem can't false-match."""
    def _hit(target: Optional[str]) -> bool:
        nt = _norm(target)
        return any(len(cr) >= _MIN_CORE and (cr in nt or nt in cr) for cr in cores if cr)

    if _hit(study.get("lead_sponsor")):
        return "lead"
    if any(_hit(c) for c in study.get("collaborators") or []):
        return "collaborator"
    return None


def _signal_id(entity_id: str, nct: str) -> str:
    return "ct_" + hashlib.sha1(f"{entity_id}|{nct}".encode()).hexdigest()[:20]


def ingest_clinical(store: Store, cfg: Config, *, limit: int | None = None,
                    tickers: Optional[list[str]] = None,
                    search: Callable[[str], Optional[list[dict]]] | None = None) -> ClinicalResult:
    """Ingest clinical-trial signals for the active universe. ``search`` injectable for tests; ``tickers``
    pins named companies to the front of the work-list (guaranteed inside ``limit``)."""
    todo: list[Entity] = store.active_entities(limit=limit, tickers=tickers)
    search = search or (lambda name: clinicaltrials.search_studies(
        name, max_studies=cfg.clinical_max_studies, limiter=_LIMITER))
    res = ClinicalResult(entities=len(todo))
    log.info("clinical: scanning %d active entities (CT.gov v2)", len(todo))

    for e in todo:
        names = [n for n in (e.legal_name, e.common_name) if n]
        if not names:
            continue
        cores = [c for c in ({_core(n) for n in names}) if c]
        if not cores:
            continue
        try:
            studies = search(names[0])
        except Exception as exc:  # noqa: BLE001 — fail-soft per entity
            log.warning("clinical: search errored for %s: %s", e.legal_name, exc)
            continue
        if studies is None:      # fetch failed (throttled) — skip; a re-run retries (nothing stamped)
            log.warning("clinical: search unavailable for %s (left for retry)", e.legal_name)
            continue

        matched = 0
        seen: set[str] = set()
        for st in studies:
            nct = st.get("nct_id")
            if not nct or nct in seen:
                continue
            role = _sponsor_role(cores, st)
            if not role:
                continue
            seen.add(nct)
            matched += 1
            store.insert_signal(SignalRecord(
                signal_id=_signal_id(e.entity_id, nct), entity_id=e.entity_id,
                signal_type=SIGNAL_TYPE, source=SOURCE,
                raw_payload={"nct_id": nct, "title": st.get("title"), "phases": st.get("phases"),
                             "phase_rank": st.get("phase_rank"), "status": st.get("status"),
                             "role": role, "lead_sponsor": st.get("lead_sponsor"),
                             "start_date": st.get("start_date")},
                detected_at=now_iso(), event_date=st.get("start_date"), language="en"), commit=False)
            res.signals += 1
            if role == "lead":
                res.lead += 1
            ph = st.get("phase_rank") or 0
            res.by_phase[ph] = res.by_phase.get(ph, 0) + 1
            health = clinicaltrials.trial_health(st.get("status"))
            res.by_health[health] = res.by_health.get(health, 0) + 1
        if matched:
            store.conn.commit()
            res.with_trials += 1

    log.info("clinical done: entities=%d with_trials=%d signals=%d lead=%d by_phase=%s",
             res.entities, res.with_trials, res.signals, res.lead, res.by_phase)
    return res
