"""M10 — literature/citation signal offline tests (injected OpenAlex; no network)."""

from __future__ import annotations

import pytest

from early_detection.clients import openalex
from early_detection.config import Config
from early_detection.models import Entity
from early_detection.signals.literature import classify_independence, ingest_literature
from early_detection.store import Store


# ── OpenAlex parsers + disambiguation ─────────────────────────────────────────
_AUTHORS = {"results": [
    {"id": "https://openalex.org/A1", "display_name": "Stuart Rich", "works_count": 264,
     "cited_by_count": 33490, "last_known_institutions": [{"display_name": "Northwestern University"},
                                                          {"display_name": "Tenax Therapeutics"}]},
    {"id": "https://openalex.org/A2", "display_name": "Stuart Rich", "works_count": 5,
     "cited_by_count": 20, "last_known_institutions": [{"display_name": "Some Community College"}]},
]}


def test_parse_authors():
    a = openalex.parse_authors(_AUTHORS)
    assert a[0]["id"] == "A1" and a[0]["cited_by_count"] == 33490
    assert "Tenax Therapeutics" in a[0]["institutions"]


def test_pick_author_disambiguates_on_institution():
    cands = openalex.parse_authors(_AUTHORS)
    # founder institution "Northwestern" → picks A1, not the community-college namesake
    assert openalex.pick_author("Stuart Rich", ["Northwestern University Feinberg"], cands)["id"] == "A1"
    # company name also works as a hint
    assert openalex.pick_author("Stuart Rich", ["Tenax Therapeutics, Inc."], cands)["id"] == "A1"


def test_pick_author_no_institution_match_returns_none():
    cands = openalex.parse_authors(_AUTHORS)
    assert openalex.pick_author("Stuart Rich", ["Harvard University"], cands) is None


def test_pick_author_name_mismatch():
    cands = openalex.parse_authors(_AUTHORS)
    assert openalex.pick_author("Jane Doe", ["Northwestern"], cands) is None


def test_pick_author_middle_initial_tolerated():
    cands = [{"id": "A9", "display_name": "Stuart M. Rich", "works_count": 1, "cited_by_count": 1,
              "institutions": []}]
    assert openalex.pick_author("Stuart Rich", [], cands)["id"] == "A9"   # unique name match, no hint


# ── independence heuristic ────────────────────────────────────────────────────
def test_classify_independence():
    cw = {"author_names": ["Someone Else"], "institutions": ["Harvard University"]}
    assert classify_independence("Stuart Rich", ["Northwestern"], cw) == "independent"
    assert classify_independence("Stuart Rich", ["Northwestern"],
                                 {"author_names": ["Stuart Rich"], "institutions": []}) == "self"
    assert classify_independence("Stuart Rich", ["Northwestern University"],
                                 {"author_names": ["Other"], "institutions": ["Northwestern University Feinberg"]}) == "same_institution"


# ── end-to-end ingester (injected OpenAlex) ───────────────────────────────────
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "l.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "l.db", literature_pub_years=4, literature_citation_years=6)


def _seed(store):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Tenax Therapeutics", cik="0000000001",
                               jurisdiction="US"))
    store.save_founders("cik:1", founders=[{"name": "Stuart Rich", "role": "CMO",
                                            "institution": "Northwestern University",
                                            "is_company_officer": True}],
                        affiliations=[], prompt_version="v1")


def test_ingest_resolves_author_and_emits_signals(store, cfg):
    _seed(store)

    def fake_search(name, **kw):
        return openalex.parse_authors(_AUTHORS)

    def fake_works(aid, *, sort="", per_page=25, **kw):
        if sort.startswith("cited_by_count"):
            return [{"id": "Wfound", "title": "Foundational", "year": 1991, "date": "1991-01-01",
                     "cited_by_count": 3507, "institutions": [], "author_names": ["Stuart Rich"]}]
        return [{"id": "Wpub", "title": "Recent paper", "year": 2025, "date": "2025-03-01",
                 "cited_by_count": 4, "institutions": [], "author_names": ["Stuart Rich"], "doi": None}]

    def fake_citing(wid, **kw):
        return [
            {"id": "Wc1", "title": "Independent cite", "year": 2026, "date": "2026-01-01",
             "cited_by_count": 1, "institutions": ["Harvard University"], "author_names": ["Alice"]},
            {"id": "Wc2", "title": "Self cite", "year": 2025, "date": "2025-06-01",
             "cited_by_count": 0, "institutions": [], "author_names": ["Stuart Rich"]},
        ]

    res = ingest_literature(store, cfg, today_year=2026, search_authors=fake_search,
                            author_works=fake_works, citing_works=fake_citing)
    assert res.authors_resolved == 1
    assert res.publications == 1
    assert res.citations == 2 and res.independent_citations == 1
    assert res.by_independence["independent"] == 1 and res.by_independence["self"] == 1

    # founder resolution persisted
    f = store.founders_for("cik:1")[0]
    assert f["openalex_author_id"] == "A1" and f["foundational_work_id"] == "Wfound"
    # signals persisted on the company
    sigs = store.signals_for("cik:1")
    kinds = {s["raw_payload"]["kind"] for s in sigs}
    assert kinds == {"publication", "citation"}
    indep = [s for s in sigs if s["raw_payload"].get("independence") == "independent"]
    assert indep and indep[0]["raw_payload"]["citing_institutions"] == ["Harvard University"]


def test_unresolved_author_is_stamped_not_retried(store, cfg):
    from early_detection.signals.author_resolution import FallbackResult
    _seed(store)
    # inject a no-match fallback so the test stays fully offline (the real one would hit Crossref)
    res = ingest_literature(store, cfg, search_authors=lambda name, **kw: [],
                            author_works=lambda *a, **k: [], citing_works=lambda *a, **k: [],
                            resolve_fallback=lambda name, hints: FallbackResult(source="none"))
    assert res.authors_resolved == 0
    # genuine no-match across primary + fallback → stamped done (not retried)
    assert store.founders_for("cik:1")[0]["literature_at"] is not None
    assert store.founders_for("cik:1")[0]["literature_at"] is not None      # stamped
    assert len(store.founders_for_literature()) == 0                        # not retried


def test_throttled_author_search_is_not_stamped(store, cfg):
    # Regression: search_authors returns None when the FETCH failed (OpenAlex 429 after retries) vs []
    # for a genuine no-match. A None must NOT stamp the founder — else only_missing drops it forever and
    # a post-cooldown re-run can't recover it (this is how 147 founders got stuck).
    _seed(store)
    res = ingest_literature(store, cfg, search_authors=lambda name, **kw: None,
                            author_works=lambda *a, **k: [], citing_works=lambda *a, **k: [])
    assert res.authors_resolved == 0
    assert store.founders_for("cik:1")[0]["literature_at"] is None          # NOT stamped
    assert len(store.founders_for_literature()) == 1                        # still retryable


def test_ingest_idempotent(store, cfg):
    _seed(store)
    fs = lambda name, **kw: openalex.parse_authors(_AUTHORS)
    fw = lambda aid, **kw: [{"id": "Wpub", "title": "p", "year": 2025, "date": "2025-01-01",
                             "cited_by_count": 1, "institutions": [], "author_names": []}]
    fc = lambda wid, **kw: []
    ingest_literature(store, cfg, today_year=2026, search_authors=fs, author_works=fw, citing_works=fc)
    # re-run: founder already resolved (literature_at set) → nothing to do
    res2 = ingest_literature(store, cfg, today_year=2026, search_authors=fs, author_works=fw, citing_works=fc)
    assert res2.founders == 0
