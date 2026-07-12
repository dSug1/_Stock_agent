"""Shared Wikidata universe builder — enumerate LISTED biotech/pharma companies for a set of countries.

Both the Nordic (M18) and Europe-broad (M19) providers are the same query with a different country set,
so the logic lives here once (the reuse promised in ``international_expansion_plan.md``). A market
provider just supplies its country QIDs + provenance tag.

Discipline carried from M18: admit only on a SECURITY identifier (ISIN or ticker), NOT LEI alone — every
legal entity (incl. private companies) can hold an LEI, but this is a universe of LISTED names. LEI is
kept for identity union-find. Names with no security id are dropped (they'd queue keyless). Fail-soft.
"""

from __future__ import annotations

import logging
from typing import Iterable, Mapping, Optional

from ..clients import _net, wikidata
from ..models import Listing

log = logging.getLogger(__name__)

# Wikidata English country label → ISO-3166 alpha-2, matching the jurisdiction codes already in the store
# (note "United Kingdom" → UK, the repo's existing convention, not GB).
COUNTRY_ISO: dict[str, str] = {
    "Sweden": "SE", "Denmark": "DK", "Norway": "NO", "Finland": "FI", "Iceland": "IS",
    "Germany": "DE", "France": "FR", "United Kingdom": "UK", "Switzerland": "CH",
    "Netherlands": "NL", "Belgium": "BE", "Italy": "IT", "Spain": "ES", "Ireland": "IE",
    "Austria": "AT", "Portugal": "PT", "Poland": "PL", "Canada": "CA",
}

# Industries: biotechnology (Q7108), pharmaceutical company (Q507443), pharmaceutical industry (Q11190).
_INDUSTRIES = "wd:Q7108 wd:Q507443 wd:Q11190"


def _query(country_qids: Iterable[str]) -> str:
    qids = " ".join(country_qids)
    return f"""
SELECT ?c ?cLabel ?ticker ?isin ?lei ?countryLabel ?exchangeLabel WHERE {{
  ?c wdt:P452 ?ind . VALUES ?ind {{ {_INDUSTRIES} }}
  ?c wdt:P17 ?country . VALUES ?country {{ {qids} }}
  OPTIONAL {{ ?c wdt:P414 ?exch . OPTIONAL {{ ?exch rdfs:label ?exchangeLabel FILTER(lang(?exchangeLabel)="en") }} }}
  OPTIONAL {{ ?c wdt:P249 ?ticker }}
  OPTIONAL {{ ?c wdt:P946 ?isin }}
  OPTIONAL {{ ?c wdt:P1278 ?lei }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" }}
}}
"""


def load_biotech_listings(country_qids: Iterable[str], provenance: str, *,
                          iso_by_label: Mapping[str, str] = COUNTRY_ISO,
                          limiter: Optional[_net.RateLimiter] = None,
                          query_fn=wikidata.sparql) -> list[Listing]:
    """Listed (ISIN/ticker) biotech/pharma companies for ``country_qids``. ``query_fn`` injectable."""
    try:
        payload = query_fn(_query(country_qids), limiter=limiter)
    except Exception as exc:  # noqa: BLE001 — provider isolation (spec §6)
        log.warning("%s: wikidata enumeration failed: %s", provenance, exc)
        return []

    out: list[Listing] = []
    seen: set[str] = set()
    dropped = 0
    for r in wikidata.rows(payload):
        isin, lei, ticker = r.get("isin"), r.get("lei"), r.get("ticker")
        if not (isin or ticker):        # security id required; LEI alone ≠ listed (M18 precision rail)
            dropped += 1
            continue
        wd = r.get("c")                 # Wikidata entity URI — dedup (a co can list on 2 venues)
        if wd in seen:
            continue
        seen.add(wd)
        out.append(Listing(
            name=(r.get("cLabel") or "").strip(),
            ticker=(ticker or None),
            exchange=(r.get("exchangeLabel") or None),
            country=iso_by_label.get(r.get("countryLabel"), None),
            isin=(isin or None),
            lei=(lei or None),
            sector_normalized="therapeutics",
            mktcap_unknown=True,        # Wikidata has no cap → flag (kept, recall-safe)
            is_primary=True,
            in_existing_universe=False,
            provenance=[provenance],
        ))
    log.info("%s: %d listed (ISIN/ticker) admitted, %d unlisted/keyless dropped", provenance, len(out), dropped)
    return out
