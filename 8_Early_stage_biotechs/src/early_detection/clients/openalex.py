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
import threading
import unicodedata
from typing import Optional
from urllib.parse import quote

from . import _net

log = logging.getLogger(__name__)

BASE = "https://api.openalex.org"
_NONALNUM = re.compile(r"[^a-z0-9 ]+")

# One shared pacer for ALL OpenAlex calls, regardless of which signal drives them. OpenAlex's polite
# pool (with mailto) documents ~10 req/s, but it 429s under sustained load well below that — the
# literature→independence back-to-back sweep on 2026-07-11 got hard-throttled at 8/s. Pace conservatively
# at 5/s here AND retry with Retry-After/backoff in ``_get`` (belt-and-suspenders): the limiter avoids
# most 429s, the retry recovers the rest so no citation signal is silently dropped.
_LIMITER = _net.RateLimiter(per_sec=5.0)


# ── credit / USD quota (the 2026 OpenAlex model) ─────────────────────────────
# As of mid-2026 OpenAlex enforces a CREDIT budget on top of the per-second rate limit: each response
# carries ``X-RateLimit-Remaining`` (credits left this window), ``X-RateLimit-Limit`` (~1000),
# ``X-RateLimit-Reset`` (seconds to reset, ~daily) and ``X-RateLimit-Remaining-USD`` (~$0.10 budget).
# Cost is PER-ENDPOINT, not per-call — an ``/authors?search=`` costs ~10 credits, a ``/works`` list
# query ~1 — so ~1000 credits ≈ only ~100 author searches/day on the free tier. We do NOT hardcode these
# prices: the tracker reads the server's own remaining count after every call, so it self-corrects. A
# daily run reads this to STOP before the budget is gone rather than getting hard-429'd mid-sweep.
class CreditTracker:
    """Thread-safe view of OpenAlex's remaining credit budget, updated from response headers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.remaining: Optional[int] = None       # credits left this window; None until first observed
        self.limit: Optional[int] = None           # window budget (~1000)
        self.reset: Optional[int] = None            # seconds until the window resets
        self.remaining_usd: Optional[float] = None

    @staticmethod
    def _get_num(headers, name, cast):
        try:
            v = headers.get(name)
            return cast(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def note(self, headers) -> None:
        """Observe a response's quota headers (called via ``_net`` on success AND on a 429)."""
        if headers is None:
            return
        rem = self._get_num(headers, "X-RateLimit-Remaining", int)
        lim = self._get_num(headers, "X-RateLimit-Limit", int)
        rst = self._get_num(headers, "X-RateLimit-Reset", int)
        usd = self._get_num(headers, "X-RateLimit-Remaining-USD", float)
        with self._lock:
            if rem is not None:
                self.remaining = rem
            if lim is not None:
                self.limit = lim
            if rst is not None:
                self.reset = rst
            if usd is not None:
                self.remaining_usd = usd

    def exhausted(self, reserve: int = 0) -> bool:
        """True once observed remaining credits fall to/below ``reserve``. False while unknown (None) so
        the first call of a run is always allowed — it populates the tracker for the calls that follow."""
        with self._lock:
            return self.remaining is not None and self.remaining <= reserve

    def status(self) -> dict:
        with self._lock:
            return {"remaining": self.remaining, "limit": self.limit,
                    "reset_s": self.reset, "remaining_usd": self.remaining_usd}

    def reset_state(self) -> None:
        """Forget observed budget (tests / a fresh process)."""
        with self._lock:
            self.remaining = self.limit = self.reset = None
            self.remaining_usd = None


# Module-level shared tracker — every OpenAlex call feeds it, every budget check reads it.
CREDITS = CreditTracker()

# Last-ditch backstop inside ``_get``: even a loop-level reserve can't stop a call once a founder is
# mid-flight, so the client refuses to fetch when the observed budget is truly gone (returns None →
# callers treat it as a throttle → the founder is left UNSTAMPED for retry, never poisoned). The real
# budget guard is the config-driven per-founder reserve enforced by the signal loops.
_CREDIT_HARD_FLOOR = 0


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
    """Fetch OpenAlex JSON through the shared pacer + Retry-After/backoff retry, feeding every response's
    quota headers into ``CREDITS``. A caller-supplied ``limiter`` overrides the module default (kept for
    tests); production always shares ``_LIMITER``. Returns None (→ callers treat as a throttle, leave the
    founder for retry) when the observed credit budget is already gone — so a hard-quota window doesn't
    burn retry/backoff time on calls that can only 429."""
    if CREDITS.exhausted(_CREDIT_HARD_FLOOR):
        log.warning("openalex: credit budget exhausted (remaining=%s, resets in ~%ss) — skipping fetch",
                    CREDITS.remaining, CREDITS.reset)
        return None
    return _net.safe_json_retry(url, limiter=limiter or _LIMITER, accept="application/json",
                                on_headers=CREDITS.note)


