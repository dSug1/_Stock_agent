"""Europe PMC ingestion — the Wave-2 biomedical specialist numerator.

Europe PMC's search API is the keyword-searchable route to **bioRxiv/medRxiv preprints + PubMed**
(the raw bioRxiv API is date-dump-only, not searchable — see decisions D9). `resultType=core`
returns abstracts; we page via `cursorMark` and slice by `FIRST_PDATE` per year so the specialist
series has monthly history (same pattern as arXiv). Documents join the shared corpus with
`source_id='europepmc'`, so they extend N_spec exactly like arXiv docs.
"""

import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _doc_url(r: dict) -> str:
    doi = r.get("doi")
    if doi:
        return f"https://doi.org/{doi}"
    src, pid = r.get("source", ""), r.get("id", "")
    return f"https://europepmc.org/article/{src}/{pid}" if src and pid else ""


def parse_page(payload: str):
    """Return (docs, next_cursor_mark) for one Europe PMC search page."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return [], None
    docs = []
    for r in data.get("resultList", {}).get("result", []):
        src, pid = r.get("source", ""), r.get("id", "")
        if not pid:
            continue
        date = r.get("firstPublicationDate")
        if not date and r.get("pubYear"):
            date = f"{r['pubYear']}-01-01"
        docs.append({
            "doc_id": f"epmc:{src}:{pid}",
            "source_id": "europepmc",
            "title": (r.get("title", "") or "").strip(),
            "abstract": (r.get("abstractText", "") or "").strip(),
            "url": _doc_url(r),
            "published_at": date or "",
        })
    return docs, data.get("nextCursorMark")


def fetch(query: str, *, max_results: int = 400, page_size: int = 100,
          http_get=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch up to max_results results for a Europe PMC query (cursorMark paging), de-duplicated."""
    http_get = http_get or _default_http_get
    out: dict[str, dict] = {}
    cursor = "*"
    while len(out) < max_results:
        params = urllib.parse.urlencode({
            "query": query, "format": "json", "resultType": "core",
            "pageSize": min(page_size, max_results - len(out)), "cursorMark": cursor,
        })
        try:
            page, next_cursor = parse_page(http_get(f"{API}?{params}"))
        except Exception as exc:  # fail-open
            log.warning("europepmc fetch failed: %s", exc)
            break
        if not page:
            break
        for d in page:
            out.setdefault(d["doc_id"], d)
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        if sleep_s:
            time.sleep(sleep_s)
    return list(out.values())


def fetch_windows(query: str, *, start_year: int, end_year: int, max_per_window: int = 150,
                  page_size: int = 100, http_get=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch a query across per-year FIRST_PDATE windows so the series has monthly history."""
    out: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        windowed = f"({query}) AND (FIRST_PDATE:[{year}-01-01 TO {year}-12-31])"
        for d in fetch(windowed, max_results=max_per_window, page_size=page_size,
                       http_get=http_get, sleep_s=sleep_s):
            out.setdefault(d["doc_id"], d)
    return list(out.values())
