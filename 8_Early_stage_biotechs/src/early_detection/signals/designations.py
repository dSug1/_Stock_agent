"""Regulatory-designation signal (spec §3.4) — FDA (and cross-listed foreign) special designations.

FDA doesn't publish Breakthrough / Fast Track / Orphan / RMAT designations as structured data — but a
company MUST disclose a material designation in an 8-K (or a foreign private issuer in a 6-K). So this is
**phrase-first**, mirroring the ownership signal's fund-first shape: for each designation phrase, full-
text-search EDGAR (`edgar_fts`) for filings containing it, then match each filing's subject CIK back to
our universe. The filing is the company's OWN material-event filing (filed under its CIK), so a universe
CIK on a filing that contains "Breakthrough Therapy Designation" is that company announcing its own — a
high-precision, high-value catalyst signal: a designation is FDA validation that the *mechanism* is
promising, independent of the academic-citation trail.

Designations are DURABLE (they don't lapse like a 13D), so the lookback is years. Covers US 8-Ks and
cross-listed foreign issuers' 6-Ks in one pass. Idempotent (signal_id = hash(entity, type, accession));
commits per phrase; fail-soft per phrase. Zero-LLM, no key, no spend.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from ..clients import _net, edgar_fts
from ..config import Config
from ..models import SignalRecord
from ..store import Store, now_iso

log = logging.getLogger(__name__)

SIGNAL_TYPE = "regulatory_designation"
SOURCE = "edgar_fts"

_LIMITER = _net.RateLimiter(per_sec=5.0)


@dataclass
class DesignationResult:
    phrases: int = 0
    filings_seen: int = 0
    matched: int = 0                 # filings matched to a universe entity
    signals: int = 0
    by_type: dict = field(default_factory=dict)


def _norm_cik(cik: Optional[str]) -> Optional[str]:
    if not cik:
        return None
    d = "".join(ch for ch in str(cik) if ch.isdigit())
    return d.zfill(10) if d else None


def _signal_id(entity_id: str, dtype: str, adsh: Optional[str]) -> str:
    return "des_" + hashlib.sha1(f"{entity_id}|{dtype}|{adsh or ''}".encode()).hexdigest()[:20]


def ingest_designations(store: Store, cfg: Config, *, lookback_days: Optional[int] = None,
                        today: Optional[str] = None, max_pages: int = 100,
                        search: Callable[..., list[dict]] | None = None) -> DesignationResult:
    """Ingest regulatory-designation signals. ``search`` (phrase → filings) injectable for tests.

    A designation phrase is COMMON across all filers (e.g. ~8.8k "Orphan Drug Designation" filings), and
    efts can't filter to our ~780 universe CIKs in one query — so we paginate the phrase to exhaustion
    (``max_pages`` × 100; search_filings stops early at the true end) and keep only universe matches. The
    efts result window caps at 10,000 (from+size); a phrase exceeding that in the lookback would silently
    truncate — flagged here so it's a known bound, not a hidden one. These 5 phrases sit well under it."""
    lookback = lookback_days or cfg.designation_lookback_days
    base = date.fromisoformat(today) if today else date.today()
    startdt, enddt = (base - timedelta(days=lookback)).isoformat(), base.isoformat()

    def _default_search(phrase: str) -> list[dict]:
        return edgar_fts.search_filings(phrase, forms=cfg.designation_forms, startdt=startdt,
                                        enddt=enddt, max_pages=max_pages, limiter=_LIMITER)

    search = search or _default_search
    cik_map = store.cik_to_entity_id(active_only=True)
    phrases = dict(cfg.designation_phrases)
    res = DesignationResult(phrases=len(phrases))
    seen: set[str] = set()   # dedup within a run (a filing can surface across pages / phrases)
    log.info("designations: %d phrases vs %d universe CIKs (window %s..%s)",
             len(phrases), len(cik_map), startdt, enddt)

    for phrase, dtype in phrases.items():
        hits = search(phrase) or []
        n_sig = 0
        for h in hits:
            res.filings_seen += 1
            matched_here = False
            for cik in h.get("ciks") or []:
                eid = cik_map.get(_norm_cik(cik))
                if not eid:
                    continue
                sid = _signal_id(eid, dtype, h.get("adsh"))
                matched_here = True
                if sid in seen:
                    continue
                seen.add(sid)
                store.insert_signal(SignalRecord(
                    signal_id=sid, entity_id=eid, signal_type=SIGNAL_TYPE, source=SOURCE,
                    raw_payload={"designation": dtype, "phrase": phrase, "form": h.get("form"),
                                 "adsh": h.get("adsh"), "subject_cik": _norm_cik(cik),
                                 "display_names": h.get("display_names")},
                    detected_at=now_iso(), event_date=h.get("file_date"), language="en"), commit=False)
                res.signals += 1
                n_sig += 1
            if matched_here:
                res.matched += 1
        store.conn.commit()   # per-phrase commit (crash-safe)
        if n_sig:
            res.by_type[dtype] = res.by_type.get(dtype, 0) + n_sig
            log.info("designations: %s → %d signals", dtype, n_sig)

    log.info("designations done: filings_seen=%d matched=%d signals=%d by_type=%s",
             res.filings_seen, res.matched, res.signals, res.by_type)
    return res
