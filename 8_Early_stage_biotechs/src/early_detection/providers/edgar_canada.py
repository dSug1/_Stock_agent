"""Canada universe provider (spec §2.2 supplementary, phase1 §3.3) — EDGAR FPI path.

Enumerates SEC-filing Canadian issuers (40-F/20-F/6-K foreign private issuers) under the biotech SIC
set, by filtering browse-edgar to Canadian province location codes. This is the clean, free path;
SEDAR+/SEDI scraping for Canada-only (non-SEC-filing) names is deferred (spec §2.5) and is a known
Phase-1 coverage gap, not a silent drop. Fail-soft (→ []).
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional

from ..clients import _net, sec
from ..models import Listing

log = logging.getLogger(__name__)

_LIMITER = _net.RateLimiter(per_sec=2.0)


def load_ca_listings(sic_map: Mapping[str, str], *, max_pages: int = 30,
                     limiter: Optional[_net.RateLimiter] = None) -> list[Listing]:
    try:
        return sec.build_ca_listings(sic_map, limiter=limiter or _LIMITER, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 — provider isolation (spec §6)
        log.warning("edgar_canada: enumeration failed: %s", exc)
        return []
