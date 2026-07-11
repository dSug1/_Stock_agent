"""M14 — §5.2 independence refinement offline tests (injected OpenAlex, no network)."""

from __future__ import annotations

import pytest

from early_detection.clients import openalex
from early_detection.config import Config
from early_detection.models import Entity, SignalRecord
from early_detection.signals.independence import classify, refine_independence
from early_detection.signals.literature import _sid
from early_detection.store import Store


# ── parser captures author ids + institution types ────────────────────────────
def test_parse_works_captures_ids_and_types():
    payload = {"results": [{"id": "https://openalex.org/W1", "publication_year": 2024,
                            "authorships": [{"author": {"id": "https://openalex.org/A9",
                                                        "display_name": "Dr X"},
                                             "institutions": [{"display_name": "Acme Pharma", "type": "company"}]}]}]}
    w = openalex.parse_works(payload)[0]
    assert w["author_ids"] == ["A9"] and w["institution_types"] == ["company"]


# ── classify precedence ───────────────────────────────────────────────────────
def test_classify_self():
    assert classify(founder_author_id="A1", coauthors=set(), hints=[],
                    citing={"author_ids": ["A1", "A2"]}) == "self"


def test_classify_collaborator_beats_independent():
    # citing author A2 is a former co-author of the founder → NOT independent, even w/ no institution match
    assert classify(founder_author_id="A1", coauthors={"A2"}, hints=["MIT"],
                    citing={"author_ids": ["A2"], "institutions": ["Harvard"]}) == "collaborator"


def test_classify_same_institution():
    assert classify(founder_author_id="A1", coauthors=set(), hints=["Northwestern University"],
                    citing={"author_ids": ["A5"], "institutions": ["Northwestern University Feinberg"]}) == "same_institution"


def test_classify_industry():
    assert classify(founder_author_id="A1", coauthors=set(), hints=["MIT"],
                    citing={"author_ids": ["A5"], "institutions": ["Some Biotech"],
                            "institution_types": ["company"]}) == "industry"


def test_classify_independent():
    assert classify(founder_author_id="A1", coauthors={"A2"}, hints=["MIT"],
                    citing={"author_ids": ["A9"], "institutions": ["Stanford"],
                            "institution_types": ["education"]}) == "independent"


# ── end-to-end refinement ─────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "i.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "i.db", literature_citation_years=6, literature_max_citing=100)


def _seed(store):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Tenax", cik="0000000001", jurisdiction="US"))
    store.save_founders("cik:1", founders=[{"name": "Stuart Rich", "role": "CMO",
                                            "institution": "Northwestern University", "is_company_officer": True}],
                        affiliations=[], prompt_version="v1")
    fid = store.founders_for("cik:1")[0]["id"]
    store.set_founder_literature(fid, author_id="A1", foundational_work_id="Wfound")
    # a pre-existing coarse M10 "independent" citation that refinement will DOWNGRADE to collaborator
    sid = _sid("cik:1", "Wc1", "citation", "Wfound")
    store.insert_signal(SignalRecord(signal_id=sid, entity_id="cik:1", signal_type="literature",
                                     source="openalex",
                                     raw_payload={"kind": "citation", "work_id": "Wc1", "independence": "independent"}))
    return fid


def test_refine_reclassifies_and_scores(store, cfg):
    fid = _seed(store)
    # M10 counted 1 independent citation
    assert store.evidence_summary("cik:1")["literature"]["independent_citations"] == 1

    citing = [
        # Wc1: authored by a former co-author (A2) → collaborator (downgrade from independent)
        {"id": "Wc1", "title": "collab cite", "year": 2024, "date": "2024-01-01",
         "author_ids": ["A2"], "institutions": ["Harvard"], "institution_types": ["education"]},
        # Wc2: genuine independent lab
        {"id": "Wc2", "title": "indep cite", "year": 2025, "date": "2025-01-01",
         "author_ids": ["A9"], "institutions": ["Stanford"], "institution_types": ["education"]},
    ]
    res = refine_independence(store, cfg, today_year=2025,
                              coauthor_ids=lambda aid, **kw: {"A2"},
                              citing_works=lambda wid, **kw: citing)
    assert res.refined == 1
    assert res.by_relationship["collaborator"] == 1 and res.by_relationship["independent"] == 1

    ev = store.evidence_summary("cik:1")["literature"]
    assert ev["independent_citations"] == 1        # Wc1 downgraded; only Wc2 truly independent
    assert ev["collaborator_citations"] == 1
    assert ev["independence_score"] is not None and ev["independence_score"] > 0

    f = store.founders_for("cik:1")[0]
    assert f["independence_score"] is not None
    # idempotent: refined founder skipped on re-run
    assert len(store.founders_for_independence()) == 0


def test_throttled_fetch_leaves_founder_unstamped_for_retry(store, cfg):
    # Regression: a 429 doesn't raise — the OpenAlex helpers swallow it and return empty. The refinement
    # must NOT stamp such a founder (score 0), or only_missing skips it forever. Empty coauthors + empty
    # citing (the throttle signature) ⇒ leave it for retry.
    fid = _seed(store)
    res = refine_independence(store, cfg, today_year=2025,
                              coauthor_ids=lambda aid, **kw: set(),   # both empty = looks throttled
                              citing_works=lambda wid, **kw: [])
    assert res.refined == 0
    assert store.founders_for("cik:1")[0]["independence_at"] is None   # unstamped → retryable
    assert len(store.founders_for_independence()) == 1                 # still in the work-list


def test_refine_only_resolved_founders(store, cfg):
    # a founder without a resolved author is not in the work-list
    store.upsert_entity(Entity(entity_id="cik:2", legal_name="NoAuthor Co", cik="0000000002", jurisdiction="US"))
    store.save_founders("cik:2", founders=[{"name": "Nobody", "role": "x", "institution": "y",
                                            "is_company_officer": False}], affiliations=[], prompt_version="v1")
    assert store.founders_for_independence() == []
