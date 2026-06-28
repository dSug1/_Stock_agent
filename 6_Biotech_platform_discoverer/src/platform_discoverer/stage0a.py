"""Stage 0a — universe assembly by UNION of nets (spec §5.1).

Goal: maximize recall. A company is admitted if ANY net catches it (UNION, not intersection), using
the BROAD sector set — the Acrivon-pattern company is frequently mis-coded as Tools/Equipment/
Software, so strict "Biotechnology" would drop it. No filtering beyond union membership here;
narrowing happens later, cheaply and reversibly. Records are dedup'd to canonical companies first
(identity resolution, not a cut — see ``dedup``).

Nets (all from ``config['stage0a_nets']``):
  sector        — record.sic ∈ sic[] OR gics_industry ∈ gics_industries[] OR icb_equiv ∈ icb_equiv[]
  index         — any of record.indices ∈ index_membership[]
  name_keyword  — any keyword is a substring of the (lowercased) name
  seed_list     — record provenance includes "seed_list"
"""

from __future__ import annotations

import logging
from typing import Iterable

from . import dedup
from .listings import record_to_company
from .models import ListingRecord
from .store import Store

log = logging.getLogger(__name__)


def match_nets(rec: ListingRecord, nets_cfg: dict) -> list[str]:
    """Return the list of Stage-0a nets that this record hits (possibly several)."""
    hits: list[str] = []

    sector = nets_cfg.get("sector_codes", {}) or {}
    sic = set(sector.get("sic", []) or [])
    gics = set(sector.get("gics_industries", []) or [])
    icb = set(sector.get("icb_equiv", []) or [])
    if (rec.sic and rec.sic in sic) or (rec.gics_industry and rec.gics_industry in gics) \
            or (rec.icb_equiv and rec.icb_equiv in icb):
        hits.append("sector")

    index_membership = set(nets_cfg.get("index_membership", []) or [])
    if index_membership and set(rec.indices) & index_membership:
        hits.append("index")

    name_lc = (rec.name or "").lower()
    keywords = nets_cfg.get("name_keywords", []) or []
    if any(kw.lower() in name_lc for kw in keywords):
        hits.append("name_keyword")

    if "seed_list" in rec.provenance:
        hits.append("seed_list")

    return hits


def run(store: Store, records: Iterable[ListingRecord], config: dict, *,
        run_id: str | None = None) -> dict:
    """Admit union-of-nets members as ``Company`` rows. Returns a summary dict."""
    nets_cfg = config.get("stage0a_nets", {}) or {}
    priority_cfg = (config.get("stage0a_nets", {}) or {}).get("exchange_priority")

    canonical = dedup.collapse(records, exchange_priority=priority_cfg)
    admitted = 0
    no_net = 0
    for rec in canonical:
        hits = match_nets(rec, nets_cfg)
        if not hits:
            no_net += 1
            continue
        company = record_to_company(rec, hits)
        store.upsert_company(company, run_id=run_id)
        admitted += 1

    summary = {"records_in": len(canonical), "admitted": admitted, "no_net": no_net}
    store.audit(stage="stage0a", action="flagged", reason="universe_assembled",
                run_id=run_id, detail=summary)
    log.info("stage0a: %s", summary)
    return summary
