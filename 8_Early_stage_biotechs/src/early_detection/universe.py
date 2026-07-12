"""Universe orchestrator (phase1 build spec §3–§5): providers → reconcile → floor → persist.

Flow:
  1. Gather ``Listing`` records from each enabled provider (m6_seed, edgar_us, edgar_canada). Each is
     fail-soft — a dead provider contributes [] and is logged, never aborts the run (spec §6).
  2. ``identity.reconcile`` groups listings into canonical entities by the hard-key cascade; keyless/
     ambiguous listings go to the reconciliation queue (never force-merged, §2.3).
  3. Apply the $10M market-cap floor as a FLAG (not a delete) — below-floor entities are recorded
     ``is_live`` but audited ``below_cap_floor``; unknown cap is KEPT + flagged (repo recall-safe posture).
  4. Persist entities + their listings + queue rows + audit + a run_meta funnel.

Nothing here spends money (no Claude). Deterministic given the same provider output.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field, replace
from typing import Optional

from . import identity
from .config import Config
from .models import Listing
from .providers import canada, edgar_canada, edgar_us, europe, fund13f, m6_seed, nordic
from .store import Store, now_iso

log = logging.getLogger(__name__)


@dataclass
class UniverseResult:
    run_id: str
    counts: dict[str, int] = field(default_factory=dict)
    below_floor: int = 0
    queued: int = 0


def _run_id() -> str:
    # Timestamp-based, seconds precision. (Date.now()/random unavailable in some harness contexts;
    # here we're a normal script so wall-clock is fine.)
    from datetime import datetime, timezone
    return "u" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _config_hash(cfg: Config) -> str:
    payload = f"{sorted(cfg.sic_allow.items())}|{cfg.mktcap_floor_usd}|{sorted(cfg.markets)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def gather_listings(cfg: Config, *, use_m6: bool = True, use_us: bool = True,
                    use_ca: bool = True, use_nordic: bool = False, use_europe: bool = False,
                    use_ca_wikidata: bool = False, use_fund13f: bool = True,
                    max_pages: int = 30) -> dict[str, list[Listing]]:
    """Run each enabled provider; return {provider_id: [Listing]}. Fail-soft per provider.

    ``use_nordic`` is OPT-IN (default off): the Wikidata Nordic provider yields names with no market cap
    (mktcap_unknown), so running it into the default US/CA build would seed uncapped Nordic mega-caps
    into the active universe before a Nordic cap-enrich exists (see providers/nordic.py)."""
    out: dict[str, list[Listing]] = {}
    if use_m6:   # the existing-universe priority tier is market-independent (spec §2.4)
        out["m6"] = m6_seed.load_m6_listings(cfg.m6_store_path)
    if use_fund13f:   # specialist-fund holdings seed (D23) — closes EDGAR-enumeration gaps, CIK-keyed
        out["fund13f"] = fund13f.load_fund13f_listings(cfg.fund_store_path, cfg.sic_allow)
    if use_us and "US" in cfg.markets:
        out["edgar_us"] = edgar_us.load_us_listings(cfg.sic_allow, max_pages=max_pages)
    if use_ca and "CA" in cfg.markets:
        out["edgar_canada"] = edgar_canada.load_ca_listings(cfg.sic_allow, max_pages=max_pages)
    if use_nordic:
        out["nordic"] = nordic.load_nordic_listings()
    if use_europe:
        out["europe"] = europe.load_europe_listings()
    if use_ca_wikidata:
        out["ca_wikidata"] = canada.load_canada_wikidata_listings()
    return out


def build_universe(store: Store, cfg: Config, *, provider_listings: dict[str, list[Listing]] | None = None,
                   use_m6: bool = True, use_us: bool = True, use_ca: bool = True, use_nordic: bool = False,
                   use_europe: bool = False, use_ca_wikidata: bool = False, use_fund13f: bool = True,
                   max_pages: int = 30, dry_run: bool = False) -> UniverseResult:
    """End-to-end universe build. If ``provider_listings`` is given (tests), providers are skipped."""
    started = now_iso()
    run_id = _run_id()

    groups = provider_listings if provider_listings is not None else gather_listings(
        cfg, use_m6=use_m6, use_us=use_us, use_ca=use_ca, use_nordic=use_nordic,
        use_europe=use_europe, use_ca_wikidata=use_ca_wikidata, use_fund13f=use_fund13f, max_pages=max_pages)
    all_listings: list[Listing] = [l for lst in groups.values() for l in lst]
    per_source = {k: len(v) for k, v in groups.items()}
    log.info("gathered %d listings: %s", len(all_listings), per_source)

    result = identity.reconcile(all_listings)
    log.info("reconciled → %d entities, %d queued", len(result.entities), len(result.queued))

    below_floor = 0
    for eid, ent in result.entities.items():
        floored = _apply_floor(ent, cfg)
        if floored:
            below_floor += 1
        if dry_run:
            continue
        store.upsert_entity(replace(ent, below_floor=floored))
        # record each contributing listing as a listing row
        for lst in result.listings.get(eid, []):
            store.add_listing(eid, ticker=lst.ticker, exchange=lst.exchange, country=lst.country,
                              isin=lst.isin, mic=lst.mic, is_primary=lst.is_primary,
                              provenance=lst.provenance)
        store.log(stage="universe", action="admitted", run_id=run_id, entity_id=eid,
                  reason=",".join(ent.source_provenance) or None)
        if floored:
            store.log(stage="universe", action="flagged", run_id=run_id, entity_id=eid,
                      reason="below_cap_floor", detail={"market_cap_usd": ent.market_cap_usd})

    if not dry_run:
        for row in result.queued:
            store.queue_recon(row)
            store.log(stage="universe", action="queued", run_id=run_id, reason=row.reason)

    counts = {
        "listings_total": len(all_listings),
        **{f"src_{k}": v for k, v in per_source.items()},
        "entities": len(result.entities),
        "queued": len(result.queued),
        "below_floor": below_floor,
        **{f"recon_{k}": v for k, v in result.stats.items() if k.startswith("queued")},
    }
    if not dry_run:
        store.record_run(run_id=run_id, started=started, finished=now_iso(), market=",".join(cfg.markets),
                         counts=counts, config_hash=_config_hash(cfg))

    return UniverseResult(run_id=run_id, counts=counts, below_floor=below_floor, queued=len(result.queued))


def _apply_floor(ent, cfg: Config) -> bool:
    """Return True if the entity is below the market-cap floor (a FLAG, not a delete).

    Unknown cap → NOT floored (missing data is kept + flagged ``mktcap_unknown`` upstream, never
    dropped for absence of a value). Only a *known* cap below the floor trips the flag.
    """
    cap = ent.market_cap_usd
    if cap is None or ent.mktcap_unknown:
        return False
    return cap < cfg.mktcap_floor_usd
