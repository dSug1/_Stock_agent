"""Free author-resolution fallback (Crossref + optional ORCID) — relieves the OpenAlex 10-credit
author-search chokepoint (spec §3.1 support; decision D25).

When OpenAlex's author search fails to resolve a founder (the ~60% miss, or when its credit budget is
best spent elsewhere), this resolves the founder to their **OpenAlex author id** WITHOUT a second
10-credit search:

  1. **Crossref** ``query.author`` (free, keyless, no quota) → the founder's works, most-cited first.
  2. Keep works whose author list contains the founder by NAME (surname + given-token match) — the same
     ``openalex._name_match`` discipline used by the primary path.
  3. **(optional) ORCID** — if creds are set, keep only DOIs the founder actually authored per their ORCID
     record (a persistent-identity precision filter). Absent ORCID, step 2 stands.
  4. For each candidate DOI (most-cited first, capped), map DOI → OpenAlex work (~1 credit) and find the
     author whose NAME **and institution hint** match — using OpenAlex's reliable affiliation data as the
     precision anchor. First match wins; its OpenAlex author id is returned.

The existing cheap OpenAlex path (recent pubs + citation pull) then continues unchanged on that author id.
Net cost ≈ 1–3 OpenAlex credits vs the 10-credit search, and it recovers founders the search missed.

**Precision-first** (a wrong author → wrong citations → a false signal): a surname match is always
required, and when an institution hint is known an OpenAlex-affiliation match is required too; ambiguous →
no resolution (recall-conservative, like the rest of the module). **Fail-open**: a transient fetch failure
returns ``fetch_failed=True`` so the caller leaves the founder UNSTAMPED for retry (never a false no-match).

Security: this module orchestrates the hardened ``crossref`` / ``orcid`` / ``openalex`` clients (hard-coded
hosts, encoded values, DOI-validated before use, capped reads, env-only ORCID secrets). It fetches no
user-supplied URL and executes no fetched content — response fields are DATA (matched + stored only).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from ..clients import crossref, openalex, orcid

log = logging.getLogger(__name__)


@dataclass
class FallbackResult:
    author_id: Optional[str] = None        # resolved OpenAlex author id, or None
    foundational_doi: Optional[str] = None
    source: str = "none"                    # "crossref" | "crossref+orcid" | "none"
    fetch_failed: bool = False              # transient (retry, don't stamp no-match)


def _usable_hints(hints: list[str]) -> list[str]:
    bad = ("", "unknown", "none identified", "none", "na")
    return [h for h in hints if openalex._norm(h) not in bad]


def _hint_ok(usable_hints: list[str], institutions: list[str]) -> bool:
    """Does any usable institution hint match any of the author's (career) institutions? (containment,
    normalized both ways — the same test ``openalex.pick_author`` applies to its author candidates)."""
    return any(any(openalex._norm(h) in openalex._norm(i) or openalex._norm(i) in openalex._norm(h)
                   for i in institutions)
               for h in usable_hints)


def resolve(name: str, hints: list[str], *, mailto: str = "", max_candidates: int = 3,
            orcid_dois: Optional[list[str]] = None,
            cr_search: Callable = crossref.search_author_works,
            oa_work_by_doi: Callable = openalex.work_by_doi,
            oa_author_by_id: Callable = openalex.author_by_id) -> FallbackResult:
    """Pure orchestration (clients injectable for offline tests). See the module docstring for the flow."""
    if not name or not name.strip():
        return FallbackResult(source="none")

    cr = cr_search(name, mailto=mailto)
    if cr is None:                                   # Crossref fetch failed → retry later
        return FallbackResult(fetch_failed=True)

    nm = openalex._norm(name)
    candidates = [w for w in cr if w.get("doi") and any(
        openalex._name_match(nm, openalex._norm(f"{a.get('given', '')} {a.get('family', '')}"))
        for a in (w.get("authors") or []))]
    # Crossref returns author-RELEVANCE order (see crossref.search_author_works); sort the name-matched
    # subset by citations locally so the founder's most-cited paper (the best identity anchor) is mapped
    # to OpenAlex first.
    candidates.sort(key=lambda w: w.get("cited_by_count") or 0, reverse=True)

    used_orcid = False
    if orcid_dois:
        oset = {d.lower() for d in orcid_dois if d}
        confirmed = [w for w in candidates if (w.get("doi") or "").lower() in oset]
        if confirmed:                                # only narrow when ORCID actually confirmed something
            candidates, used_orcid = confirmed, True

    usable = _usable_hints(hints)
    src = "crossref+orcid" if used_orcid else "crossref"
    tried = 0
    for w in candidates:                             # most-cited first (local sort above)
        if tried >= max_candidates:
            break
        doi = openalex.valid_doi(w.get("doi"))
        if not doi:
            continue
        tried += 1
        work = oa_work_by_doi(doi, mailto=mailto)    # ~1 credit
        if work is None:                             # OpenAlex fetch failed / credit-exhausted → retry
            return FallbackResult(fetch_failed=True)
        nmatch = [a for a in (work.get("authors") or [])
                  if a.get("id") and openalex._name_match(nm, openalex._norm(a.get("name") or ""))]
        for a in nmatch:
            if usable:
                # Verify the hint against the author's WHOLE-CAREER institutions (a single-record author
                # fetch is FREE), not this one paper's affiliation — an old foundational paper often
                # predates the founder's current institution. Mirrors the primary path's precision.
                prof = oa_author_by_id(a["id"], mailto=mailto)
                if prof is None:                     # profile fetch failed → transient, retry
                    return FallbackResult(fetch_failed=True)
                if _hint_ok(usable, prof.get("institutions") or []):
                    return FallbackResult(author_id=a["id"], foundational_doi=doi, source=src)
            elif len(nmatch) == 1:                    # no usable hint → accept only an unambiguous match
                return FallbackResult(author_id=a["id"], foundational_doi=doi, source=src)
    return FallbackResult(source="none")             # genuine no-match across sources


def orcid_dois_for(name: str, hints: list[str], token: str) -> Optional[list[str]]:
    """Best-effort ORCID DOI set for a founder (name + institution hint → iD → works). None if ORCID is
    unavailable (no token) or the fetch failed; the resolver then proceeds Crossref-only."""
    if not token:
        return None
    cands = orcid.search(name, token)
    if not cands:
        return None
    usable = _usable_hints(hints)
    nm = openalex._norm(name)
    named = [c for c in cands
             if openalex._name_match(nm, openalex._norm(f"{c.get('given', '')} {c.get('family', '')}"))]
    if not named:
        return None
    pick = None
    if usable:
        for c in named:
            if any(any(openalex._norm(h) in openalex._norm(i) or openalex._norm(i) in openalex._norm(h)
                       for i in c.get("institutions") or []) for h in usable):
                pick = c
                break
    if pick is None:
        pick = named[0] if len(named) == 1 else None      # no hint / no inst match → only a unique name
    if not pick or not pick.get("orcid"):
        return None
    return orcid.work_dois(pick["orcid"], token)


def resolve_founder(name: str, hints: list[str], *, mailto: str = "", max_candidates: int = 3,
                    orcid_token: Optional[str] = None) -> FallbackResult:
    """Production entry: fetch the founder's ORCID DOIs (if a token is available) then resolve. Thin wrapper
    over :func:`resolve` so the real clients are used; tests exercise :func:`resolve` with injected clients.
    """
    dois = orcid_dois_for(name, hints, orcid_token) if orcid_token else None
    return resolve(name, hints, mailto=mailto, max_candidates=max_candidates, orcid_dois=dois)
