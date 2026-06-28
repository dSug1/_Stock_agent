"""OpenAlex evidence — the primary science signal (spec §6, free, no key).

Finds the company as an OpenAlex *institution of type company* and summarizes its research footprint:
publication volume, citation impact (h-index, mean citedness), and top research concepts. This is the
strongest, most global Acrivon-pattern signal (peer-reviewed output, English worldwide).

`filter=type:company` plus a name-token guard avoids matching a same-named university. Pure parser
(`parse_institution`) is unit-tested; the fetch is fail-open. Uses the polite pool via `mailto`.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Optional

from . import _net

log = logging.getLogger(__name__)

ENDPOINT = "https://api.openalex.org/institutions"
AUTHORS_ENDPOINT = "https://api.openalex.org/authors"


def _name_matches(target: str, found: str) -> bool:
    """Require the company's first distinctive word to appear in the institution name."""
    toks = [t for t in _net.clean_name(target).lower().split() if len(t) > 2]
    found_l = (found or "").lower()
    return bool(toks) and toks[0] in found_l


def parse_institution(payload: dict, target_name: str) -> Optional[dict]:
    results = payload.get("results") or []
    if not results:
        return None
    inst = results[0]
    if not _name_matches(target_name, inst.get("display_name", "")):
        return None
    ss = inst.get("summary_stats") or {}
    # OpenAlex deprecated x_concepts in favour of topics — prefer whichever is populated.
    concepts = [c.get("display_name") for c in (inst.get("x_concepts") or []) if c.get("display_name")]
    if not concepts:
        concepts = [t.get("display_name") for t in (inst.get("topics") or []) if t.get("display_name")]
    return {
        "institution": inst.get("display_name"),
        "openalex_id": inst.get("id"),
        "works_count": inst.get("works_count"),
        "cited_by_count": inst.get("cited_by_count"),
        "h_index": ss.get("h_index"),
        "i10_index": ss.get("i10_index"),
        "mean_citedness_2y": ss.get("2yr_mean_citedness"),
        "top_concepts": concepts[:8],
        "updated_date": inst.get("updated_date"),
    }


def fetch(company_name: str, *, limiter: Optional[_net.RateLimiter] = None,
          mailto: Optional[str] = None) -> Optional[tuple[dict, str]]:
    """Return (summary, cursor) or None. cursor = institution updated_date (change marker)."""
    params = {"search": _net.clean_name(company_name), "filter": "type:company", "per_page": "1"}
    if mailto:
        params["mailto"] = mailto
    payload = _net.safe_json(f"{ENDPOINT}?{urllib.parse.urlencode(params)}", limiter=limiter)
    if not payload:
        return None
    summary = parse_institution(payload, company_name)
    if not summary or not summary.get("works_count"):
        return None
    cursor = summary.get("updated_date") or str(summary.get("works_count"))
    return summary, cursor


# ── top authors (scientific pedigree, spec §5.6.1) ──────────────────────────

def parse_authors(payload: dict) -> list[dict]:
    """authors response → [{name, h_index, works_count}] (the company's top-publishing scientists)."""
    out: list[dict] = []
    for a in payload.get("results") or []:
        ss = a.get("summary_stats") or {}
        if a.get("display_name"):
            out.append({"name": a["display_name"], "h_index": ss.get("h_index"),
                        "works_count": a.get("works_count")})
    return out


def _institution_id(company_name: str, limiter, mailto) -> Optional[str]:
    params = {"search": _net.clean_name(company_name), "filter": "type:company", "per_page": "1"}
    if mailto:
        params["mailto"] = mailto
    payload = _net.safe_json(f"{ENDPOINT}?{urllib.parse.urlencode(params)}", limiter=limiter)
    results = (payload or {}).get("results") or []
    if results and _name_matches(company_name, results[0].get("display_name", "")):
        return (results[0].get("id") or "").rsplit("/", 1)[-1] or None   # e.g. I4210128074
    return None


def fetch_top_authors(company_name: str, *, limiter: Optional[_net.RateLimiter] = None,
                      mailto: Optional[str] = None, per_page: int = 10) -> Optional[list[dict]]:
    """The company's highest-h-index OpenAlex authors (its scientific founders/SAB proxy). Fail-open."""
    iid = _institution_id(company_name, limiter, mailto)
    if not iid:
        return None
    params = {"filter": f"affiliations.institution.id:{iid}",
              "sort": "summary_stats.h_index:desc", "per_page": str(per_page)}
    if mailto:
        params["mailto"] = mailto
    payload = _net.safe_json(f"{AUTHORS_ENDPOINT}?{urllib.parse.urlencode(params)}", limiter=limiter)
    return parse_authors(payload) if payload else None
