"""PatentsView evidence — the IP signal (spec §6). Weights METHOD/PLATFORM patents over composition.

The current PatentsView Search API (search.patentsview.org) requires a free API key
(`PATENTSVIEW_API_KEY` in `.env`). Without one this client is **inert** (returns None, logs once) so
the rest of Stage 2 runs unaffected — patents are an optional enrichment, not a gate. A platform
company's tell is method/system/assay/screen patents (the data engine) rather than composition-of-
matter (a single molecule), so the parser buckets titles accordingly.

Pure parser (`parse_patents`) is unit-tested; the fetch is fail-open and key-gated.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Optional

from . import _net

log = logging.getLogger(__name__)

ENDPOINT = "https://search.patentsview.org/api/v1/patent/"

_METHOD_KW = ("method", "system", "platform", "assay", "screen", "process", "model",
              "predict", "analy", "detect", "sequenc", "imaging")
_COMPOSITION_KW = ("compound", "composition", "derivative", "salt", "formulation", "antibody",
                   "inhibitor")


def parse_patents(payload: dict) -> dict:
    pats = payload.get("patents") or []
    latest: Optional[str] = None
    method = composition = 0
    for p in pats:
        d = p.get("patent_date")
        if d and (latest is None or d > latest):
            latest = d
        title = (p.get("patent_title") or "").lower()
        if any(k in title for k in _METHOD_KW):
            method += 1
        elif any(k in title for k in _COMPOSITION_KW):
            composition += 1
    return {
        "patent_count": payload.get("total_hits", len(pats)),
        "method_platform_titles": method,
        "composition_titles": composition,
        "latest_date": latest,
    }


def fetch(company_name: str, *, limiter: Optional[_net.RateLimiter] = None,
          api_key: Optional[str] = None) -> Optional[tuple[dict, str]]:
    """Return (summary, cursor) or None. Inert (None) without an API key."""
    if not api_key:
        log.info("PatentsView skipped — set PATENTSVIEW_API_KEY in .env to enable patent evidence")
        return None
    q = json.dumps({"_text_phrase": {"assignees.assignee_organization":
                                     _net.clean_name(company_name)}})
    params = {"q": q, "f": json.dumps(["patent_id", "patent_title", "patent_date"]),
              "o": json.dumps({"size": 100})}
    payload = _net.safe_json(f"{ENDPOINT}?{urllib.parse.urlencode(params)}",
                             limiter=limiter, extra_headers={"X-Api-Key": api_key})
    if not payload:
        return None
    summary = parse_patents(payload)
    if not summary.get("patent_count"):
        return None
    cursor = summary.get("latest_date") or str(summary["patent_count"])
    return summary, cursor
