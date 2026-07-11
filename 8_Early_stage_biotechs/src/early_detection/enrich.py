"""Market-cap enrichment stage — fills USD market cap so the cap floor can gate the universe.

The universe build leaves EDGAR-discovered names with an unknown cap (``company_tickers.json`` carries
no cap). This stage fetches each unknown-cap entity's cap via yfinance, converts to USD, and persists
``market_cap_usd`` + ``below_floor`` so Phase-2 signal jobs can filter to the active (above-floor)
universe.

Discipline:
- **Persist each result immediately** (repo rule for long external-API loops): a mid-batch crash must
  not discard fetched work. We upsert per ticker, not in a final bulk write.
- **Fail-open**: a ticker with no data stays KEPT + ``mktcap_unknown`` (missing ≠ delete, missing ≠
  below-floor); we stamp ``enriched_at`` so it isn't retried every run.
- **yfinance is LOCAL-ONLY** (ToS) — swap for a licensed provider before any public deploy.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from .clients import gleif, market
from .config import Config
from .fx import FXConverter
from .models import ReconRow
from .store import Store, now_iso

log = logging.getLogger(__name__)


@dataclass
class EnrichResult:
    attempted: int = 0
    filled: int = 0
    below_floor: int = 0
    misses: int = 0
    by_ccy: dict[str, int] = field(default_factory=dict)


def enrich_caps(store: Store, cfg: Config, *, limit: int | None = None, per_sec: float = 3.0,
                fetch: Callable[[str], Optional[dict]] | None = None,
                progress_every: int = 50) -> EnrichResult:
    """Fetch USD market caps for unknown-cap entities and persist each immediately.

    ``fetch`` is injectable for tests (defaults to ``market.fetch_market_cap``). ``per_sec`` throttles
    the (local) yfinance calls. Returns an ``EnrichResult`` funnel.
    """
    fetch = fetch or market.fetch_market_cap
    fx = FXConverter()
    todo = store.entities_needing_cap(limit=limit)
    res = EnrichResult(attempted=len(todo))
    min_interval = 1.0 / per_sec if per_sec > 0 else 0.0
    log.info("enrich: %d entities need a market cap", len(todo))

    last = 0.0
    for i, ent in enumerate(todo, 1):
        if min_interval:
            gap = min_interval - (time.monotonic() - last)
            if gap > 0:
                time.sleep(gap)
            last = time.monotonic()

        info = fetch(ent.ticker_primary) or {}
        usd = fx.to_usd(info.get("mktcap_native"), info.get("currency"))
        below = store.apply_cap(ent.entity_id, market_cap_usd=usd, currency=info.get("currency"),
                                floor_usd=cfg.mktcap_floor_usd, ceiling_usd=cfg.mktcap_ceiling_usd,
                                ipo_date=info.get("ipo_date"), enriched_at=now_iso())
        if usd is None:
            res.misses += 1
            store.log(stage="enrich", action="flagged", entity_id=ent.entity_id,
                      reason="cap_unavailable")
        else:
            res.filled += 1
            ccy = (info.get("currency") or "USD").upper()
            res.by_ccy[ccy] = res.by_ccy.get(ccy, 0) + 1
            if below:
                res.below_floor += 1
                store.log(stage="enrich", action="flagged", entity_id=ent.entity_id,
                          reason="below_cap_floor", detail={"market_cap_usd": usd})
        if progress_every and i % progress_every == 0:
            log.info("enrich: %d/%d (filled=%d below_floor=%d misses=%d)",
                     i, len(todo), res.filled, res.below_floor, res.misses)

    log.info("enrich done: filled=%d below_floor=%d misses=%d by_ccy=%s",
             res.filled, res.below_floor, res.misses, res.by_ccy)
    return res


@dataclass
class LeiResult:
    attempted: int = 0
    filled: int = 0
    misses: int = 0            # no confident match
    collisions: int = 0        # LEI already held by a different entity → queued, not set


def enrich_lei(store: Store, cfg: Config, *, limit: int | None = None, concurrency: int = 8,
               search: Callable[..., list[dict]] | None = None,
               progress_every: int = 50) -> LeiResult:
    """Backfill LEIs (GLEIF) for entities that lack one. HIGH-PRECISION: only a unique exact
    normalized-name match is accepted (``gleif.pick_lei``). Persists per entity; fail-open.

    **Concurrency**: GLEIF searches run in a thread pool (``concurrency`` workers) — GLEIF is a
    network-bound public API that tolerates bursts and 429-retries internally (``gleif._get``), so
    ~8 workers ≈ 12/s vs ~2/s sequential. **Persistence stays on the main thread** as results arrive
    in order (SQLite connection is single-threaded), so per-entity crash-safety is preserved.

    Collision guard: if the picked LEI is already held by a *different* entity, we do NOT set it (that
    would create two rows with one LEI, and LEI is the top merge key) — instead we queue a review row,
    because it usually means the two rows are the same company (a store duplicate to reconcile by hand).
    """
    search = search or gleif.search_lei
    todo = store.entities_needing_lei(limit=limit)
    res = LeiResult(attempted=len(todo))
    log.info("lei-enrich: %d entities need an LEI (concurrency=%d)", len(todo), concurrency)

    def _fetch(ent):
        return ent, (search(ent.legal_name, country=ent.jurisdiction) or [])

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        for i, (ent, candidates) in enumerate(ex.map(_fetch, todo), 1):
            lei = gleif.pick_lei(ent.legal_name, candidates)
            if not lei:
                res.misses += 1
            else:
                holder = store.find_entity_by_key(lei=lei)
                if holder is not None and holder.entity_id != ent.entity_id:
                    res.collisions += 1
                    store.queue_recon(ReconRow(
                        candidate={"entity_id": ent.entity_id, "name": ent.legal_name, "lei": lei,
                                   "collides_with": holder.entity_id},
                        reason="gleif_lei_collision", added_at=now_iso()))
                    store.log(stage="lei_enrich", action="queued", entity_id=ent.entity_id,
                              reason="gleif_lei_collision", detail={"lei": lei, "holder": holder.entity_id})
                else:
                    store.set_lei(ent.entity_id, lei)
                    res.filled += 1
                    store.log(stage="lei_enrich", action="flagged", entity_id=ent.entity_id,
                              reason="lei_backfilled", detail={"lei": lei})
            if progress_every and i % progress_every == 0:
                log.info("lei-enrich: %d/%d (filled=%d misses=%d collisions=%d)",
                         i, len(todo), res.filled, res.misses, res.collisions)

    log.info("lei-enrich done: filled=%d misses=%d collisions=%d",
             res.filled, res.misses, res.collisions)
    return res
