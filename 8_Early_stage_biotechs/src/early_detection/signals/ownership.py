"""Ownership-crossing signal (spec §3.5 headline) — specialist-fund 5%+ crossings via EDGAR full-text.

Fund-first: for each fund in the watchlist, full-text-search efts for its SC 13D/G filings in the
lookback window, then match each filing's subject CIK back to our universe. This is what the M7
submissions approach structurally could not do (13D/G index under the filer's CIK, not the subject's).

Precision rails:
- The fund must appear in the filing's ``display_names`` (confirms it's an associated party, not a
  stray body mention) before we trust the hit.
- Only CIKs that are in **our universe** map are emitted — a fund's own CIK isn't a biotech, so it
  never self-matches.

Idempotent (signal_id = hash(entity, accession, fund)); commits per fund; fail-soft per fund.
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

SIGNAL_TYPE = "ownership_crossing"
SOURCE = "edgar_fts"

_LIMITER = _net.RateLimiter(per_sec=5.0)
_CIK_SUFFIX = re.compile(r"\s*\(cik\s*\d+\)\s*$", re.IGNORECASE)
_NONALNUM = re.compile(r"[^a-z0-9]+")


@dataclass
class OwnershipResult:
    funds: int = 0
    filings_seen: int = 0
    matched: int = 0                 # filings matched to a universe entity
    signals: int = 0
    by_fund: dict = field(default_factory=dict)


def _norm(s: str) -> str:
    """Lowercase + collapse non-alphanumerics (no suffix stripping — predictable containment match)."""
    return _NONALNUM.sub(" ", (s or "").lower()).strip()


def _norm_cik(cik: Optional[str]) -> Optional[str]:
    if not cik:
        return None
    d = "".join(ch for ch in str(cik) if ch.isdigit())
    return d.zfill(10) if d else None


def _fund_in_names(fund: str, display_names: list[str]) -> bool:
    nf = _norm(fund)
    return any(nf and nf in _norm(_CIK_SUFFIX.sub("", dn)) for dn in display_names)


def _signal_id(entity_id: str, adsh: Optional[str], fund: str) -> str:
    return "own_" + hashlib.sha1(f"{entity_id}|{adsh or ''}|{_norm(fund)}".encode()).hexdigest()[:20]


def ingest_ownership(store: Store, cfg: Config, *, lookback_days: Optional[int] = None,
                     today: Optional[str] = None, max_pages: int = 5,
                     search: Callable[..., list[dict]] | None = None) -> OwnershipResult:
    """Ingest specialist-fund ownership-crossing signals. ``search`` injectable for tests."""
    lookback = lookback_days or cfg.signal_lookback_days
    base = date.fromisoformat(today) if today else date.today()
    startdt, enddt = (base - timedelta(days=lookback)).isoformat(), base.isoformat()

    def _default_search(q: str) -> list[dict]:
        return edgar_fts.search_filings(q, forms=edgar_fts.OWNERSHIP_FORMS, startdt=startdt,
                                        enddt=enddt, max_pages=max_pages, limiter=_LIMITER)

    search = search or _default_search
    cik_map = store.cik_to_entity_id(active_only=True)
    res = OwnershipResult(funds=len(cfg.specialist_funds))
    seen: set[str] = set()   # efts `from`-pagination can repeat a hit on a relevance sort; dedup per run
    log.info("ownership: %d funds vs %d universe CIKs (window %s..%s)",
             len(cfg.specialist_funds), len(cik_map), startdt, enddt)

    for fund in cfg.specialist_funds:
        hits = search(fund) or []
        n_sig = 0
        for h in hits:
            res.filings_seen += 1
            if not _fund_in_names(fund, h.get("display_names") or []):
                continue
            matched_here = False
            for cik in h.get("ciks") or []:
                eid = cik_map.get(_norm_cik(cik))
                if not eid:
                    continue
                sid = _signal_id(eid, h.get("adsh"), fund)
                matched_here = True
                if sid in seen:
                    continue
                seen.add(sid)
                store.insert_signal(SignalRecord(
                    signal_id=sid, entity_id=eid,
                    signal_type=SIGNAL_TYPE, source=SOURCE,
                    raw_payload={"fund": fund, "form": h.get("form"), "adsh": h.get("adsh"),
                                 "subject_cik": _norm_cik(cik),
                                 "display_names": h.get("display_names")},
                    detected_at=now_iso(), event_date=h.get("file_date"), language="en",
                ), commit=False)
                res.signals += 1
                n_sig += 1
            if matched_here:
                res.matched += 1
        store.conn.commit()   # per-fund commit (crash-safe)
        if n_sig:
            res.by_fund[fund] = n_sig
            log.info("ownership: %s → %d signals", fund, n_sig)

    log.info("ownership done: filings_seen=%d matched=%d signals=%d", res.filings_seen, res.matched, res.signals)
    return res
