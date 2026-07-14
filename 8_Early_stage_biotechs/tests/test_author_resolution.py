"""D25 — free author-resolution fallback (Crossref + optional ORCID) offline tests. All network is
injected/mocked; also covers the DOI-validation security guard and the ORCID env gating."""

from __future__ import annotations

import pytest

from early_detection.clients import crossref, openalex, orcid
from early_detection.config import Config
from early_detection.models import Entity
from early_detection.signals import author_resolution as ar
from early_detection.signals.author_resolution import FallbackResult, resolve
from early_detection.signals.literature import ingest_literature
from early_detection.store import Store


# ── DOI validation (security guard) ───────────────────────────────────────────
def test_valid_doi_accepts_and_normalizes():
    assert openalex.valid_doi("10.1161/01.cir.84.3.1145") == "10.1161/01.cir.84.3.1145"
    assert openalex.valid_doi("https://doi.org/10.1161/abc") == "10.1161/abc"
    assert openalex.valid_doi("doi:10.1097/XYZ-123") == "10.1097/XYZ-123"


def test_valid_doi_rejects_junk_and_traversal():
    for bad in ("", None, "not-a-doi", "javascript:alert(1)", "http://evil.example/x",
                "10.1/x",                      # registrant too short (<4 digits)
                "10.1161/../../etc/passwd"):   # path-traversal shape
        assert openalex.valid_doi(bad) is None


# ── Crossref + OpenAlex parsers ───────────────────────────────────────────────
def test_crossref_parse_author_works():
    payload = {"message": {"items": [
        {"DOI": "10.1161/found", "title": ["Foundational"], "is-referenced-by-count": 3000,
         "issued": {"date-parts": [[1991, 3]]},
         "author": [{"given": "Stuart", "family": "Rich",
                     "affiliation": [{"name": "Northwestern University"}],
                     "ORCID": "https://orcid.org/0000-0002-1825-0097"}]},
    ]}}
    works = crossref.parse_author_works(payload)
    assert works[0]["doi"] == "10.1161/found" and works[0]["year"] == 1991
    assert works[0]["cited_by_count"] == 3000
    assert works[0]["authors"][0]["family"] == "Rich"
    assert works[0]["authors"][0]["orcid"] == "0000-0002-1825-0097"


def test_openalex_authors_of_work():
    work = {"authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "Stuart Rich"},
                             "institutions": [{"display_name": "Northwestern University"}]}]}
    au = openalex.authors_of_work(work)
    assert au == [{"id": "A1", "name": "Stuart Rich", "institutions": ["Northwestern University"]}]


# ── resolver core (injected clients) ──────────────────────────────────────────
_CR = [
    {"doi": "10.1161/found", "title": "Foundational", "year": 1991, "cited_by_count": 3000,
     "authors": [{"given": "Stuart", "family": "Rich", "affiliations": ["Northwestern"], "orcid": None}]},
    {"doi": "10.1000/other", "title": "Unrelated", "year": 2000, "cited_by_count": 10,
     "authors": [{"given": "Someone", "family": "Else", "affiliations": [], "orcid": None}]},
]


def _oa_work(_id):
    """An OpenAlex work carrying one name-matching author id (institutions come from the profile fetch)."""
    return {"id": _id, "title": "W", "authors": [{"id": _id, "name": "Stuart Rich", "institutions": []}]}


def _prof(insts):
    return lambda aid, **k: {"id": aid, "name": "Stuart Rich", "institutions": insts}


def test_resolve_happy_path_crossref():
    calls = []

    def oa(doi, **kw):
        calls.append(doi)
        return _oa_work("A1")

    r = resolve("Stuart Rich", ["Northwestern University"], cr_search=lambda n, **k: _CR,
                oa_work_by_doi=oa, oa_author_by_id=_prof(["Northwestern University"]))
    assert r.author_id == "A1" and r.source == "crossref" and r.foundational_doi == "10.1161/found"
    assert calls == ["10.1161/found"]                 # only the name-matched work was mapped


