"""USPTO PatentsView ingestion — Wave-4 IP specialist source.

The current PatentsView API (search.patentsview.org) requires a free API key (X-Api-Key). If no key
is configured, this source is **skipped gracefully** (returns []). With a key, each patent is a
document (title + abstract) → corpus `source_id='patentsview'` → N_spec. Per-year `patent_date`
windows. Set the key via PATENTSVIEW_API_KEY in the repo-root .env (read by the orchestrator).
"""

import json
import logging
import time
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

API = "https://search.patentsview.org/api/v1/patent/"
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, api_key: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "X-Api-Key": api_key})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_patents(payload: str):
    """Return list of documents for one PatentsView response page."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    docs = []
    for p in data.get("patents", []) or []:
        pid = p.get("patent_id")
        if not pid:
            continue
        docs.append({
            "doc_id": f"uspto:{pid}",
            "source_id": "patentsview",
            "title": (p.get("patent_title") or "").strip(),
            "abstract": (p.get("patent_abstract") or "").strip(),
            "url": f"https://patents.google.com/patent/US{pid}",
            "published_at": (p.get("patent_date") or "")[:10],
        })
    return docs


def _query_url(query: str, year: int, size: int) -> str:
    q = {"_and": [
        {"_text_phrase": {"patent_abstract": query}},
        {"_gte": {"patent_date": f"{year}-01-01"}},
        {"_lte": {"patent_date": f"{year}-12-31"}},
    ]}
    f = ["patent_id", "patent_title", "patent_abstract", "patent_date"]
    o = {"size": size}
    return (f"{API}?q={urllib.parse.quote(json.dumps(q))}"
            f"&f={urllib.parse.quote(json.dumps(f))}&o={urllib.parse.quote(json.dumps(o))}")


def fetch_windows(query: str, *, start_year: int, end_year: int, api_key: str | None = None,
                  max_per_window: int = 100, http_get=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch patents whose abstract matches the query, per year. No key -> skipped (returns [])."""
    if not api_key:
        log.info("patentsview skipped (no PATENTSVIEW_API_KEY configured)")
        return []
    http_get = http_get or (lambda url: _default_http_get(url, api_key))
    out: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        try:
            docs = parse_patents(http_get(_query_url(query, year, max_per_window)))
        except Exception as exc:  # fail-open
            log.warning("patentsview fetch failed (%d): %s", year, exc)
            continue
        for d in docs:
            out.setdefault(d["doc_id"], d)
        if sleep_s:
            time.sleep(sleep_s)
    return list(out.values())
