"""Stage 2 — evidence harvesting (spec §5.4).

For each Stage-1-retained company, harvest a structured evidence bundle from the free science/clinical/
IP sources and persist it to the ``evidence`` table (one row per company+source, with a cursor for
incremental re-runs). Raw-ish normalized summaries are stored once; downstream stages (embed/score)
recompute against them without re-harvesting (reversibility §1.4).

Sources wired here (the free, high-signal core): **OpenAlex** (publications/concepts/impact),
**ClinicalTrials.gov** (trials/phases/biomarker-Dx language), **PatentsView** (method/platform
patents — key-gated, inert without a key). EDGAR full-text and IR-poster scraping are spec sources
deferred to a follow-up (they need filing-text fetch + per-company IR-URL discovery).

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

from . import prestige
from .clients import _net, clinicaltrials, openalex, patentsview
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
    enabled = s2.get("sources", ["openalex", "ctgov", "patents", "pedigree"])
    mailto = s2.get("openalex_mailto") or _openalex_mailto_from_ua()
    oa_lim = _net.RateLimiter(float(s2.get("openalex_rate_per_sec", 8)))
    ct_lim = _net.RateLimiter(float(s2.get("ctgov_rate_per_sec", 4)))
    pv_lim = _net.RateLimiter(float(s2.get("patents_rate_per_sec", 4)))
    pv_key = os.getenv("PATENTSVIEW_API_KEY", "").strip() or None
    prestige_idx = prestige.build_index(prestige.load_prestige(
        s2.get("prestige_labs", "config/prestige_labs.yaml")))

    harvesters: dict[str, Harvester] = {}
    if "openalex" in enabled:
        harvesters["openalex"] = lambda c: openalex.fetch(c.name, limiter=oa_lim, mailto=mailto)
    if "ctgov" in enabled:
        harvesters["ctgov"] = lambda c: clinicaltrials.fetch(c.name, limiter=ct_lim)
    if "patents" in enabled:
        harvesters["patents"] = lambda c: patentsview.fetch(c.name, limiter=pv_lim, api_key=pv_key)
    if "pedigree" in enabled:
        harvesters["pedigree"] = lambda c: _harvest_pedigree(c.name, oa_lim, mailto, prestige_idx)
    return harvesters


def _harvest_pedigree(name, limiter, mailto, prestige_idx) -> Optional[tuple[dict, str]]:
    """Founder/scientific pedigree: the company's top OpenAlex authors + prestige-awardee matches."""
    authors = openalex.fetch_top_authors(name, limiter=limiter, mailto=mailto) or []
    matches = prestige.match([a["name"] for a in authors], prestige_idx)
    if not authors and not matches:
        return None
    summary = {"top_authors": authors[:8], "prestige_recognitions": matches,
               "max_h_index": max((a.get("h_index") or 0 for a in authors), default=0)}
    return summary, str(summary["max_h_index"])


def _openalex_mailto_from_ua() -> Optional[str]:
    import re
    m = re.search(r"[\w.+-]+@[\w.-]+", _net.user_agent())
    return m.group(0) if m else None


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
