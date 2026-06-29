"""SEC EDGAR submissions — earliest-filing-date fallback for ``ipo_date`` (spec D2 phase 2 / D12).

When yfinance has no first-trade date for a US filer, the company lands in Tier 0 (untiered). The SEC
submissions API (`data.sec.gov/submissions/CIK##########.json`) lists a filer's history; its **oldest
filing date** is a recall-safe *proxy* for "how long it's been public" — typically the S-1/registration
for a recently-IPO'd small biotech. Used only to fill a MISSING ipo_date (never to override yfinance).

Pure parsers (`_earliest_filing_date`, `_invert_cik_map`) are unit-tested; fetches are fail-open
(→ None) and require the SEC `User-Agent` (loaded from .env by ``_net``). US filers only — a non-US
ticker has no CIK and simply returns None.
"""

from __future__ import annotations

import logging
from typing import Optional

from . import _net
from .sec_sic import TICKERS_EXCHANGE, parse_cik_exchange

log = logging.getLogger(__name__)

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def _invert_cik_map(cik_map: dict) -> dict[str, int]:
    """{cik: (ticker, exchange, name)} → {TICKER: cik} (upper-cased)."""
    out: dict[str, int] = {}
    for cik, info in (cik_map or {}).items():
        t = (info[0] if info else "").strip().upper()
        if t and t not in out:
            out[t] = cik
    return out


def _earliest_filing_date(payload: dict) -> Optional[str]:
    """Oldest filing date (ISO 'YYYY-MM-DD') from a submissions payload, or None.

    Parses ``filings.recent.filingDate`` (covers a filer's full history when it has < ~1000 filings,
    which holds for essentially every small/recently-public biotech; older filings live in
    ``filings.files`` and are intentionally not fetched here — this is a proxy, not an audit)."""
    dates = (((payload or {}).get("filings") or {}).get("recent") or {}).get("filingDate") or []
    valid = [d for d in dates if isinstance(d, str) and len(d) == 10 and d[4] == "-"]
    return min(valid) if valid else None


def build_ticker_cik_map(*, limiter: Optional[_net.RateLimiter] = None) -> dict[str, int]:
    """Fetch the SEC cik↔ticker file once and return {TICKER: cik}. Fail-open (→ {})."""
    payload = _net.safe_json(TICKERS_EXCHANGE, limiter=limiter)
    return _invert_cik_map(parse_cik_exchange(payload)) if payload else {}


def first_filing_date(ticker: str, *, ticker_cik_map: dict[str, int],
                      limiter: Optional[_net.RateLimiter] = None) -> Optional[str]:
    """Earliest SEC filing date for ``ticker`` (the ipo_date fallback), or None. Fail-open.

    ``ticker_cik_map`` (from ``build_ticker_cik_map``) is passed in so the big cik↔ticker file is
    fetched once per run, not per company."""
    cik = ticker_cik_map.get((ticker or "").strip().upper())
    if cik is None:
        return None
    payload = _net.safe_json(SUBMISSIONS.format(cik=int(cik)), limiter=limiter)
    return _earliest_filing_date(payload) if payload else None
