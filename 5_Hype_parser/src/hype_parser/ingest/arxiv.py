"""arXiv ingestion — the Wave-1 specialist numerator (deep queryable history, rich abstracts).

Uses the public arXiv Atom API (export.arxiv.org/api/query). Pages politely (arXiv asks ~3s
between calls). Parsing is split from fetching so tests can feed canned Atom XML.
"""

import logging
import time
import urllib.parse
import urllib.request
from ..nethttp import capped_read

import feedparser

log = logging.getLogger(__name__)

API = "http://export.arxiv.org/api/query"
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return capped_read(resp).decode("utf-8", "replace")


def parse_atom(xml: str) -> list[dict]:
    """Parse an arXiv Atom response into documents."""
    feed = feedparser.parse(xml)
    docs = []
    for e in feed.entries:
        raw_id = e.get("id", "")
        # canonical id like http://arxiv.org/abs/2401.01234v1 -> 2401.01234
        doc_id = raw_id.rsplit("/abs/", 1)[-1].split("v")[0] if "/abs/" in raw_id else raw_id
        published = e.get("published", "") or e.get("updated", "")
        docs.append({
            "doc_id": f"arxiv:{doc_id}" if doc_id else raw_id,
            "source_id": "arxiv",
            "title": (e.get("title", "") or "").replace("\n", " ").strip(),
            "abstract": (e.get("summary", "") or "").replace("\n", " ").strip(),
            "url": raw_id,
            "published_at": published,  # ISO-8601 from arXiv
        })
    return docs


def fetch_windows(query: str, *, start_year: int, end_year: int, max_per_window: int = 120,
                  page_size: int = 100, http_get=None, sleep_s: float = 3.0) -> list[dict]:
    """Fetch a query across per-year submittedDate windows, so the specialist series has
    monthly history (the newest-N approach collapses a hot theme into one month). Dedup'd."""
    out: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        windowed = f"({query}) AND submittedDate:[{year}01010000 TO {year}12312359]"
        for d in fetch(windowed, max_results=max_per_window, page_size=page_size,
                       http_get=http_get, sleep_s=sleep_s):
            out.setdefault(d["doc_id"], d)
        if sleep_s:
            time.sleep(sleep_s)
    return list(out.values())


def fetch(query: str, *, max_results: int = 400, page_size: int = 100,
          http_get=None, sleep_s: float = 3.0) -> list[dict]:
    """Fetch up to max_results documents for an arXiv query (newest first), de-duplicated."""
    http_get = http_get or _default_http_get
    out: dict[str, dict] = {}
    start = 0
    while len(out) < max_results:
        params = urllib.parse.urlencode({
            "search_query": query,
            "start": start,
            "max_results": min(page_size, max_results - len(out)),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        })
        url = f"{API}?{params}"
        try:
            xml = http_get(url)
        except Exception as exc:  # fail-open: return what we have
            log.warning("arxiv fetch failed at start=%d: %s", start, exc)
            break
        page = parse_atom(xml)
        if not page:
            break
        for d in page:
            out.setdefault(d["doc_id"], d)
        if len(page) < page_size:
            break
        start += page_size
        if len(out) < max_results and sleep_s:
            time.sleep(sleep_s)
    return list(out.values())
