"""M4 tests — evidence clients (parsers) + Stage 2 harvest (incremental, fail-open)."""

import pytest

from platform_discoverer import stage2
from platform_discoverer.clients import _net, clinicaltrials, openalex, patentsview
from platform_discoverer.models import Company
from platform_discoverer.store import Store


# ── name cleaning ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,clean", [
    ("Acrivon Therapeutics, Inc.", "Acrivon Therapeutics"),
    ("Boundless Bio, Inc.", "Boundless Bio"),
    ("Genmab A/S", "Genmab"),
    ("Foo Holdings Ltd", "Foo"),
])
def test_clean_name(raw, clean):
    assert _net.clean_name(raw) == clean


# ── OpenAlex ─────────────────────────────────────────────────────────────────

def test_openalex_parse_institution_matches_name():
    payload = {"results": [{"display_name": "Acrivon Therapeutics", "id": "I123",
                            "works_count": 40, "cited_by_count": 1200,
                            "summary_stats": {"h_index": 18, "i10_index": 25,
                                              "2yr_mean_citedness": 4.1},
                            "x_concepts": [{"display_name": "Phosphoproteomics", "score": 80},
                                           {"display_name": "Kinase", "score": 60}],
                            "updated_date": "2026-05-01"}]}
    s = openalex.parse_institution(payload, "Acrivon Therapeutics, Inc.")
    assert s["works_count"] == 40 and s["h_index"] == 18
    assert "Phosphoproteomics" in s["top_concepts"]


def test_openalex_rejects_wrong_name_match():
    # a same-search-rank university must not be accepted as the company
    payload = {"results": [{"display_name": "University of Relay Sciences", "works_count": 9000}]}
    assert openalex.parse_institution(payload, "Acrivon Therapeutics") is None


# ── ClinicalTrials ───────────────────────────────────────────────────────────

def test_ctgov_parse_studies():
    payload = {"totalCount": 3, "studies": [
        {"protocolSection": {
            "designModule": {"phases": ["PHASE2"]},
            "conditionsModule": {"conditions": ["Ovarian Cancer"]},
            "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-03-10"}},
            "descriptionModule": {"briefSummary": "biomarker-defined patient selection"}}},
        {"protocolSection": {
            "designModule": {"phases": ["PHASE1"]},
            "conditionsModule": {"conditions": ["Solid Tumor"]},
            "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-06-01"}}}}]}
    s = clinicaltrials.parse_studies(payload)
    assert s["trial_count"] == 3
    assert set(s["phases"]) == {"PHASE1", "PHASE2"}
    assert s["biomarker_or_cdx_language"] is True
    assert s["latest_update"] == "2026-06-01"


# ── PatentsView ──────────────────────────────────────────────────────────────

def test_patentsview_parse_buckets_method_vs_composition():
    payload = {"total_hits": 4, "patents": [
        {"patent_title": "Method for kinase activity inference", "patent_date": "2025-01-01"},
        {"patent_title": "System and platform for phosphoproteomic screening", "patent_date": "2025-06-01"},
        {"patent_title": "Composition of a WEE1 inhibitor compound", "patent_date": "2024-02-01"}]}
    s = patentsview.parse_patents(payload)
    assert s["method_platform_titles"] == 2 and s["composition_titles"] == 1
    assert s["latest_date"] == "2025-06-01"


def test_patentsview_inert_without_key():
    assert patentsview.fetch("Acrivon Therapeutics", api_key=None) is None


# ── pedigree (OpenAlex authors + prestige match) ─────────────────────────────

def test_openalex_parse_authors():
    payload = {"results": [
        {"display_name": "Carolyn Bertozzi", "works_count": 600,
         "summary_stats": {"h_index": 150}},
        {"display_name": "Jane Doe", "works_count": 30, "summary_stats": {"h_index": 12}}]}
    a = openalex.parse_authors(payload)
    assert a[0]["name"] == "Carolyn Bertozzi" and a[0]["h_index"] == 150


def test_openalex_parse_authors_from_works_fallback():
    # the no-institution fallback: tally distinct authors across works (by affiliation string)
    payload = {"results": [
        {"authorships": [{"author": {"display_name": "Carolyn Bertozzi"}},
                         {"author": {"display_name": "Jane Doe"}}]},
        {"authorships": [{"author": {"display_name": "Carolyn Bertozzi"}},
                         {"author": {"display_name": ""}}]}]}
    a = openalex.parse_authors_from_works(payload)
    assert a[0]["name"] == "Carolyn Bertozzi" and a[0]["works_count"] == 2   # most frequent first
    assert a[0]["via"] == "works_affiliation"
    assert all(x["name"] for x in a)                                         # blank author dropped
    assert {x["name"] for x in a} == {"Carolyn Bertozzi", "Jane Doe"}