def test_resolve_institution_mismatch_returns_no_match():
    # hint verified against the author's CAREER institutions (profile) — none match → no resolution
    r = resolve("Stuart Rich", ["Stanford University"], cr_search=lambda n, **k: _CR,
                oa_work_by_doi=lambda doi, **k: _oa_work("A1"), oa_author_by_id=_prof(["Northwestern"]))
    assert r.author_id is None and r.source == "none" and r.fetch_failed is False


def test_resolve_no_hint_unique_name_accepts():
    # no usable hint → accept an unambiguous name match without fetching the profile
    def _no_profile(aid, **k):  # pragma: no cover
        raise AssertionError("profile must not be fetched when there is no usable hint")

    r = resolve("Stuart Rich", [], cr_search=lambda n, **k: _CR,
                oa_work_by_doi=lambda doi, **k: _oa_work("A1"), oa_author_by_id=_no_profile)
    assert r.author_id == "A1"


def test_resolve_crossref_fetch_failure_is_transient():
    r = resolve("Stuart Rich", ["Northwestern"], cr_search=lambda n, **k: None,
                oa_work_by_doi=lambda doi, **k: _oa_work("A1"), oa_author_by_id=_prof(["Northwestern"]))
    assert r.fetch_failed is True and r.author_id is None


def test_resolve_openalex_budget_failure_is_transient():
    r = resolve("Stuart Rich", ["Northwestern"], cr_search=lambda n, **k: _CR,
                oa_work_by_doi=lambda doi, **k: None, oa_author_by_id=_prof(["Northwestern"]))
    assert r.fetch_failed is True


def test_resolve_author_profile_failure_is_transient():
    r = resolve("Stuart Rich", ["Northwestern"], cr_search=lambda n, **k: _CR,
                oa_work_by_doi=lambda doi, **k: _oa_work("A1"),
                oa_author_by_id=lambda aid, **k: None)      # profile fetch failed → retry
    assert r.fetch_failed is True


def test_resolve_orcid_confirms_and_tags_source():
    r = resolve("Stuart Rich", ["Northwestern University"], cr_search=lambda n, **k: _CR,
                oa_work_by_doi=lambda doi, **k: _oa_work("A1"),
                oa_author_by_id=_prof(["Northwestern University"]), orcid_dois=["10.1161/found"])
    assert r.author_id == "A1" and r.source == "crossref+orcid"


def test_resolve_orcid_no_overlap_falls_back_to_crossref():
    r = resolve("Stuart Rich", ["Northwestern University"], cr_search=lambda n, **k: _CR,
                oa_work_by_doi=lambda doi, **k: _oa_work("A1"),
                oa_author_by_id=_prof(["Northwestern University"]),
                orcid_dois=["10.9999/unrelated"])            # no overlap → don't over-narrow to empty
    assert r.author_id == "A1" and r.source == "crossref"


def test_resolve_name_mismatch_no_candidates():
    r = resolve("Jane Doe", ["MIT"], cr_search=lambda n, **k: _CR,
                oa_work_by_doi=lambda doi, **k: _oa_work("A1"), oa_author_by_id=_prof(["MIT"]))
    assert r.author_id is None and r.source == "none"


def test_resolve_caps_candidate_maps():
    many = [{"doi": f"10.1161/w{i}", "title": "t", "year": 2000, "cited_by_count": 100 - i,
             "authors": [{"given": "Stuart", "family": "Rich", "affiliations": [], "orcid": None}]}
            for i in range(6)]
    calls = []

    def oa(doi, **kw):
        calls.append(doi)
        return _oa_work("A1")

    r = resolve("Stuart Rich", ["Northwestern"], max_candidates=2, cr_search=lambda n, **k: many,
                oa_work_by_doi=oa, oa_author_by_id=_prof(["Harvard"]))   # never matches the hint
    assert r.author_id is None and len(calls) == 2      # stopped after the cap