def probe_credits(mailto: str = "") -> dict:
    """One cheap (~1-credit) call to populate ``CREDITS`` before a run, so a daily runner can report the
    budget and bail early if it's already spent. Uses a ``/works`` LIST query (cheap), never an author
    search (10 credits). Returns the tracker status dict."""
    _get(f"{BASE}/works?per-page=1&select=id{_mailto(mailto)}")
    return CREDITS.status()


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
                   limiter: Optional[_net.RateLimiter] = None) -> Optional[list[dict]]:
    """Author candidates for ``name``. Returns ``None`` when the FETCH FAILED (e.g. OpenAlex 429 after
    retries) vs ``[]`` for a genuine no-match — the caller must not treat a throttled fetch as "no
    author" and stamp the founder done (that permanently drops it from the retry work-list)."""
    if not name or not name.strip():
        return []
    url = f"{BASE}/authors?search={quote(name.strip())}&per-page={int(per_page)}{_mailto(mailto)}"
    payload = _get(url, limiter=limiter)
    if payload is None:                    # fetch failed after retries — signal retry, don't say no-match
        return None
    return parse_authors(payload)


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

# A DOI from a third-party (Crossref/ORCID) response is UNTRUSTED input that we splice into a request.
# Validate it to the bare ``10.<registrant>/<suffix>`` shape and reject anything else, so a malicious or
# malformed DOI can't inject a path/host (defence-in-depth on top of the hard-coded BASE host).
_DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:a-z0-9<>\[\]]+$", re.IGNORECASE)


def valid_doi(doi: Optional[str]) -> Optional[str]:
    """Normalize (strip a ``doi.org`` / ``doi:`` prefix) and validate a DOI. Returns the bare DOI, or None
    for anything that isn't a well-formed DOI."""
    if not doi:
        return None
    d = doi.strip()
    for pre in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if d.lower().startswith(pre):
            d = d[len(pre):]
            break
    if ".." in d:                       # no legitimate DOI contains '..'; reject path-traversal shapes
        return None
    return d if _DOI_RE.match(d) else None


def authors_of_work(work: dict) -> list[dict]:
    """Per-author ``{id, name, institutions}`` for a single OpenAlex work — lets the fallback resolver map
    a founder (name + institution hint) to the author's OpenAlex id after resolving the foundational paper
    via Crossref/ORCID (a DOI), instead of the 10-credit author search."""
    out = []
    for au in work.get("authorships") or []:
        a = au.get("author") or {}
        out.append({
            "id": _short_id(a.get("id")),
            "name": a.get("display_name"),
            "institutions": [i.get("display_name") for i in (au.get("institutions") or [])
                             if i.get("display_name")],
        })
    return out


def work_by_doi(doi: str, *, mailto: str = "",
                limiter: Optional[_net.RateLimiter] = None) -> Optional[dict]:
    """Resolve a DOI → its OpenAlex work (``{id, title, authors:[{id,name,institutions}]}``) via the cheap
    (~1-credit) ``filter=doi:`` query — the fallback path's bridge from a Crossref/ORCID-resolved paper to
    the OpenAlex citation graph, avoiding the 10-credit author search. Returns None on an invalid DOI or a
    fetch failure (credit-exhausted/throttled → caller leaves the founder for retry)."""
    d = valid_doi(doi)
    if not d:
        return None
    url = (f"{BASE}/works?filter=doi:{quote(d, safe='')}&per-page=1"
           f"&select=id,display_name,authorships{_mailto(mailto)}")
    payload = _get(url, limiter=limiter)
    if not payload:
        return None
    results = payload.get("results") or []
    if not results:
        return None
    w = results[0]
    return {"id": _short_id(w.get("id")), "title": w.get("display_name"),
            "authors": authors_of_work(w)}


def parse_works(payload: dict) -> list[dict]:
    """works response → [{id, title, year, date, cited_by_count, doi, institutions[], author_names[],
    author_ids[], institution_types[]}]. author_ids + institution_types power the §5.2 co-authorship /
    industry-authored independence classification."""
    out = []
    for w in (payload or {}).get("results", []) or []:
        insts, authors, aids, itypes = [], [], [], []
        for au in w.get("authorships") or []:
            a = au.get("author") or {}
            if a.get("display_name"):
                authors.append(a["display_name"])
            aid = _short_id(a.get("id"))
            if aid:
                aids.append(aid)
            for inst in au.get("institutions") or []:
                if inst.get("display_name"):
                    insts.append(inst["display_name"])
                if inst.get("type"):
                    itypes.append(inst["type"])
        out.append({
            "id": _short_id(w.get("id")),
            "title": w.get("title") or w.get("display_name"),
            "year": w.get("publication_year"),
            "date": w.get("publication_date"),
            "cited_by_count": w.get("cited_by_count") or 0,
            "doi": w.get("doi"),
            "institutions": insts,
            "author_names": authors,
            "author_ids": aids,
            "institution_types": itypes,
        })
    return out


def coauthor_ids(author_id: str, *, mailto: str = "", max_works: int = 200,
                 limiter: Optional[_net.RateLimiter] = None) -> set:
    """The set of OpenAlex author IDs who have co-published with ``author_id`` (excluding the author).

    This is the founder's collaboration/lineage network — a citing author in this set is NOT an
    independent validator even at a different institution (former co-author, trainee, collaborator)."""
    ids: set = set()
    fetched = 0
    page, per = 1, 100
    while fetched < max_works:
        url = (f"{BASE}/works?filter=author.id:{author_id}&per-page={per}&page={page}"
               f"&select=authorships{_mailto(mailto)}")
        payload = _get(url, limiter=limiter)
        if not payload:
            break
        for w in payload.get("results", []) or []:
            for au in w.get("authorships") or []:
                aid = _short_id((au.get("author") or {}).get("id"))
                if aid and aid != author_id:
                    ids.add(aid)
            fetched += 1
        if len(payload.get("results", []) or []) < per:
            break
        page += 1
    return ids


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
