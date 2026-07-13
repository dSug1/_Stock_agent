"""Literature & citation-network signal (spec §3.1) — the module's thesis signal.

For each founder (from M9), resolve the OpenAlex author (high-precision, disambiguated on the founder's
institution + company), then write `literature` signals:
  - recent **publications** by the founder-scientist;
  - **citations** of the founder's foundational paper, each tagged with a cheap independence heuristic
    (self / same_institution / independent) — the independent ones are the signal the module exists to
    surface. Claude's §5.2 independence classification refines this later.

Zero-LLM. Fail-soft per founder; persists per founder (crash-safe); idempotent (stable signal_id).
OpenAlex politeness: polite pool via mailto + a shared rate limiter.
"""

from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

from ..clients import openalex, orcid
from ..config import Config
from ..models import SignalRecord
from ..store import Store, now_iso
from . import author_resolution

log = logging.getLogger(__name__)

SIGNAL_TYPE = "literature"
SOURCE = "openalex"
_LIMITER = None      # OpenAlex pacing is owned by the client (shared 5/s limiter + Retry-After retry)
_NONALNUM = re.compile(r"[^a-z0-9 ]+")


@dataclass
class LiteratureResult:
    founders: int = 0
    processed: int = 0                # founders actually attempted this run (before any budget stop)
    authors_resolved: int = 0
    authors_resolved_fallback: int = 0   # of authors_resolved, how many came via the Crossref/ORCID fallback
    publications: int = 0
    citations: int = 0
    independent_citations: int = 0
    stopped_early: bool = False       # True if the OpenAlex credit reserve halted the run
    budget_left: int = 0              # founders left unprocessed when it stopped (retry next window)
    by_independence: dict = field(default_factory=lambda: {"self": 0, "same_institution": 0, "independent": 0})


def _norm(s: Optional[str]) -> str:
    if not s:
        return ""
    folded = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(_NONALNUM.sub(" ", folded).split())


def _sid(*parts: str) -> str:
    return "lit_" + hashlib.sha1("|".join(p or "" for p in parts).encode()).hexdigest()[:20]


def classify_independence(founder_name: str, hints: list[str], citing: dict) -> str:
    """Cheap heuristic: is the citing work from the founder (self), their institution (same), or an
    independent lab? Compares the citing work's authors/institutions to the founder + their hints."""
    fn = _norm(founder_name)
    cauthors = {_norm(a) for a in citing.get("author_names") or []}
    if fn and fn in cauthors:
        return "self"
    cinsts = [_norm(i) for i in citing.get("institutions") or []]
    for h in hints:
        nh = _norm(h)
        if nh and any(nh in ci or ci in nh for ci in cinsts):
            return "same_institution"
    return "independent"


