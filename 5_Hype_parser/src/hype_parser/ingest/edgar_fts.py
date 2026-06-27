"""EDGAR full-text search ingestion — Wave-3 filing-keyword emergence + ticker linkage.

The SEC EDGAR full-text search API (https://efts.sec.gov/LATEST/search-index) keyword-matches
filing text server-side. We use it two ways, per decision D10:
  1. **Monthly filing counts** (`hits.total.value` per month) -> a "how many filings mention this
     theme" series, kept *separate* from N_spec (it is a corporate/capital signal, not specialist
     literature). Stored in `theme_series.edgar_filings`.
  2. **Ticker linkage** -> tickers parsed from each hit's `display_names` ("Company (TICK) (CIK ...)")
     -> `theme_tickers`. This is the first bridge toward constituent expansion (Stage 3 later).

SEC etiquette: a descriptive User-Agent + <=10 req/s (sleep ~0.11s). This is a minimal local client
for the FTS endpoint; heavier EDGAR data (companyfacts/Form 4) would reuse the shared repo EDGAR
client in a later module (D3/D10).
"""

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from ..nethttp import capped_read
from collections import Counter
from datetime import datetime, timezone

log = logging.getLogger(__name__)

API = "https://efts.sec.gov/LATEST/search-index"
USER_AGENT = "HypeParser/0.1 research (admin@hypeparser.local)"
RATE_LIMIT_SLEEP = 0.12  # SEC: <= 10 req/s
# "Apple Inc. (AAPL) (CIK 0000320193)" -> AAPL
_TICKER_RE = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,6})\)\s*\(CIK", re.IGNORECASE)


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return capped_read(resp).decode("utf-8", "replace")


def parse_response(payload: str):
    """Return (total_hits, [tickers from this page's display_names])."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return 0, []
    hits = data.get("hits", {})
    total = hits.get("total", {}).get("value", 0) or 0
    tickers = []
    for h in hits.get("hits", []):
        for dn in h.get("_source", {}).get("display_names", []):
            m = _TICKER_RE.search(dn or "")
            if m:
                tickers.append(m.group(1).upper())
    return int(total), tickers


_PAGE_SIZE = 10   # EDGAR FTS returns ~10 hits per page; `from` offsets paginate.


def _url(query: str, startdt: str, enddt: str, frm: int = 0) -> str:
    params = {"q": query, "startdt": startdt, "enddt": enddt}
    if frm:
        params["from"] = frm
    return f"{API}?{urllib.parse.urlencode(params)}"


def fetch_yearly(query: str, *, start_year: int, end_year: int, http_get=None,
                 sleep_s: float = RATE_LIMIT_SLEEP, retries: int = 2, backoff_s: float = 2.0,
                 ticker_pages: int = 1):
    """Return ({year: filing_count}, {ticker: mentions}) for a query.

    Per YEAR: page 0 gives the total filing count + first page of tickers; `ticker_pages` > 1
    paginates additional hit pages (via `from`) to **broaden the constituent harvest** (Stage C /
    D20) — each theme then links many more filers, not just the top ~10. Per-year (vs per-month)
    keeps EDGAR robust against intermittent 500/429; each page-0 request is retried with backoff
    before failing open. Extra pages are best-effort (a failed/empty page just stops paging that year).
    """
    http_get = http_get or _default_http_get
    now = datetime.now(timezone.utc)
    yearly: dict[int, int] = {}
    tickers: Counter = Counter()
    for year in range(start_year, end_year + 1):
        if year > now.year:
            break
        startdt = f"{year}-01-01"
        enddt = f"{year}-12-31" if year < now.year else now.strftime("%Y-%m-%d")
        total, tks, ok = 0, [], False
        for attempt in range(retries + 1):
            try:
                total, tks = parse_response(http_get(_url(query, startdt, enddt)))
                ok = True
                break
            except Exception as exc:  # transient 500/429 -> retry, then fail open
                if attempt < retries:
                    time.sleep(backoff_s * (attempt + 1))
                    continue
                log.info("edgar_fts %d failed: %s", year, exc)
        if not ok:
            continue
        page_tickers = list(tks)
        # Additional pages broaden the constituent set (best-effort; stop on empty/error).
        for pg in range(1, max(1, ticker_pages)):
            if pg * _PAGE_SIZE >= total:        # no more hits to page through
                break
            if sleep_s:
                time.sleep(sleep_s)
            try:
                _, more = parse_response(http_get(_url(query, startdt, enddt, pg * _PAGE_SIZE)))
            except Exception as exc:
                log.info("edgar_fts %d page %d failed: %s", year, pg, exc)
                break
            if not more:
                break
            page_tickers += more
        if total:
            yearly[year] = total
        tickers.update(page_tickers)
        if sleep_s:
            time.sleep(sleep_s)
    return yearly, dict(tickers)
