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
        return resp.read().decode("utf-8", "replace")


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


def fetch_yearly(query: str, *, start_year: int, end_year: int, http_get=None,
                 sleep_s: float = RATE_LIMIT_SLEEP, retries: int = 2, backoff_s: float = 2.0):
    """Return ({year: filing_count}, {ticker: mentions}) for a query.

    One request per YEAR (reads total + a sample page of hits for tickers). Per-year (vs per-month)
    keeps EDGAR robust against the endpoint's intermittent HTTP 500/429 — ~9 requests per theme,
    each retried with backoff before failing open. Yearly granularity is fine for the secondary
    "filing keyword emergence" trend; the high-value output is the ticker linkage.
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
        url = f"{API}?{urllib.parse.urlencode({'q': query, 'startdt': startdt, 'enddt': enddt})}"
        total, tks, ok = 0, [], False
        for attempt in range(retries + 1):
            try:
                total, tks = parse_response(http_get(url))
                ok = True
                break
            except Exception as exc:  # transient 500/429 -> retry, then fail open
                if attempt < retries:
                    time.sleep(backoff_s * (attempt + 1))
                    continue
                log.info("edgar_fts %d failed: %s", year, exc)
        if not ok:
            continue
        if total:
            yearly[year] = total
        tickers.update(tks)
        if sleep_s:
            time.sleep(sleep_s)
    return yearly, dict(tickers)
