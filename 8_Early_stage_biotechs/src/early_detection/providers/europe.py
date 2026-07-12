"""Europe-broad universe provider (spec §2.2 non-US) — Wikidata SPARQL via the shared builder.

Enumerates the non-Nordic Western-European listed biotech + pharma universe: Germany, France, UK,
Switzerland, Netherlands, Belgium, Italy, Spain, Ireland, Austria. (Nordic is its own provider; identity
union-find merges any overlap by ISIN/LEI.) Same shared logic + ISIN/ticker admission rail as Nordic.

Honest limits (spec §9, see ``international_expansion_plan.md``): a Wikidata SEED (large-skewed, not
complete micro-cap coverage — fuller needs ESMA FIRDS, which probed as HTML); no market cap → names enter
``mktcap_unknown`` → **opt-in** (`--europe`) so the default US/CA build isn't polluted before a European
cap-enrich exists. The EU capital signal (per-country major-holdings) + EMA designations are deferred
(EMA probed as antibot HTML). But once in the universe, the inherited signals fire: CT.gov clinical
(global), FDA-designations-via-6-K + FPI capital (for US-cross-listed EU ADRs), OpenAlex, GLEIF.
"""

from __future__ import annotations

from typing import Optional

from ..clients import _net, wikidata
from ..models import Listing
from . import _wikidata_universe

PROVENANCE = "wikidata_europe"
# DE Q183, FR Q142, UK Q145, CH Q39, NL Q55, BE Q31, IT Q38, ES Q29, IE Q27, AT Q40.
_COUNTRY_QIDS = ("wd:Q183", "wd:Q142", "wd:Q145", "wd:Q39", "wd:Q55",
                 "wd:Q31", "wd:Q38", "wd:Q29", "wd:Q27", "wd:Q40")


def load_europe_listings(*, limiter: Optional[_net.RateLimiter] = None,
                         query_fn=wikidata.sparql) -> list[Listing]:
    """Broad-EU biotech/pharma listings from Wikidata. ``query_fn`` injectable for offline tests."""
    return _wikidata_universe.load_biotech_listings(
        _COUNTRY_QIDS, PROVENANCE,
        limiter=limiter or _net.RateLimiter(per_sec=2.0), query_fn=query_fn)
