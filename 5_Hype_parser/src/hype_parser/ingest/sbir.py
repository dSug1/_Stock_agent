"""SBIR.gov ingestion — Wave-4 public-capital specialist source (small-business R&D awards).

Keyless GET API. Each award is a document (title + abstract) → corpus `source_id='sbir'` → N_spec.
Cross-sector startup funding signal. Per-year paging via the `year` param.
"""

import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

API = "https://api.www.sbir.gov/public/api/awards"
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_awards(payload: str):
    """Return list of documents. SBIR returns a bare JSON array (or {results:[...]})."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    rows = data if isinstance(data, list) else data.get("results", [])
    docs = []
    for a in rows:
        key = a.get("agency_tracking_number") or a.get("contract") or a.get("award_link")
        if not key:
            continue
        date = a.get("proposal_award_date") or a.get("contract_end_date") or ""
        if not date and a.get("award_year"):
            date = f"{a['award_year']}-01-01"
        docs.append({
            "doc_id": f"sbir:{key}",
            "source_id": "sbir",
            "title": (a.get("award_title") or "").strip(),
            "abstract": (a.get("abstract") or "").strip(),
            "url": a.get("award_link") or "https://www.sbir.gov/awards",
            "published_at": str(date)[:10],
        })
    return docs


def fetch_windows(query: str, *, start_year: int, end_year: int, max_per_window: int = 100,
                  page_size: int = 100, http_get=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch awards matching a keyword, per year, de-duplicated."""
    http_get = http_get or _default_http_get
    out: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        start = 0
        while start < max_per_window:
            params = urllib.parse.urlencode({
                "keyword": query, "year": year,
                "rows": min(page_size, max_per_window - start), "start": start})
            try:
                docs = parse_awards(http_get(f"{API}?{params}"))
            except Exception as exc:  # fail-open
                log.warning("sbir fetch failed (%d, start %d): %s", year, start, exc)
                break
            if not docs:
                break
            for d in docs:
                out.setdefault(d["doc_id"], d)
            if len(docs) < page_size:
                break
            start += page_size
            if sleep_s:
                time.sleep(sleep_s)
    return list(out.values())