def ingest_literature(store: Store, cfg: Config, *, limit: int | None = None,
                      today_year: Optional[int] = None,
                      search_authors: Callable = openalex.search_authors,
                      author_works: Callable = openalex.author_works,
                      citing_works: Callable = openalex.citing_works,
                      resolve_fallback: Optional[Callable] = None) -> LiteratureResult:
    """Resolve founders → OpenAlex authors and ingest publication + citation signals. The OpenAlex calls
    (and the free Crossref/ORCID ``resolve_fallback``) are injectable for offline tests. Persists per
    founder."""
    mailto = cfg.openalex_mailto or _env_email()
    year = today_year or date.today().year

    # Free author-resolution fallback (D25): used when the primary OpenAlex author search fails to resolve
    # a founder. Injected in tests; in production it is the real Crossref (+optional ORCID) resolver, with
    # the ORCID token fetched ONCE per run (only if ORCID_CLIENT_ID/_SECRET are set — else Crossref-only).
    do_fallback = resolve_fallback
    if do_fallback is None and cfg.author_fallback_enabled:
        orcid_token = orcid.get_token() if orcid.credentials() else None
        if orcid_token:
            log.info("literature: ORCID precision booster active")

        def do_fallback(name: str, hints: list[str]):  # noqa: E306
            return author_resolution.resolve_founder(
                name, hints, mailto=mailto,
                max_candidates=cfg.author_fallback_max_candidates, orcid_token=orcid_token)

    # Crossref-FIRST (D25 promotion): try the free resolver before the 10-credit OpenAlex author search,
    # paying the search only as a recall backstop. Requires the fallback to exist.
    crossref_first = cfg.author_crossref_first and do_fallback is not None

    def _resolve(name: str, hints: list[str]) -> tuple[str, Optional[str], bool]:
        """Resolve a founder to an OpenAlex author id. → (status, author_id, via_fallback) where status
        is 'resolved' | 'no_match' (genuine — stamp done) | 'retry' (transient — leave unstamped).

        crossref-first: free resolver → OpenAlex-search backstop only if it misses (credit saving).
        search-first (legacy): 10-credit OpenAlex search → free resolver only if it misses (recall)."""
        def _search():
            cands = search_authors(name, mailto=mailto, limiter=_LIMITER)
            if cands is None:                          # throttled/credit-exhausted → retry
                return ("retry", None, False)
            author = openalex.pick_author(name, hints, cands)
            return ("resolved", author["id"], False) if author else ("miss", None, False)

        if crossref_first:
            fb = do_fallback(name, hints)
            if fb and fb.author_id:                    # resolved FREE — the 10-credit search is skipped
                return ("resolved", fb.author_id, True)
            st, aid, _ = _search()                     # backstop (only reached when the free path missed)
            if st == "resolved":
                return ("resolved", aid, False)
            if st == "retry":
                return ("retry", None, False)
            # both missed: stamp no-match, UNLESS the free path merely had a transient fetch failure
            return ("retry", None, False) if (fb and fb.fetch_failed) else ("no_match", None, False)

        st, aid, _ = _search()                         # search-first
        if st == "resolved":
            return ("resolved", aid, False)
        if st == "retry":
            return ("retry", None, False)
        fb = do_fallback(name, hints) if do_fallback else None
        if fb and fb.fetch_failed:
            return ("retry", None, False)
        if fb and fb.author_id:
            return ("resolved", fb.author_id, True)
        return ("no_match", None, False)

    todo = store.founders_for_literature(limit=limit, only_missing=True)
    res = LiteratureResult(founders=len(todo))
    log.info("literature: %d founders to resolve (mailto=%s, fallback=%s, crossref_first=%s)",
             len(todo), bool(mailto), bool(do_fallback), crossref_first)

    for i, f in enumerate(todo):
        # Stop BEFORE starting a founder we can't afford to finish — leaving it (and the rest) UNSTAMPED
        # so a re-run next window resumes losslessly. Checked here (between founders), never mid-founder,
        # so a half-processed founder is never stamped/poisoned. Unknown budget (first call) → proceeds.
        if openalex.CREDITS.exhausted(cfg.openalex_credit_reserve):
            res.stopped_early = True
            res.budget_left = len(todo) - i
            log.warning("literature: OpenAlex credit reserve reached (remaining=%s, resets in ~%ss) — "
                        "stopping; %d founders left for the next window",
                        openalex.CREDITS.remaining, openalex.CREDITS.reset, res.budget_left)
            break
        res.processed += 1
        fid = f["id"]
        name = f["name"]
        hints = [h for h in (f.get("institution"), f.get("company_name")) if h]
        try:
            status, aid, via_fallback = _resolve(name, hints)
        except Exception as exc:  # noqa: BLE001 — fail-soft per founder
            log.warning("literature: resolution errored for %s (left for retry): %s", name, exc)
            continue
        if status == "retry":               # transient (throttle/credit/Crossref) — leave unstamped
            log.warning("literature: %s unresolved this pass (left for retry)", name)
            continue
        if status == "no_match":            # genuine no academic trail across sources → stamp done
            store.set_founder_literature(fid, author_id=None, foundational_work_id=None)
            continue
        res.authors_resolved += 1
        if via_fallback:
            res.authors_resolved_fallback += 1

        # recent publications by the founder
        pubs = _safe(author_works, aid, mailto=mailto, from_year=year - cfg.literature_pub_years,
                     sort="publication_date:desc", limiter=_LIMITER) or []
        for w in pubs:
            _emit(store, f, w, kind="publication", extra={"author_id": aid})
            res.publications += 1

        # foundational paper = the author's most-cited work
        top = _safe(author_works, aid, mailto=mailto, sort="cited_by_count:desc", per_page=1,
                    limiter=_LIMITER) or []
        foundational = top[0] if top else None
        found_id = foundational["id"] if foundational else None
        store.set_founder_literature(fid, author_id=aid, foundational_work_id=found_id)

        # citations of the foundational paper — the independent-citation signal
        if found_id:
            citing = _safe(citing_works, found_id, mailto=mailto,
                           from_year=year - cfg.literature_citation_years,
                           max_results=cfg.literature_max_citing, limiter=_LIMITER) or []
            for cw in citing:
                indep = classify_independence(name, hints, cw)
                res.by_independence[indep] = res.by_independence.get(indep, 0) + 1
                res.citations += 1
                if indep == "independent":
                    res.independent_citations += 1
                _emit(store, f, cw, kind="citation",
                      extra={"foundational_work_id": found_id, "independence": indep,
                             "citing_institutions": cw.get("institutions")})

    log.info("literature done: authors=%d (fallback=%d) pubs=%d citations=%d (independent=%d) by=%s",
             res.authors_resolved, res.authors_resolved_fallback, res.publications, res.citations,
             res.independent_citations, res.by_independence)
    return res


def _emit(store: Store, founder: dict, work: dict, *, kind: str, extra: dict) -> None:
    sid = _sid(founder["entity_id"], work.get("id") or "", kind, extra.get("foundational_work_id", ""))
    payload = {"kind": kind, "founder": founder["name"], "work_id": work.get("id"),
               "title": work.get("title"), "year": work.get("year"),
               "cited_by_count": work.get("cited_by_count"), "doi": work.get("doi"), **extra}
    store.insert_signal(SignalRecord(
        signal_id=sid, entity_id=founder["entity_id"], signal_type=SIGNAL_TYPE, source=SOURCE,
        raw_payload=payload, detected_at=now_iso(), event_date=work.get("date"), language="en",
    ))


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception as exc:  # noqa: BLE001 — fail-soft
        log.warning("literature: %s failed: %s", getattr(fn, "__name__", fn), exc)
        return None


def _env_email() -> str:
    """Fall back to the email in the repo USER_AGENT (already loaded by clients._net)."""
    import os
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", os.getenv("USER_AGENT", ""))
    return m.group(0) if m else ""