# ── ORCID parsers + env gating ────────────────────────────────────────────────
def test_orcid_credentials_absent_by_default(monkeypatch):
    monkeypatch.delenv("ORCID_CLIENT_ID", raising=False)
    monkeypatch.delenv("ORCID_CLIENT_SECRET", raising=False)
    assert orcid.credentials() is None
    assert orcid.get_token() is None                    # no creds → no token, no network


def test_orcid_parse_work_dois():
    payload = {"group": [{"work-summary": [
        {"external-ids": {"external-id": [
            {"external-id-type": "doi", "external-id-value": "10.1161/found"},
            {"external-id-type": "eid", "external-id-value": "ignore"}]}}]}]}
    assert orcid.parse_work_dois(payload) == ["10.1161/found"]


# ── literature integration (fallback fires when the primary misses) ───────────
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "ar.db")
    yield s
    s.close()


def _seed(store):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Tenax Therapeutics", cik="0000000001",
                               jurisdiction="US"))
    store.save_founders("cik:1", founders=[{"name": "Stuart Rich", "role": "CMO",
                                            "institution": "Northwestern University",
                                            "is_company_officer": True}],
                        affiliations=[], prompt_version="v1")


def test_literature_uses_fallback_when_primary_misses(store, tmp_path):
    _seed(store)
    cfg = Config(db_path=tmp_path / "ar.db")

    def fake_works(aid, *, sort="", per_page=25, **kw):
        if sort.startswith("cited_by_count"):
            return [{"id": "Wfound", "title": "F", "year": 1991, "date": "1991-01-01",
                     "cited_by_count": 3000, "institutions": [], "author_names": ["Stuart Rich"]}]
        return []

    def fake_citing(wid, **kw):
        return [{"id": "Wc1", "title": "indep", "year": 2026, "date": "2026-01-01",
                 "cited_by_count": 1, "institutions": ["Harvard"], "author_names": ["Alice"]}]

    res = ingest_literature(
        store, cfg, today_year=2026,
        search_authors=lambda name, **kw: [],                       # primary OpenAlex finds nothing
        author_works=fake_works, citing_works=fake_citing,
        resolve_fallback=lambda name, hints: FallbackResult(author_id="Afb", source="crossref"))
    assert res.authors_resolved == 1 and res.authors_resolved_fallback == 1
    assert res.independent_citations == 1
    assert store.founders_for("cik:1")[0]["openalex_author_id"] == "Afb"


def test_literature_fallback_transient_leaves_unstamped(store, tmp_path):
    _seed(store)
    cfg = Config(db_path=tmp_path / "ar.db")
    res = ingest_literature(
        store, cfg,
        search_authors=lambda name, **kw: [], author_works=lambda *a, **k: [],
        citing_works=lambda *a, **k: [],
        resolve_fallback=lambda name, hints: FallbackResult(fetch_failed=True))
    assert res.authors_resolved == 0
    assert store.founders_for("cik:1")[0]["literature_at"] is None    # left for retry, not stamped


# ── crossref-first (D25 promotion): free resolver runs BEFORE the 10-credit search ────────────────
def test_crossref_first_skips_the_10credit_search(store, tmp_path):
    _seed(store)
    cfg = Config(db_path=tmp_path / "ar.db", author_crossref_first=True)   # default

    def _no_search(*a, **k):  # pragma: no cover
        raise AssertionError("the 10-credit OpenAlex author search must be SKIPPED when Crossref resolves")

    res = ingest_literature(
        store, cfg, today_year=2026, search_authors=_no_search,
        author_works=lambda aid, *, sort="", per_page=25, **kw: (
            [{"id": "Wf", "title": "F", "year": 1991, "date": "1991-01-01", "cited_by_count": 9,
              "institutions": [], "author_names": []}] if sort.startswith("cited_by_count") else []),
        citing_works=lambda wid, **kw: [],
        resolve_fallback=lambda name, hints: FallbackResult(author_id="Afb", source="crossref"))
    assert res.authors_resolved == 1 and res.authors_resolved_fallback == 1
    assert store.founders_for("cik:1")[0]["openalex_author_id"] == "Afb"


