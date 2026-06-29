"""International (Europe/Nordic + Japan/Korea) universe enumeration via Wikidata SPARQL (free, no key).

SEC only covers US filers, so the non-US listed-biotech universe needs another free source. Wikidata
is the pragmatic one: query companies whose *industry* (P452) is biotechnology or pharmaceuticals,
whose *country* (P17) is in-scope (Sweden, Denmark, the broader EU set, and — D25 — Japan/South Korea),
and which have a *stock-exchange listing* (P414) carrying a *ticker* (P249 qualifier).

**Coverage caveat (honest):** Wikidata is notable-entity-biased — it captures mid/large and many
small caps but will miss some nano-caps. It is a real, free pan-European net to start with; a
licensed vendor screener remains the eventual upgrade for exhaustive coverage. Best-effort by design.

The result parser (`parse_sparql`) is pure and unit-tested; the fetch is fail-open. The SPARQL query
text + country QIDs live here and are easy to edit; validate the live result shape on the first run.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Iterable, Optional

from ..models import ListingRecord
from . import _net

log = logging.getLogger(__name__)

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

# Region code -> Wikidata country QID. "EU" expands to a broad European set (incl. SE/DK). JP/KR (D25)
# are single-country regions enumerated the same way — Wikidata covers TSE/KRX-listed pharma/biotech.
COUNTRY_QID = {
    "SE": "Q34", "DK": "Q35", "FI": "Q33", "NO": "Q20",
    "DE": "Q183", "FR": "Q142", "NL": "Q55", "CH": "Q39", "GB": "Q145",
    "BE": "Q31", "IT": "Q38", "ES": "Q29", "AT": "Q40", "IE": "Q27",
    "JP": "Q17", "KR": "Q884",
}
_EU_BROAD = ["SE", "DK", "FI", "NO", "DE", "FR", "NL", "CH", "GB", "BE", "IT", "ES", "AT", "IE"]

# Wikidata industry QIDs: biotechnology, pharmaceutical industry.
_INDUSTRY_QIDS = ["wd:Q7108", "wd:Q507443"]


def regions_to_qids(regions: Iterable[str]) -> list[str]:
    """Map run regions (e.g. ['SE','DK','EU']) to a de-duplicated list of country QIDs."""
    codes: list[str] = []
    for r in regions:
        if r == "EU":
            codes.extend(_EU_BROAD)
        elif r in COUNTRY_QID:
            codes.append(r)
    qids: list[str] = []
    for c in codes:
        q = COUNTRY_QID.get(c)
        if q and q not in qids:
            qids.append(q)
    return qids


def build_query(country_qids: list[str]) -> str:
    countries = " ".join(f"wd:{q}" for q in country_qids)
    industries = " ".join(_INDUSTRY_QIDS)
    return f"""
SELECT DISTINCT ?company ?companyLabel ?ticker ?exchangeLabel ?countryLabel ?industryLabel WHERE {{
  VALUES ?industry {{ {industries} }}
  VALUES ?country {{ {countries} }}
  ?company wdt:P452 ?industry .
  ?company wdt:P17 ?country .
  ?company p:P414 ?stmt .
  ?stmt pq:P249 ?ticker .
  OPTIONAL {{ ?stmt ps:P414 ?exchange . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 5000
""".strip()


def parse_sparql(payload: dict) -> list[dict]:
    """Wikidata SPARQL JSON → list of {name, ticker, exchange, country, industry} dicts."""
    out: list[dict] = []
    for b in (payload.get("results", {}) or {}).get("bindings", []):
        def _v(key: str) -> Optional[str]:
            cell = b.get(key)
            return cell.get("value") if cell else None
        name = _v("companyLabel")
        ticker = _v("ticker")
        if not name or not ticker:
            continue
        out.append({"name": name, "ticker": ticker, "exchange": _v("exchangeLabel"),
                    "country": _v("countryLabel"), "industry": _v("industryLabel")})
    return out


def _gics_from_industry(industry: Optional[str]) -> str:
    """Tag with a config GICS label so the Stage-0a `sector` net admits the record."""
    if industry and "pharm" in industry.lower():
        return "Pharmaceuticals"
    return "Biotechnology"


def build_eu_records(regions: Iterable[str], *,
                     limiter: Optional[_net.RateLimiter] = None) -> list[ListingRecord]:
    """Enumerate the Europe/Nordic listed biotech-pharma universe → ListingRecords (sector net).

    Tickers are returned raw (no Yahoo suffix); the directory provider attaches the suffix for
    yfinance enrichment. Fail-open (→ []).
    """
    qids = regions_to_qids(regions)
    if not qids:
        return []
    query = build_query(qids)
    url = f"{SPARQL_ENDPOINT}?{urllib.parse.urlencode({'query': query, 'format': 'json'})}"
    payload = _net.safe_json(url, accept="application/sparql-results+json", limiter=limiter)
    if not payload:
        log.warning("Wikidata SPARQL unavailable; EU/Nordic enumeration empty this run")
        return []
    records: list[ListingRecord] = []
    for row in parse_sparql(payload):
        records.append(ListingRecord(
            name=row["name"], ticker=row["ticker"], exchange=row.get("exchange"),
            country=row.get("country"), gics_industry=_gics_from_industry(row.get("industry")),
            provenance=["sector"]))
    log.info("Wikidata: %d EU/Nordic records across %d countries", len(records), len(qids))
    return records
