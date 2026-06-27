"""NIH RePORTER ingestion — Wave-4 public-capital specialist source (biomedical funding).

POST API (no key). Each funded project is a document (title + abstract), so it joins the corpus
with `source_id='nih'` and feeds N_spec via membership — strong for biomed themes (crispr, mKRAS).
Award amounts are captured for a later B2 "funding into theme" signal but the engine only uses doc
counts. Per-fiscal-year paging for history. Parsing split from fetching for tests.
"""

import json
import logging
import time
import urllib.request
from ..nethttp import capped_read

log = logging.getLogger(__name__)

API = "https://api.reporter.nih.gov/v2/projects/search"
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_post(url: str, body: dict, timeout: int = 30) -> str:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return capped_read(resp).decode("utf-8", "replace")


def parse_results(payload: str):
    """Return (docs, total) for one NIH RePORTER response page."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return [], 0
    docs = []
    for r in data.get("results", []):
        pid = r.get("appl_id") or r.get("project_num") or r.get("core_project_num")
        if not pid:
            continue
        date = r.get("award_notice_date") or r.get("project_start_date") or ""
        if not date and r.get("fiscal_year"):
            date = f"{r['fiscal_year']}-01-01"
        docs.append({
            "doc_id": f"nih:{pid}",
            "source_id": "nih",
            "title": (r.get("project_title") or "").strip(),
            "abstract": (r.get("abstract_text") or "").strip(),
            "url": f"https://reporter.nih.gov/project-details/{pid}",
            "published_at": (date or "")[:10],
        })
    total = (data.get("meta") or {}).get("total", 0) or 0
    return docs, int(total)


def fetch_windows(query: str, *, start_year: int, end_year: int, max_per_window: int = 100,
                  page_size: int = 100, http_post=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch funded projects matching a text query, per fiscal year, de-duplicated."""
    http_post = http_post or _default_http_post
    out: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        offset = 0
        while True:
            body = {
                "criteria": {
                    "advanced_text_search": {
                        "operator": "and", "search_field": "projecttitle,abstracttext,terms",
                        "search_text": query},
                    "fiscal_years": [year]},
                "include_fields": ["ApplId", "ProjectTitle", "AbstractText", "FiscalYear",
                                   "AwardNoticeDate", "ProjectStartDate", "ProjectNum",
                                   "AwardAmount"],
                "offset": offset, "limit": min(page_size, max_per_window - offset),
            }
            try:
                docs, _ = parse_results(http_post(API, body))
            except Exception as exc:  # fail-open
                log.warning("nih fetch failed (%d, offset %d): %s", year, offset, exc)
                break
            if not docs:
                break
            for d in docs:
                out.setdefault(d["doc_id"], d)
            offset += len(docs)
            if len(docs) < page_size or offset >= max_per_window:
                break
            if sleep_s:
                time.sleep(sleep_s)
    return list(out.values())
