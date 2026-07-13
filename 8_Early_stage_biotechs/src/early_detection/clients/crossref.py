"""Crossref REST API client (free, NO key, NO registration) — an author-resolution fallback source
(spec §3.1 support; decision D25).

Why: OpenAlex's 2026 credit quota makes its 10-credit author search the pipeline's budget chokepoint, and
it resolves only ~40% of founders. Crossref is free, keyless, and has **no credit quota**; its
``query.author`` search returns works with author affiliations + ``is-referenced-by-count`` — enough to
find a founder's foundational paper, whose DOI is then mapped to the OpenAlex citation graph via the cheap
(~1-credit) ``openalex.work_by_doi``. See [[reference_openalex_credit_quota]].

Security (repo SECURITY_AUDIT.md):
  - **No SSRF surface** — the host is a hard-coded HTTPS constant; only query VALUES vary, and they are
    percent-encoded. No user/config-supplied URL is ever fetched.
  - **JSON only** (no XML) → no entity-expansion attack surface.
  - **64 MiB capped reads** + ``Retry-After``/backoff retry, inherited from the shared ``_net`` client.
  - **Polite pool** via a syntactically-validated ``mailto`` (never splice arbitrary text into the query).
  - **Fail-open**: a dead/slow source degrades to ``None`` (retry) / ``[]`` (no-match), never a crash.
  - Response fields (titles / author names / affiliations) are UNTRUSTED DATA — only string-matched and
    stored in the signal payload; never executed, never used to build SQL (parameterized), never rendered
    unescaped (the HTML renderer escapes at its boundary).
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote

from . import _net

log = logging.getLogger(__name__)

BASE = "https://api.crossref.org"

# Crossref's polite pool (with mailto) allows ~3/s for list queries (``query.author`` is a list query);
# pace conservatively under that with a shared limiter. Retry in ``_net`` recovers the occasional 429/5xx.
_LIMITER = _net.RateLimiter(per_sec=2.0)
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+\.[\w.-]+$")


def _safe_mailto(mailto: str) -> str:
    """Append ``&mailto=`` only for a syntactically-valid email — defence against splicing arbitrary text
    (or extra query params) into the request via the mailto value."""
    m = (mailto or "").strip()
    return f"&mailto={quote(m, safe='')}" if _EMAIL_RE.match(m) else ""


def parse_author_works(payload: dict) -> list[dict]:
    """Crossref ``/works`` response → ``[{doi, title, year, cited_by_count,
    authors:[{given, family, affiliations:[str], orcid}]}]`` (most-cited first, as queried)."""
    out = []
    for it in (((payload or {}).get("message") or {}).get("items") or []):
        titles = it.get("title") or []
        year = None
        parts = (it.get("issued") or {}).get("date-parts") or []
        if parts and parts[0]:
            year = parts[0][0]
        authors = []
        for a in it.get("author") or []:
            orcid = a.get("ORCID")
            authors.append({
                "given": a.get("given") or "",
                "family": a.get("family") or "",
                "affiliations": [af.get("name") for af in (a.get("affiliation") or []) if af.get("name")],
                "orcid": orcid.rsplit("/", 1)[-1] if orcid else None,
            })
        out.append({
            "doi": it.get("DOI"),
            "title": titles[0] if titles else None,
            "year": year,
            "cited_by_count": it.get("is-referenced-by-count") or 0,
            "authors": authors,
        })
    return out


def search_author_works(name: str, *, mailto: str = "", rows: int = 25,
                        limiter: Optional[_net.RateLimiter] = None) -> Optional[list[dict]]:
    """Works by an author NAME (``query.author``), in Crossref's default **author-relevance** order.
    Returns **None on a FETCH FAILURE** (so the caller leaves the founder for retry, not a false no-match)
    vs ``[]`` on a genuine empty result. ``rows`` is int-coerced and bounded by Crossref.

    NB: do NOT sort by ``is-referenced-by-count`` here — ``query.author`` is a loose token search, and a
    global citation sort surfaces mega-cited consortium papers that merely CONTAIN a matching name token
    (e.g. "Stuart Pocock" for "Stuart Rich"), burying the real author. Relevance ranks true name matches
    first; the resolver then sorts the name-MATCHED subset by citations locally to pick the foundational."""
    if not name or not name.strip():
        return []
    url = (f"{BASE}/works?query.author={quote(name.strip(), safe='')}&rows={int(rows)}"
           f"&select=DOI,title,author,is-referenced-by-count,issued{_safe_mailto(mailto)}")
    payload = _net.safe_json_retry(url, limiter=limiter or _LIMITER, accept="application/json")
    if payload is None:
        return None
    return parse_author_works(payload)
