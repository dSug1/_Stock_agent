"""GDELT ingestion — the Wave-1 mainstream denominator (news article volume).

Uses the GDELT DOC 2.0 API in TimelineVolRaw mode (raw article counts over time, ~2017+).
Returns a {month: count} mapping. Parsing split from fetching for tests.
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from ..nethttp import capped_read
from collections import defaultdict

log = logging.getLogger(__name__)

API = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return capped_read(resp).decode("utf-8", "replace")


def parse_timeline(payload: str) -> dict:
    """Parse a TimelineVolRaw JSON response into {YYYY-MM: article_count}."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return {}
    monthly = defaultdict(float)
    for series in data.get("timeline", []):
        for pt in series.get("data", []):
            ds = str(pt.get("date", ""))
            # GDELT date is 'YYYYMMDDhhmmss' or 'YYYY-MM-DDT...'; normalize to YYYY-MM
            if len(ds) >= 6 and ds[:6].isdigit():
                month = f"{ds[:4]}-{ds[4:6]}"
            elif len(ds) >= 7 and ds[4] == "-":
                month = ds[:7]
            else:
                continue
            monthly[month] += float(pt.get("value", 0) or 0)
    return {m: int(round(v)) for m, v in monthly.items()}


def fetch(query: str, *, start: str | None = None, end: str | None = None,
          http_get=None, retries: int = 2, backoff_s: float = 5.0) -> dict:
    """Fetch monthly article counts for a query. Dates as 'YYYYMMDDhhmmss' (GDELT format).

    GDELT rate-limits aggressively (HTTP 429); retry with backoff, then fail open."""
    http_get = http_get or _default_http_get
    params = {
        "query": query,
        "mode": "timelinevolraw",
        "format": "json",
        "timelinesmooth": "0",
    }
    if start:
        params["startdatetime"] = start
    if end:
        params["enddatetime"] = end
    url = f"{API}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            return parse_timeline(http_get(url))
        except Exception as exc:  # includes HTTP 429
            if attempt < retries:
                log.info("gdelt fetch retry %d (%s)", attempt + 1, exc)
                time.sleep(backoff_s * (attempt + 1))
                continue
            log.warning("gdelt fetch failed after %d tries: %s", retries + 1, exc)
            return {}
