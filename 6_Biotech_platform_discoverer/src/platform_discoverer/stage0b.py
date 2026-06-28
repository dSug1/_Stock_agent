"""Stage 0b — hard cuts: the ONLY deletions in the whole pipeline (spec §5.2).

Apply only unambiguous, recall-safe cuts:
  * market cap outside ``[min_usd, max_usd]`` (fully-diluted, FX→USD) → DELETE ``mktcap_out_of_band``
  * not live (delisted/halted/acquired shell)                          → DELETE ``not_live``
Everything else is KEEP + flag:
  * missing cap → KEEP + flag ``mktcap_unknown`` (NEVER drop on missing data).

Deletions route through ``Store.delete_company``, whose guardrail rejects any reason outside the
cardinal set — so this stage physically cannot delete for any other reason. Optional ``enricher``
(e.g. yfinance) fills cap/liveness for companies that arrived without them; it is fail-open (None →
keep + flag unknown).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Callable, Optional

from .models import Company
from .store import Store

log = logging.getLogger(__name__)

Enricher = Callable[[Company], Optional[dict]]


def run(store: Store, config: dict, *, run_id: str | None = None,
        enricher: Optional[Enricher] = None, max_enrich: int = 0) -> dict:
    """Run hard cuts over every company in the store. Returns a summary dict.

    ``enricher`` (yfinance) fills cap/liveness; at universe scale this is the slow step (one network
    call per company). ``max_enrich`` (0 = unlimited) bounds the number of enrichment calls; companies
    past the bound keep whatever cap they have (unknown → flagged, never dropped). Progress is logged.
    """
    mc = config.get("market_cap", {}) or {}
    min_usd = mc.get("min_usd", 0)
    max_usd = mc.get("max_usd", float("inf"))
    keep_if_unknown = mc.get("keep_if_unknown", True)
    tol = mc.get("near_band_tolerance", 0.0) or 0.0

    deleted_band = deleted_live = flagged_unknown = near_band = kept = enriched = 0
    companies = store.all_companies()
    total = len(companies)

    for company in companies:
        cap = company.mktcap_usd_fd
        live = company.is_live

        if enricher is not None and (not max_enrich or enriched < max_enrich):
            enriched += 1
            if enriched % 100 == 0:
                log.info("stage0b: enriched %d/%d ...", enriched, total)
            info = enricher(company)
            if info:
                if "mktcap_usd_fd" in info and info["mktcap_usd_fd"] is not None:
                    cap = info["mktcap_usd_fd"]
                if "is_live" in info:
                    live = bool(info["is_live"])
                # persist enrichment (cap/liveness + business description/sector/industry for M3).
                store.upsert_company(
                    replace(company, mktcap_usd_fd=cap, mktcap_unknown=(cap is None), is_live=live,
                            business_description=info.get("business_description")
                            or company.business_description,
                            sector=info.get("sector") or company.sector,
                            industry=info.get("industry") or company.industry),
                    run_id=run_id)

        ident = {"name": company.name, "ticker": company.primary_ticker,
                 "exchange": company.exchange}

        # 1) liveness — delete dead shells
        if not live:
            store.delete_company(company.company_id, reason="not_live", run_id=run_id,
                                 detail=ident)
            deleted_live += 1
            continue

        # 2) missing cap — KEEP + flag, never delete
        if cap is None:
            if keep_if_unknown:
                store.flag_company(company.company_id, "mktcap_unknown", stage="stage0b",
                                   run_id=run_id)
                flagged_unknown += 1
                kept += 1
            continue

        # 3) market-cap band — the only value-based deletion allowed
        if cap < min_usd or cap > max_usd:
            # near-band tolerance: within tol of a bound -> KEEP + review_queue, never delete
            near = ((cap < min_usd and cap >= min_usd * (1 - tol)) or
                    (cap > max_usd and cap <= max_usd * (1 + tol)))
            if near:
                bound = "floor" if cap < min_usd else "ceiling"
                store.add_to_review_queue(
                    company.company_id,
                    f"near_band_mktcap: {company.primary_ticker or company.company_id} "
                    f"${cap:,.0f} near {bound} [{min_usd:,}, {max_usd:,}]")
                store.audit(stage="stage0b", action="flagged", company_id=company.company_id,
                            reason="near_band_mktcap", run_id=run_id,
                            detail={**ident, "mktcap_usd_fd": cap, "bound": bound,
                                    "band": [min_usd, max_usd]})
                near_band += 1
                kept += 1
                continue
            store.delete_company(company.company_id, reason="mktcap_out_of_band", run_id=run_id,
                                 detail={**ident, "mktcap_usd_fd": cap, "band": [min_usd, max_usd]})
            deleted_band += 1
            continue

        kept += 1

    summary = {"kept": kept, "deleted_mktcap_out_of_band": deleted_band,
               "deleted_not_live": deleted_live, "flagged_mktcap_unknown": flagged_unknown,
               "near_band_review": near_band}
    store.audit(stage="stage0b", action="flagged", reason="hard_cuts_done",
                run_id=run_id, detail=summary)
    log.info("stage0b: %s", summary)
    return summary