def test_crossref_first_backstops_to_search_on_fallback_miss(store, tmp_path):
    from early_detection.clients import openalex
    _seed(store)
    cfg = Config(db_path=tmp_path / "ar.db", author_crossref_first=True)
    authors = [{"id": "A1", "display_name": "Stuart Rich", "works_count": 9, "cited_by_count": 99,
                "institutions": ["Northwestern University"]}]

    res = ingest_literature(
        store, cfg, today_year=2026,
        search_authors=lambda name, **kw: authors,                     # backstop resolves
        author_works=lambda aid, *, sort="", per_page=25, **kw: (
            [{"id": "Wf", "title": "F", "year": 1991, "date": "1991-01-01", "cited_by_count": 9,
              "institutions": [], "author_names": []}] if sort.startswith("cited_by_count") else []),
        citing_works=lambda wid, **kw: [],
        resolve_fallback=lambda name, hints: FallbackResult(source="none"))   # free path misses
    assert res.authors_resolved == 1 and res.authors_resolved_fallback == 0   # resolved via the backstop
    assert store.founders_for("cik:1")[0]["openalex_author_id"] == "A1"


# ── crossref-ONLY (D29): free resolver misses → NO 10-credit backstop ──────────────────────────────
def test_crossref_only_skips_backstop_on_miss(store, tmp_path):
    _seed(store)
    cfg = Config(db_path=tmp_path / "ar.db", author_crossref_first=True, author_search_backstop=False)

    def _no_search(*a, **k):  # pragma: no cover
        raise AssertionError("crossref-only must NOT run the 10-credit search when the free path misses")

    res = ingest_literature(
        store, cfg, today_year=2026, search_authors=_no_search,
        author_works=lambda *a, **k: [], citing_works=lambda *a, **k: [],
        resolve_fallback=lambda name, hints: FallbackResult(source="none"))   # genuine free-path miss
    assert res.authors_resolved == 0
    # genuine miss → stamped done (no wasted 10-credit search), not retried
    assert store.founders_for("cik:1")[0]["literature_at"] is not None


def test_crossref_only_still_resolves_via_free_path(store, tmp_path):
    _seed(store)
    cfg = Config(db_path=tmp_path / "ar.db", author_crossref_first=True, author_search_backstop=False)
    res = ingest_literature(
        store, cfg, today_year=2026, search_authors=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        author_works=lambda aid, *, sort="", per_page=25, **kw: (
            [{"id": "Wf", "title": "F", "year": 1991, "date": "1991-01-01", "cited_by_count": 9,
              "institutions": [], "author_names": []}] if sort.startswith("cited_by_count") else []),
        citing_works=lambda wid, **kw: [],
        resolve_fallback=lambda name, hints: FallbackResult(author_id="Afb", source="crossref"))
    assert res.authors_resolved == 1 and res.authors_resolved_fallback == 1
    assert store.founders_for("cik:1")[0]["openalex_author_id"] == "Afb"


def test_crossref_only_transient_failure_left_for_retry(store, tmp_path):
    _seed(store)
    cfg = Config(db_path=tmp_path / "ar.db", author_crossref_first=True, author_search_backstop=False)
    res = ingest_literature(
        store, cfg, search_authors=lambda *a, **k: (_ for _ in ()).throw(AssertionError()),
        author_works=lambda *a, **k: [], citing_works=lambda *a, **k: [],
        resolve_fallback=lambda name, hints: FallbackResult(fetch_failed=True))   # Crossref transient
    assert res.authors_resolved == 0
    assert store.founders_for("cik:1")[0]["literature_at"] is None    # NOT stamped — retry next window