def test_prestige_match_exact_only():
    from platform_discoverer import prestige
    idx = prestige.build_index({"awardees": [
        {"name": "Carolyn Bertozzi", "recognition": "Nobel 2022"},
        {"name": "David Baker", "recognition": "Nobel 2024"}]})
    hits = prestige.match(["Carolyn Bertozzi", "John Q. Random", "david baker"], idx)
    assert {h["name"] for h in hits} == {"Carolyn Bertozzi", "David Baker"}
    # a partial/surname-only must NOT match (precision)
    assert prestige.match(["Baker"], idx) == []


# ── FDA designation scan ─────────────────────────────────────────────────────

def test_scan_fda_designations():
    txt = "granted Breakthrough Therapy designation and Orphan Drug status by the FDA"
    assert set(clinicaltrials.scan_designations(txt)) == {"Breakthrough Therapy", "Orphan Drug"}
    assert clinicaltrials.scan_designations("a generic biotech") == []


def test_ctgov_parse_extracts_designations():
    payload = {"totalCount": 1, "studies": [{"protocolSection": {
        "descriptionModule": {"briefSummary": "received FDA Breakthrough Device designation"}}}]}
    assert "Breakthrough Device" in clinicaltrials.parse_studies(payload)["fda_designations"]


# ── Stage 2 orchestration ────────────────────────────────────────────────────

CONFIG = {"stage2": {"sources": ["openalex", "ctgov"], "incremental_ttl_days": 7},
          "stage1_filters": {"include_excluded": False}}


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db")
    yield s
    s.close()


def _seed(store):
    store.upsert_company(Company(company_id="c1", name="Acrivon Therapeutics", primary_ticker="ACRV"))
    store.upsert_company(Company(company_id="c2", name="Excluded Co", primary_ticker="EXC",
                                 stage1_excluded=True))


def test_stage2_harvests_retained_skips_excluded(monkeypatch, store):
    _seed(store)
    monkeypatch.setattr(stage2.openalex, "fetch",
                        lambda name, **k: ({"works_count": 10}, "2026-01-01"))
    monkeypatch.setattr(stage2.clinicaltrials, "fetch",
                        lambda name, **k: ({"trial_count": 2}, "2026-02-01"))
    summary = stage2.run(store, CONFIG, run_id="r")
    assert summary["harvested"] == 2                  # c1 × 2 sources; c2 excluded
    assert store.get_evidence("c1", "openalex")["cursor"] == "2026-01-01"
    assert store.get_evidence("c2", "openalex") is None
    assert summary["companies"] == 1


def test_stage2_include_excluded_harvests_excluded(monkeypatch, store):
    _seed(store)   # c2 is stage1_excluded
    monkeypatch.setattr(stage2.openalex, "fetch", lambda name, **k: ({"works_count": 5}, "x"))
    monkeypatch.setattr(stage2.clinicaltrials, "fetch", lambda name, **k: None)
    summary = stage2.run(store, CONFIG, run_id="r", include_excluded=True)
    assert summary["companies"] == 2                  # both c1 and the excluded c2
    assert store.get_evidence("c2", "openalex") is not None


def test_stage2_incremental_skips_fresh(monkeypatch, store):
    _seed(store)
    calls = {"n": 0}

    def oa(name, **k):
        calls["n"] += 1
        return ({"works_count": 10}, "2026-01-01")

    monkeypatch.setattr(stage2.openalex, "fetch", oa)
    monkeypatch.setattr(stage2.clinicaltrials, "fetch", lambda name, **k: None)
    stage2.run(store, CONFIG, run_id="r1")            # harvests c1/openalex
    n_after_first = calls["n"]
    summary = stage2.run(store, CONFIG, run_id="r2")  # fresh -> skip re-fetch
    assert calls["n"] == n_after_first                # not called again
    assert summary["skipped_fresh"] >= 1


def test_stage2_failopen_on_client_error(monkeypatch, store):
    _seed(store)

    def boom(name, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(stage2.openalex, "fetch", boom)
    monkeypatch.setattr(stage2.clinicaltrials, "fetch", lambda name, **k: ({"trial_count": 1}, "x"))
    summary = stage2.run(store, CONFIG, run_id="r")   # must not raise
    assert summary["harvested"] == 1                  # ctgov still harvested
