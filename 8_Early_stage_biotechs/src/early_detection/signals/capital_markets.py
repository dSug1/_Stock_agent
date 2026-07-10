"""Capital-markets signal ingester (spec §3.5) — the highest-value, most-reliable free source.

For each active-universe entity with a CIK, reads recent SEC filings (submissions API) and writes a
`signal` row per material filing — 5%+ ownership crossings (SC 13D/G), insider Form-4s, material 8-Ks,
and registration/shelf/ATM raises — within the configured lookback window. Zero-LLM.

Disciplines: fetches run in a bounded thread pool (network-bound), but **persistence stays on the main
thread** (SQLite single-threaded) and is committed per entity, so a mid-run crash keeps prior work.
Idempotent: signal_id is a stable hash of (entity, accession, form), so re-runs upsert, never duplicate.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from ..clients import _net, edgar_signals
from ..config import Config
from ..models import SignalRecord
from ..store import Store, now_iso

log = logging.getLogger(__name__)

SIGNAL_TYPE = "capital_markets"
SOURCE = "edgar_submissions"

# SEC fair-access is ~10 req/s; stay well under with a per-worker limiter shared across the pool.
_LIMITER = _net.RateLimiter(per_sec=8.0)


@dataclass
class SignalIngestResult:
    entities: int = 0
    with_filings: int = 0        # entities that had ≥1 material filing
    signals: int = 0             # total signal rows written
    by_form: dict = None

    def __post_init__(self):
        if self.by_form is None:
            self.by_form = {}


def _signal_id(entity_id: str, accession: Optional[str], form: str) -> str:
    raw = f"{entity_id}|{accession or ''}|{form}"
    return "cap_" + hashlib.sha1(raw.encode()).hexdigest()[:20]


def ingest_capital_markets(store: Store, cfg: Config, *, limit: int | None = None,
                           concurrency: int = 6, today: Optional[str] = None,
                           fetch: Callable[..., list[dict]] | None = None,
                           progress_every: int = 100) -> SignalIngestResult:
    """Ingest capital-markets signals for the active universe. Fetch is injectable for tests."""
    material = set(cfg.material_forms)

    def _default_fetch(cik: str) -> list[dict]:
        return edgar_signals.recent_material_filings(
            cik, material_forms=material, lookback_days=cfg.signal_lookback_days,
            today=today, limiter=_LIMITER)

    fetch = fetch or _default_fetch
    todo = store.entities_for_signals(limit=limit)
    res = SignalIngestResult(entities=len(todo))
    log.info("capital-markets: %d active entities (concurrency=%d, lookback=%dd)",
             len(todo), concurrency, cfg.signal_lookback_days)

    def _work(ent):
        return ent, (fetch(ent.cik) or [])

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
        for i, (ent, filings) in enumerate(ex.map(_work, todo), 1):
            if filings:
                res.with_filings += 1
            for f in filings:
                sid = _signal_id(ent.entity_id, f.get("accession"), f["form"])
                store.insert_signal(SignalRecord(
                    signal_id=sid, entity_id=ent.entity_id, signal_type=SIGNAL_TYPE, source=SOURCE,
                    raw_payload=f, detected_at=now_iso(), event_date=f.get("date"), language="en",
                ), commit=False)
                res.signals += 1
                res.by_form[f["form"]] = res.by_form.get(f["form"], 0) + 1
            store.conn.commit()   # commit per entity (crash-safe), not per signal
            if progress_every and i % progress_every == 0:
                log.info("capital-markets: %d/%d (signals=%d)", i, len(todo), res.signals)

    log.info("capital-markets done: entities=%d with_filings=%d signals=%d by_form=%s",
             res.entities, res.with_filings, res.signals, res.by_form)
    return res
