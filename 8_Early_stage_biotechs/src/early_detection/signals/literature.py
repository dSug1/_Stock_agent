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

from ..clients import openalex
from ..config import Config
from ..models import SignalRecord
from ..store import Store, now_iso

log = logging.getLogger(__name__)

SIGNAL_TYPE = "literature"
SOURCE = "openalex"
_LIMITER = None      # OpenAlex pacing is owned by the client (shared 5/s limiter + Retry-After retry)
_NONALNUM = re.compile(r"[^a-z0-9 ]+")


@dataclass
class LiteratureResult:
    founders: int = 0
    authors_resolved: int = 0
    publications: int = 0
    citations: int = 0
    independent_citations: int = 0
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
                      citing_works: Callable = openalex.citing_works) -> LiteratureResult:
    """Resolve founders → OpenAlex authors and ingest publication + citation signals. The three OpenAlex
    calls are injectable for offline tests. Persists per founder."""
    mailto = cfg.openalex_mailto or _env_email()
    year = today_year or date.today().year
    todo = store.founders_for_literature(limit=limit, only_missing=True)
    res = LiteratureResult(founders=len(todo))
    log.info("literature: %d founders to resolve (mailto=%s)", len(todo), bool(mailto))

    for f in todo:
        fid = f["id"]
        name = f["name"]
        hints = [h for h in (f.get("institution"), f.get("company_name")) if h]
        try:
            cands = search_authors(name, mailto=mailto, limiter=_LIMITER)
        except Exception as exc:  # noqa: BLE001 — fail-soft per founder
            log.warning("literature: author search errored for %s (left for retry): %s", name, exc)
            continue
        if cands is None:                  # fetch failed after retries (throttled) — retry later
            log.warning("literature: author search unavailable for %s (left for retry)", name)
            continue
        # cands == [] is a GENUINE no-match → stamp so we don't re-query a founder with no OpenAlex trail
        author = openalex.pick_author(name, hints, cands)
        if not author:
            store.set_founder_literature(fid, author_id=None, foundational_work_id=None)
            continue
        res.authors_resolved += 1
        aid = author["id"]

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

    log.info("literature done: authors=%d pubs=%d citations=%d (independent=%d) by=%s",
             res.authors_resolved, res.publications, res.citations, res.independent_citations,
             res.by_independence)
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
