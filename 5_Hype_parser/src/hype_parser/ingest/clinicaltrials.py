"""ClinicalTrials.gov v2 ingestion — the Wave-2 trial-registration specialist signal.

A trial registration is specialist activity (and later feeds B5 "discrete NarrativeRealization").
We treat each study as a document timestamped by its **first-posted date** (when it became public),
so registrations contribute to N_spec by month. Joins the shared corpus with
`source_id='clinicaltrials'`. Pages via `nextPageToken`.
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from ..nethttp import capped_read

log = logging.getLogger(__name__)

API = "https://clinicaltrials.gov/api/v2/studies"
USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return capped_read(resp).decode("utf-8", "replace")


def parse_page(payload: str):
    """Return (docs, next_page_token) for one studies page."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return [], None
    docs = []
    for s in data.get("studies", []):
        ps = s.get("protocolSection", {})
        idm = ps.get("identificationModule", {})
        nct = idm.get("nctId")
        if not nct:
            continue
        status = ps.get("statusModule", {})
        date = (status.get("studyFirstPostDateStruct", {}).get("date")
                or status.get("startDateStruct", {}).get("date") or "")
        docs.append({
            "doc_id": f"ctgov:{nct}",
            "source_id": "clinicaltrials",
            "title": idm.get("briefTitle", "") or "",
            "abstract": ps.get("descriptionModule", {}).get("briefSummary", "") or "",
            "url": f"https://clinicaltrials.gov/study/{nct}",
            "published_at": date,
        })
    return docs, data.get("nextPageToken")


def fetch(query: str, *, max_results: int = 300, page_size: int = 100,
          http_get=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch up to max_results studies matching a free-text query (nextPageToken paging)."""
    http_get = http_get or _default_http_get
    out: dict[str, dict] = {}
    token = None
    while len(out) < max_results:
        params = {"query.term": query, "format": "json",
                  "pageSize": min(page_size, max_results - len(out))}
        if token:
            params["pageToken"] = token
        try:
            page, token = parse_page(http_get(f"{API}?{urllib.parse.urlencode(params)}"))
        except Exception as exc:  # fail-open
            log.warning("clinicaltrials fetch failed: %s", exc)
            break
        if not page:
            break
        for d in page:
            out.setdefault(d["doc_id"], d)
        if not token:
            break
        if sleep_s:
            time.sleep(sleep_s)
    return list(out.values())
