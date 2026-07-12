"""Wikidata SPARQL client (free, no key) — a structured, reachable universe source for markets whose
exchange feeds aren't cleanly accessible (spec §2.2 non-US enumeration).

Probed 2026-07-11: of the candidate Nordic/EU universe sources, ESMA FIRDS + EMA returned HTML/antibot
and the Nasdaq-Nordic feed timed out; **Wikidata SPARQL returned clean JSON**. So it's the pragmatic
enumerator — with the honest caveat that Wikidata skews to better-known (larger) names, so it's a SEED,
not complete micro-cap coverage (fuller coverage needs the exchange-listing scrape, deferred).

Endpoint is the hard-coded public SPARQL host (no user-URL / SSRF surface); the query is URL-encoded;
reads go through ``_net``'s 64 MiB cap + Retry-After retry; fail-open (→ {}). Pure ``rows`` parser is
unit-tested. Wikidata etiquette: one bounded query, paced by a limiter.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from . import _net

log = logging.getLogger(__name__)

BASE = "https://query.wikidata.org/sparql"


def sparql(query: str, *, limiter: Optional[_net.RateLimiter] = None) -> dict:
    """Run a SPARQL query → JSON results dict (or {} on failure). The query text is a hard-coded
    constant from the caller (a provider), never user input; still URL-encoded defensively."""
    if not query or not query.strip():
        return {}
    url = f"{BASE}?format=json&query={quote(query.strip(), safe='')}"
    return _net.safe_json_retry(url, limiter=limiter,
                                accept="application/sparql-results+json") or {}


def rows(payload: dict) -> list[dict]:
    """SPARQL JSON → [{var: value}] — flattens each binding's ``{var: {value: …}}`` to ``{var: value}``.
    Missing OPTIONAL vars are simply absent from a row (defensive)."""
    binds = (((payload or {}).get("results") or {}).get("bindings")) or []
    return [{k: (v or {}).get("value") for k, v in b.items()} for b in binds]
