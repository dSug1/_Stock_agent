"""NSF Awards ingestion — Wave-4 public-capital specialist source (research funding).

Keyless GET API (research.gov). Each award is a document (title + abstract) → corpus
`source_id='nsf'` → N_spec via membership. Good for science/tech themes. Per-year date windows.
"""

import json
import logging
import time
import urllib.parse
import urllib.request
from ..nethttp import capped_read

log = logging.getLogger(__name__)

API = "https://www.research.gov/awardapi-service/v1/awards.json"
USER_AGENT = "HypeParser/0.1 (research; local)"
FIELDS = "id,title,abstractText,date,fundsObligatedAmt"


def _default_http_get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return capped_read(resp).decode("utf-8", "replace")


def _iso(date_str: str) -> str:
    # NSF dates are mm/dd/yyyy
    s = (date_str or "").strip()
    if len(s) == 10 and s[2] == "/" and s[5] == "/":
        return f"{s[6:10]}-{s[0:2]}-{s[3:5]}"
    return s[:10]


def parse_awards(payload: str):
    """Return list of documents for one NSF awards response page."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    docs = []
    for a in (data.get("response", {}) or {}).get("award", []):
        aid = a.get("id")
        if not aid:
            continue
        docs.append({
            "doc_id": f"nsf:{aid}",
            "source_id": "nsf",
            "title": (a.get("title") or "").strip(),
            "abstract": (a.get("abstractText") or "").strip(),
            "url": f"https://www.nsf.gov/awardsearch/showAward?AWD_ID={aid}",
            "published_at": _iso(a.get("date", "")),
        })
    return docs


def fetch_windows(query: str, *, start_year: int, end_year: int, max_per_window: int = 100,
                  page_size: int = 25, http_get=None, sleep_s: float = 0.0) -> list[dict]:
    """Fetch awards matching a keyword, per-year (dateStart/dateEnd), de-duplicated."""
    http_get = http_get or _default_http_get
    out: dict[str, dict] = {}
    for year in range(start_year, end_year + 1):
        offset = 1  # NSF offset is 1-based
        while offset <= max_per_window:
            params = urllib.parse.urlencode({
                "keyword": query, "printFields": FIELDS,
                "dateStart": f"01/01/{year}", "dateEnd": f"12/31/{year}",
                "offset": offset, "rpp": page_size})
            try:
                docs = parse_awards(http_get(f"{API}?{params}"))
            except Exception as exc:  # fail-open
                log.warning("nsf fetch failed (%d, offset %d): %s", year, offset, exc)
                break
            if not docs:
                break
            for d in docs:
                out.setdefault(d["doc_id"], d)
            if len(docs) < page_size:
                break
            offset += page_size
            if sleep_s:
                time.sleep(sleep_s)
    return list(out.values())
