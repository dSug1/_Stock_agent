"""Stage 2 — evidence harvesting (spec §5.4).

For each Stage-1-retained company, harvest a structured evidence bundle from the free science/clinical/
IP sources and persist it to the ``evidence`` table (one row per company+source, with a cursor for
incremental re-runs). Raw-ish normalized summaries are stored once; downstream stages (embed/score)
recompute against them without re-harvesting (reversibility §1.4).

Sources wired here (the free, high-signal core): **ClinicalTrials.gov** (trials/phases/biomarker-Dx
language) and **PatentsView** (method/platform patents — key-gated, inert without a key). OpenAlex
(publications + author pedigree) was DISMISSED (decisions.md D9): its institution registry covers <5%
of small-cap biotech and it rate-limits on a depleting $-budget — publications + scientific pedigree
are now researched by the Stage-4 Claude call via the web_search server tool instead. EDGAR full-text
and IR-poster scraping remain deferred spec sources.

Incremental: a company+source harvested within ``incremental_ttl_days`` is skipped (polite + cheap).
Companies marked ``stage1_excluded`` are skipped unless ``stage1_filters.include_excluded`` — the
reversibility guarantee, honored here without re-harvesting anything already stored.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .clients import _net, clinicaltrials, patentsview
from .models import Evidence
from .store import Store, now_iso

log = logging.getLogger(__name__)

Harvester = Callable[[object], Optional[tuple[dict, str]]]


def _payload_hash(summary: dict) -> str:
    return hashlib.sha1(json.dumps(summary, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _is_fresh(fetched_at: Optional[str], ttl_days: int) -> bool:
    if not fetched_at or ttl_days <= 0:
        return False
    try:
        ts = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts < timedelta(days=ttl_days)


def build_harvesters(config: dict) -> dict[str, Harvester]:
    """Map source name -> harvester(company)->(summary, cursor)|None, per config + env keys."""
    s2 = config.get("stage2", {}) or {}
    enabled = s2.get("sources", ["ctgov", "patents"])
    ct_lim = _net.RateLimiter(float(s2.get("ctgov_rate_per_sec", 4)))
    pv_lim = _net.RateLimiter(float(s2.get("patents_rate_per_sec", 4)))
    pv_key = os.getenv("PATENTSVIEW_API_KEY", "").strip() or None

    harvesters: dict[str, Harvester] = {}
    if "ctgov" in enabled:
        harvesters["ctgov"] = lambda c: clinicaltrials.fetch(c.name, limiter=ct_lim)
    if "patents" in enabled:
        harvesters["patents"] = lambda c: patentsview.fetch(c.name, limiter=pv_lim, api_key=pv_key)
    return harvesters


def run(store: Store, config: dict, *, run_id: str | None = None, incremental: bool = True,
        limit: Optional[int] = None, include_excluded: Optional[bool] = None,
        tickers: Optional[list[str]] = None) -> dict:
    """Harvest evidence for Stage-1-retained companies. Returns a summary dict.

    ``include_excluded`` overrides config when not None — pass True to harvest the *full* retained set
    (incl. ``stage1_excluded`` companies), which is the recall-safe move when Stage 1 tagged on
    description alone: harvest evidence for everyone, then re-run Stage 1 to re-tag from that evidence.
    """
    s2 = config.get("stage2", {}) or {}
    ttl_days = int(s2.get("incremental_ttl_days", 7))
    if include_excluded is None:
        include_excluded = (config.get("stage1_filters", {}) or {}).get("include_excluded", False)
    harvesters = build_harvesters(config)

    companies = [c for c in store.all_companies() if include_excluded or not c.stage1_excluded]
    if tickers:
        tset = {t.upper() for t in tickers}
        companies = [c for c in companies if (c.primary_ticker or "").upper() in tset]
    if limit:
        companies = companies[:limit]

    harvested = skipped = empty = 0
    per_source: dict[str, int] = {}
    for company in companies:
        for source, harvest in harvesters.items():
            existing = store.get_evidence(company.company_id, source)
            if incremental and existing and _is_fresh(existing.get("fetched_at"), ttl_days):
                skipped += 1
                continue
            try:
                result = harvest(company)
            except Exception as exc:                      # fail-open per source
                log.debug("harvest %s failed for %s: %s", source, company.primary_ticker, exc)
                result = None
            if not result:
                empty += 1
                continue
            summary, cursor = result
            store.upsert_evidence(Evidence(company_id=company.company_id, source=source,
                                           cursor=cursor, payload_hash=_payload_hash(summary),
                                           payload=summary, fetched_at=now_iso()))
            harvested += 1
            per_source[source] = per_source.get(source, 0) + 1

    summary = {"companies": len(companies), "harvested": harvested, "skipped_fresh": skipped,
               "empty": empty, "per_source": per_source}
    store.audit(stage="stage2", action="flagged", reason="harvest_done", run_id=run_id,
                detail=summary)
    log.info("stage2: %s", summary)
    return summary
