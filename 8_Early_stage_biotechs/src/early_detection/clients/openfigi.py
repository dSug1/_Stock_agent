"""OpenFIGI client (free, no key) — ISIN → exchange-suffixed ticker for non-US market-cap enrichment.

The Wikidata-seeded foreign names (Nordic/EU/CA) carry an ISIN but usually no ticker, and yfinance needs
an exchange-SUFFIXED symbol (Zealand = ``ZEAL.CO``, Argenx = ``ARGX.BR``). OpenFIGI maps an ISIN to its
listings; its FIRST record is the primary home venue (verified: Zealand→ZEAL/DC, Argenx→ARGX/BB,
Abivax→ABVX/FP), whose Bloomberg exchange code we translate to a yfinance suffix. Pan-European MTF/
composite codes (EO/XH/XF/…) are not real exchanges and are skipped.

Keyless (free tier ~25 req/min → paced by a limiter); POST body is JSON; the endpoint is hard-coded (no
SSRF); reads are 64 MiB-capped + retry (429/5xx). Fail-open (→ None). A wrong/unknown exchange code just
yields no symbol → the entity stays mktcap_unknown (recall-safe), never a bad cap.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from . import _net

log = logging.getLogger(__name__)

BASE = "https://api.openfigi.com/v3/mapping"

# Bloomberg exchange code (OpenFIGI ``exchCode``) → yfinance ticker suffix. PRIMARY venues only; composite
# / MTF codes are intentionally absent so they're skipped. Verified codes marked; others best-effort
# (fail-open if wrong). US venues → "" (no suffix).
EXCH_SUFFIX: dict[str, str] = {
    "DC": ".CO",                       # Copenhagen (Nasdaq)  [verified]
    "SS": ".ST",                       # Stockholm (Nasdaq)
    "NO": ".OL",                       # Oslo Børs
    "FH": ".HE",                       # Helsinki (Nasdaq)
    "IR": ".IC",                       # Iceland (Nasdaq)
    "GY": ".DE", "GR": ".DE",          # XETRA / Frankfurt → yfinance .DE
    "FP": ".PA",                       # Euronext Paris  [verified]
    "LN": ".L",                        # London (LSE)
    "SW": ".SW", "VX": ".SW", "SE": ".SW",  # SIX Swiss
    "NA": ".AS",                       # Euronext Amsterdam
    "BB": ".BR",                       # Euronext Brussels  [verified]
    "IM": ".MI",                       # Borsa Italiana (Milan)
    "SM": ".MC", "SQ": ".MC",          # BME Madrid
    "ID": ".IR",                       # Euronext Dublin
    "CT": ".TO", "CN": ".TO",          # Toronto (TSX)
    "CV": ".V",                        # TSX Venture
    "US": "", "UN": "", "UW": "", "UQ": "", "UR": "", "UA": "", "UP": "",  # US → no suffix
}


def _map_isin(isin: str, *, limiter: Optional[_net.RateLimiter] = None) -> list[dict]:
    """POST an ISIN to OpenFIGI → its list of FIGI records (or [] on failure). Fail-open."""
    if not isin or not isin.strip():
        return []
    if limiter is not None:
        limiter.wait()
    body = json.dumps([{"idType": "ID_ISIN", "idValue": isin.strip()}]).encode("utf-8")
    payload = _net.safe_json_retry(BASE, data=body, accept="application/json",
                                   extra_headers={"Content-Type": "application/json"})
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return []
    return payload[0].get("data") or []


def yf_symbol_for_isin(isin: str, *, limiter: Optional[_net.RateLimiter] = None,
                       map_fn=None) -> Optional[str]:
    """Resolve an ISIN → yfinance symbol (ticker + exchange suffix) using the PRIMARY listing venue, or
    None. ``map_fn`` injectable for tests (defaults to the live OpenFIGI call)."""
    recs = (map_fn or _map_isin)(isin, limiter=limiter) if map_fn else _map_isin(isin, limiter=limiter)
    for r in recs:                              # first record with a real (mappable) exchange = primary
        code = (r.get("exchCode") or "").strip()
        ticker = (r.get("ticker") or "").strip()
        if ticker and code in EXCH_SUFFIX:
            return ticker + EXCH_SUFFIX[code]
    return None
