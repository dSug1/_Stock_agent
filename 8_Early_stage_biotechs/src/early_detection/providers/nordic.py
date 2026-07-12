"""Nordic universe provider (spec §2.2 non-US) — Wikidata SPARQL via the shared builder.

Enumerates Sweden / Denmark / Norway / Finland / Iceland listed biotech + pharma companies. See
``_wikidata_universe.py`` for the shared logic + the ISIN/ticker admission rail, and
``international_expansion_plan.md`` for why Wikidata (the only Nordic source that probed clean) and its
honest limits (large-skewed SEED; no cap → names enter mktcap_unknown → opt-in provider so it never
pollutes the default US/CA build until a Nordic cap-enrich exists).
"""

from __future__ import annotations

from typing import Optional

from ..clients import _net, wikidata
from ..models import Listing
from . import _wikidata_universe

PROVENANCE = "wikidata_nordic"
# Sweden Q34, Denmark Q35, Norway Q20, Finland Q33, Iceland Q189.
_COUNTRY_QIDS = ("wd:Q34", "wd:Q35", "wd:Q20", "wd:Q33", "wd:Q189")


def load_nordic_listings(*, limiter: Optional[_net.RateLimiter] = None,
                         query_fn=wikidata.sparql) -> list[Listing]:
    """Nordic biotech/pharma listings from Wikidata. ``query_fn`` injectable for offline tests."""
    return _wikidata_universe.load_biotech_listings(
        _COUNTRY_QIDS, PROVENANCE,
        limiter=limiter or _net.RateLimiter(per_sec=2.0), query_fn=query_fn)
