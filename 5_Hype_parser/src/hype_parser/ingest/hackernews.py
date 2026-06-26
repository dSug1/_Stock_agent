"""Hacker News ingestion — Wave-3 builder-mindshare specialist source.

Uses the HN Algolia search API. HN stories are a leading indicator for software/infra themes
(the ROKU/NET/dev-tools style). Each story is a document with rich text, so it joins the corpus
with `source_id='hackernews'` and goes through the normal embedding membership path (like arXiv).
Per-year `created_at_i` windows give monthly history (Algolia caps paging at 1000/window).
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

log = logging.getLogger(__name__)

API = "http://hn.algolia.com/api/v1/search"
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _year_epoch(year: int) -> int:
    return int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())


def parse_hits(payload: str):
    """Return (docs, nb_pages) for one Algolia search page."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return [], 0
    docs = []
    for h in data.get("hits", []):
        oid = h.get("objectID")
        if not oid:
            continue
        title = h.get("title") or h.get("story_title") or ""
        text = h.get("story_text") or h.get("comment_text") or ""
        docs.append({
            "doc_id": f"hn:{oid}",
            "source_id": "hackernews",
            "title": title,
            "abstract": text or title,
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
            "published_at": h.get("created_at", ""),  # ISO-8601
        })
    return docs, int(data.get("nbPages", 0))


def fetch(query: str, *, numeric_filter: str = "", max_results: int = 200,
          page_size: int = 100, http_get=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch HN stories for a query (optionally constrained by a numericFilters string)."""
    http_get = http_get or _default_http_get
    out: dict[str, dict] = {}
    page = 0
    while len(out) < max_results:
        params = {"query": query, "tags": "story",
                  "hitsPerPage": min(page_size, max_results - len(out)), "page": page}
        if numeric_filter:
            params["numericFilters"] = numeric_filter
        try:
            hits, nb_pages = parse_hits(http_get(f"{API}?{urllib.parse.urlencode(params)}"))
        except Exception as exc:  # fail-open
            log.warning("hackernews fetch failed: %s", exc)
            break
        if not hits:
            break
        for d in hits:
            out.setdefault(d["doc_id"], d)
        page += 1
        if page >= nb_pages:
            break
        if sleep_s:
            time.sleep(sleep_s)
    return list(out.values())


def fetch_windows(query: str, *, start_year: int, end_year: int, max_per_window: int = 200,
                  page_size: int = 100, http_get=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch per-year created_at_i windows so the series has monthly history."""
    out: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        nf = f"created_at_i>={_year_epoch(year)},created_at_i<{_year_epoch(year + 1)}"
        for d in fetch(query, numeric_filter=nf, max_results=max_per_window,
                       page_size=page_size, http_get=http_get, sleep_s=sleep_s):
            out.setdefault(d["doc_id"], d)
    return list(out.values())
