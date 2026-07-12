"""Canada (TSX/TSXV) universe provider (spec §2.2) — Wikidata SPARQL via the shared builder.

The COMPLEMENT to ``edgar_canada`` (which already covers SEC-filing Canadian FPIs). This adds Canadian
biotech/pharma listings from Wikidata to reach TSX/TSXV-only names that don't file with the SEC. Identity
union-find merges any overlap (most Wikidata CA names are NYSE/Nasdaq cross-listed and already present via
EDGAR — the D16 path).

HONEST FINDING (spec §9 / `international_expansion_plan.md`): Wikidata CA coverage is thin — probed at
~8 listed names, mostly already-covered cross-listed ones — which **empirically confirms** that the TSX-
only universe is poorly served by keyless sources. True coverage needs a TMX/SEDAR+ listing scrape (no
clean API, bot-protected — deferred). Same opt-in + mktcap_unknown caveats as the other Wikidata markets.
"""

from __future__ import annotations

from typing import Optional

from ..clients import _net, wikidata
from ..models import Listing
from . import _wikidata_universe

PROVENANCE = "wikidata_canada"
_COUNTRY_QIDS = ("wd:Q16",)   # Canada


def load_canada_wikidata_listings(*, limiter: Optional[_net.RateLimiter] = None,
                                  query_fn=wikidata.sparql) -> list[Listing]:
    """Canadian biotech/pharma listings from Wikidata (TSX/TSXV complement to edgar_canada)."""
    return _wikidata_universe.load_biotech_listings(
        _COUNTRY_QIDS, PROVENANCE,
        limiter=limiter or _net.RateLimiter(per_sec=2.0), query_fn=query_fn)
