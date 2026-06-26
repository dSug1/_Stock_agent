"""Wikipedia pageviews ingestion — secondary mainstream-attention level (Features 1.4 context).

Uses the Wikimedia REST pageviews API (monthly granularity). Returns {YYYY-MM: views}.
Shown alongside the GDELT denominator in the report; not used as N_main itself.
"""

import json
import logging
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
       "en.wikipedia/all-access/all-agents/{article}/monthly/{start}/{end}")
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_pageviews(payload: str) -> dict:
    """Parse the REST response into {YYYY-MM: views}."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return {}
    out = {}
    for item in data.get("items", []):
        ts = str(item.get("timestamp", ""))  # 'YYYYMMDD00'
        if len(ts) >= 6:
            out[f"{ts[:4]}-{ts[4:6]}"] = int(item.get("views", 0) or 0)
    return out


def fetch(article: str, *, start: str = "2015010100", end: str = "2030010100",
          http_get=None) -> dict:
    """article: Wikipedia title (spaces ok). start/end as 'YYYYMMDDHH'."""
    http_get = http_get or _default_http_get
    enc = urllib.parse.quote(article.replace(" ", "_"), safe="")
    url = API.format(article=enc, start=start, end=end)
    try:
        payload = http_get(url)
    except Exception as exc:  # fail-open (404 for missing article is common)
        log.info("wikipedia fetch failed for %r: %s", article, exc)
        return {}
    return parse_pageviews(payload)
