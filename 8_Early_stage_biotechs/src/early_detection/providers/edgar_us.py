"""US universe provider (spec §2.2, phase1 §3.2) — thin wrapper over ``clients.sec``.

Enumerates every listed SEC filer under the biotech/pharma/life-sciences SIC allow-map and returns
``Listing`` records with CIK + normalized sector populated. Fail-soft (a network error → []).
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional

from ..clients import _net, sec
from ..models import Listing

log = logging.getLogger(__name__)

# SEC fair-access: ≤10 req/s. Keep well under (2/s) — enumeration is paginated, not latency-critical.
_LIMITER = _net.RateLimiter(per_sec=2.0)


def load_us_listings(sic_map: Mapping[str, str], *, max_pages: int = 30,
                     limiter: Optional[_net.RateLimiter] = None) -> list[Listing]:
    """Enumerate US listed biotech/pharma/tools filers → ``Listing``s. Fail-open (→ [])."""
    try:
        return sec.build_us_listings(sic_map, limiter=limiter or _LIMITER, max_pages=max_pages)
    except Exception as exc:  # noqa: BLE001 — provider isolation (spec §6)
        log.warning("edgar_us: enumeration failed: %s", exc)
        return []
