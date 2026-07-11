"""OpenAlex client (free, no key) — the literature/citation signal source (spec §3.1).

OpenAlex is the spec's recommended source for author + citation-network + institution-affiliation data
at scale. (Module 6 dismissed OpenAlex in its D9 — but that was for *company→institution* matching,
where small-biotech coverage was thin; Module 8 uses *author→works→citations*, OpenAlex's forte, and
disambiguates on the founder's institution.) Rate-limit discipline from that lesson still applies:
polite pool via ``mailto`` + spacing + fail-open.

Author disambiguation is high-precision (a wrong author → wrong papers → wrong independence signal):
``pick_author`` requires a name-token match AND, when hints (founder institution / company) are known,
an institution match. Parsers are pure + unit-tested; fetches are fail-open (→ None/[]) and capped.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional
from urllib.parse import quote

from . import _net

log = logging.getLogger(__name__)

BASE = "https://api.openalex.org"
_NONALNUM = re.compile(r"[^a-z0-9 ]+")


def _short_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    return url.rsplit("/", 1)[-1]


def _norm(s: Optional[str]) -> str:
    if not s:
        return ""
    folded = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(_NONALNUM.sub(" ", folded).split())


def _mailto(mailto: str) -> str:
    return f"&mailto={quote(mailto)}" if mailto else ""


def _get(url: str, *, limiter: Optional[_net.RateLimiter] = None) -> Optional[dict]:
    return _net.safe_json(url, limiter=limiter, accept="application/json")


# ── authors ──────────────────────────────────────────────────────────────────

def _author_institutions(a: dict) -> list[str]:
    out = [i.get("display_name") for i in (a.get("last_known_institutions") or []) if i.get("display_name")]
    for aff in a.get("affiliations") or []:
        name = (aff.get("institution") or {}).get("display_name")
        if name:
            out.append(name)
    return out


def parse_authors(payload: dict) -> list[dict]:
    """authors response → [{id, display_name, works_count, cited_by_count, institutions:[names]}]."""
    out = []
    for a in (payload or {}).get("results", []) or []:
        out.append({
            "id": _short_id(a.get("id")),
            "display_name": a.get("display_name"),
            "works_count": a.get("works_count") or 0,
            "cited_by_count": a.get("cited_by_count") or 0,
            "institutions": _author_institutions(a),
        })
    return out


def search_authors(name: str, *, mailto: str = "", per_page: int = 10,
                   limiter: Optional[_net.RateLimiter] = None) -> list[dict]:
    if not name or not name.strip():
        return []
    url = f"{BASE}/authors?search={quote(name.strip())}&per-page={int(per_page)}{_mailto(mailto)}"
    return parse_authors(_get(url, limiter=limiter) or {})


def _name_match(a: str, b: str) -> bool:
    """Token-subset match in either direction (handles middle initials) with a shared surname."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return False
    if ta[-1] != tb[-1]:            # surname must match
        return False
    sa, sb = set(ta), set(tb)
    return sa <= sb or sb <= sa


def pick_author(name: str, hints: list[str], candidates: list[dict]) -> Optional[dict]:
    """High-precision author pick. ``hints`` = institution/company strings to disambiguate on.

    - name-token match required (surname + subset). Zero name matches → None.
    - if any usable hint: require an institution match; unique → accept, several → most-cited, none → None.
    - no usable hint: accept only a single name match (else ambiguous → None)."""
    nm = _norm(name)
    name_matches = [c for c in candidates if _name_match(nm, _norm(c["display_name"]))]
    if not name_matches:
        return None
    usable = [h for h in hints if _norm(h) and _norm(h) not in ("unknown", "none identified", "none", "na")]
    if usable:
        inst_matches = [c for c in name_matches
                        if any(any(_norm(h) in _norm(i) or _norm(i) in _norm(h) for i in c["institutions"])
                               for h in usable)]
        if not inst_matches:
            return None
        return max(inst_matches, key=lambda c: c["cited_by_count"])
    return name_matches[0] if len(name_matches) == 1 else None


# ── works ────────────────────────────────────────────────────────────────────

def parse_works(payload: dict) -> list[dict]:
    """works response → [{id, title, year, date, cited_by_count, doi, institutions[], author_names[]}]."""
    out = []
    for w in (payload or {}).get("results", []) or []:
        insts, authors = [], []
        for au in w.get("authorships") or []:
            nm = (au.get("author") or {}).get("display_name")
            if nm:
                authors.append(nm)
            for inst in au.get("institutions") or []:
                if inst.get("display_name"):
                    insts.append(inst["display_name"])
        out.append({
            "id": _short_id(w.get("id")),
            "title": w.get("title") or w.get("display_name"),
            "year": w.get("publication_year"),
            "date": w.get("publication_date"),
            "cited_by_count": w.get("cited_by_count") or 0,
            "doi": w.get("doi"),
            "institutions": insts,
            "author_names": authors,
        })
    return out


def author_works(author_id: str, *, mailto: str = "", from_year: Optional[int] = None,
                 sort: str = "cited_by_count:desc", per_page: int = 25,
                 limiter: Optional[_net.RateLimiter] = None) -> list[dict]:
    filt = f"author.id:{author_id}"
    if from_year:
        filt += f",from_publication_date:{int(from_year)}-01-01"
    url = f"{BASE}/works?filter={filt}&sort={sort}&per-page={int(per_page)}{_mailto(mailto)}"
    return parse_works(_get(url, limiter=limiter) or {})


def citing_works(work_id: str, *, mailto: str = "", from_year: Optional[int] = None,
                 max_results: int = 200, limiter: Optional[_net.RateLimiter] = None) -> list[dict]:
    """Works that cite ``work_id`` (the independent-citation signal). Paginated, capped at max_results."""
    filt = f"cites:{work_id}"
    if from_year:
        filt += f",from_publication_date:{int(from_year)}-01-01"
    out: list[dict] = []
    page, per = 1, 100
    while len(out) < max_results:
        url = (f"{BASE}/works?filter={filt}&sort=publication_date:desc"
               f"&per-page={per}&page={page}{_mailto(mailto)}")
        payload = _get(url, limiter=limiter)
        if not payload:
            break
        batch = parse_works(payload)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < per:
            break
        page += 1
    return out[:max_results]
