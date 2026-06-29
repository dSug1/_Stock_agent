"""M5 tests — rubric bundle/validation, composite math, and Stage-4 tier routing (mocked client)."""

from pathlib import Path

import pytest

from platform_discoverer import config as cfg
from platform_discoverer import stage4
from platform_discoverer.models import Company, Evidence
from platform_discoverer.scoring import composite, rubric
from platform_discoverer.store import Store

ROOT = Path(__file__).parent.parent
CONFIG = cfg.load_config(ROOT / "config" / "config.yaml")
TAXONOMY = cfg.load_taxonomy(ROOT / "config" / "taxonomy.yaml")


def _good_rubric(**over):
    base = {
        "company": "Acrivon", "ticker": "ACRV",
        "A_proprietary_data": {"score": 5, "modality": "phosphoproteomics", "scale_evidence": "120k",
                               "citation": "C053"},
        "B_compute_engine": {"score": 3, "is_foundation_model": True, "generative_evidence": "ESM-2",
                             "citation": "C053"},
        "C_validation": {"score": 5, "validation_type": "wetlab-loop", "citation": "InViKA"},
        "D_mechanism": {"score": 4, "mechanism_ids": ["adp_ribosylation_parp_tankyrase"],
                        "branch": "oncology", "disruption_rationale": "replication stress"},
        "E_translation": {"score": 5, "companion_dx": True, "lead_phase": "PHASE2", "citation": "x"},
        "moat_location": {"data_vs_architecture": "data", "rationale": "withheld dataset"},
        "substance_check": {"verdict": "substantive", "disconfirming_evidence": "company-authored"},
        "memo": "strong",
    }
    base.update(over)
    return base


# ── bundle ───────────────────────────────────────────────────────────────────

def test_build_bundle_assembles_evidence():
    c = Company(company_id="c1", name="Acrivon Therapeutics", primary_ticker="ACRV",
                business_description="phosphoproteomics platform", ta_tags=["menin_kmt2a"])
    ev = {"ctgov": {"trial_count": 3, "phases": ["PHASE2"], "biomarker_or_cdx_language": True}}
    b = rubric.build_bundle(c, ev)
    assert b["ticker"] == "ACRV" and b["stage1_mechanism_tags"] == ["menin_kmt2a"]
    assert "publications" not in b and "pedigree" not in b   # D9: web-researched, not pre-harvested
    assert b["clinical"]["biomarker_or_companion_dx_language"] is True


def test_rubric_system_prompt_includes_vocab_and_pedigree_guidance():
    s = rubric.build_rubric_system(TAXONOMY)
    assert "adp_ribosylation_parp_tankyrase" in s and "Acrivon" in s
    assert "PEDIGREE" in s and "FDA" in s            # weighs founder pedigree + FDA breakthroughs


def test_rubric_system_prompt_has_web_research_and_fairness():
    s = rubric.build_rubric_system(TAXONOMY)
    assert "DATA-COVERAGE FAIRNESS" in s
    assert "web_search" in s and "pedigree" in s     # D9: research publications + pedigree via web


def test_rubric_system_prompt_injects_prestige_list():
    s = rubric.build_rubric_system(TAXONOMY, {"awardees": [
        {"name": "Carolyn Bertozzi", "recognition": "Nobel 2022"}]})
    assert "PRESTIGE LIST" in s and "Carolyn Bertozzi" in s


def test_build_bundle_data_coverage_note_when_signals_absent():
    c = Company(company_id="c1", name="Tiny Bio", primary_ticker="TINY",
                business_description="proprietary screening platform")
    # only ctgov present → publications/pedigree/patent_estate are coverage gaps
    b = rubric.build_bundle(c, {"ctgov": {"trial_count": 1}})
    note = b.get("data_coverage_note", "")
    assert "publications" in note and "pedigree" in note and "patent_estate" in note
    assert "ABSENT DATA" in note


def test_build_bundle_no_coverage_note_when_patents_present():
    c = Company(company_id="c1", name="Full Bio", primary_ticker="FULL")
    b = rubric.build_bundle(c, {"patents": {"patent_count": 3}, "ctgov": {"trial_count": 2}})
    assert "data_coverage_note" not in b   # patents present → no gap note (D9)


def test_build_bundle_includes_patents_and_fda():
    c = Company(company_id="c1", name="Acrivon Therapeutics", primary_ticker="ACRV",
                business_description="granted FDA Breakthrough Device designation")
    ev = {"patents": {"patent_count": 12, "method_platform_titles": 8, "composition_titles": 2}}
    b = rubric.build_bundle(c, ev)
    assert b["patent_estate"]["method_platform_titles"] == 8
    assert "Breakthrough Device" in b["fda_designations"]      # scanned from the description
    assert "pedigree" not in b                                 # D9: pedigree is web-researched


def test_build_bundle_coverage_note_when_patents_absent():
    c = Company(company_id="c1", name="Tiny Bio", primary_ticker="TINY",
                business_description="platform")
    b = rubric.build_bundle(c, {"ctgov": {"trial_count": 1}})   # no patents
    assert "patent_estate NOT fetched" in b["data_coverage_note"]
    assert "web_search" in b["data_coverage_note"]


# ── validation ───────────────────────────────────────────────────────────────

def test_validate_rubric_accepts_good():
    assert rubric.validate_rubric(_good_rubric())["ticker"] == "ACRV"


@pytest.mark.parametrize("bad", [
    {"A_proprietary_data": {"score": 9, "modality": "", "scale_evidence": "", "citation": ""}},  # >5
    {"moat_location": {"data_vs_architecture": "nonsense", "rationale": ""}},
    {"substance_check": {"verdict": "hype", "disconfirming_evidence": ""}},
])
def test_validate_rubric_rejects_bad(bad):
    with pytest.raises(rubric.RubricError):
        rubric.validate_rubric(_good_rubric(**bad))


def test_validate_rubric_rejects_missing_axis():
    r = _good_rubric()
    del r["C_validation"]
    with pytest.raises(rubric.RubricError):
        rubric.validate_rubric(r)


# ── composite ────────────────────────────────────────────────────────────────

def test_composite_weighted_and_normalized():
    w = CONFIG["composite_weights"]
    pen = CONFIG["penalties"]
    comp = composite.compute_composite(_good_rubric(), w, pen)
    # A5 B3 C5 D4 E5 (data moat, substantive) -> high but not 1.0
    assert 0.7 < comp < 1.0


def test_composite_architecture_penalty():
    w, pen = CONFIG["composite_weights"], CONFIG["penalties"]
    data = composite.compute_composite(_good_rubric(), w, pen)
    arch = composite.compute_composite(
        _good_rubric(moat_location={"data_vs_architecture": "architecture", "rationale": ""}), w, pen)
    assert arch == pytest.approx(data * pen["architecture_moat_factor"], abs=1e-3)


def test_composite_marketing_penalty():
    w, pen = CONFIG["composite_weights"], CONFIG["penalties"]
    mk = composite.compute_composite(
        _good_rubric(substance_check={"verdict": "marketing", "disconfirming_evidence": ""}), w, pen)
    clean = composite.compute_composite(_good_rubric(), w, pen)
    assert mk == pytest.approx(clean * pen["marketing_verdict_factor"], abs=1e-3)


def test_confidence_rises_with_evidence_and_finalize():
    r = _good_rubric()
    assert composite.compute_confidence(r, evidence_sources=0, finalized=False) < \
           composite.compute_confidence(r, evidence_sources=3, finalized=True)


# ── cost estimate ────────────────────────────────────────────────────────────

def test_estimate_cost_scales_and_bounds():
    est = stage4.estimate_cost(589, CONFIG)
    assert est["candidates"] == 589
    assert est["tiers"]["triage_haiku"]["companies"] == 589
    assert est["est_total_usd"] < est["max_usd_per_run"]   # well under the $50 cap
    assert est["est_total_usd"] < stage4.estimate_cost(2000, CONFIG)["est_total_usd"]


def test_estimate_includes_web_search_when_enabled():
    est = stage4.estimate_cost(100, CONFIG)               # config has web_research.enabled: true
    assert "web_search" in est["tiers"] and est["tiers"]["web_search"]["usd"] > 0


def test_web_search_tool_shape():
    from platform_discoverer.clients.anthropic_client import web_search_tool
    t = web_search_tool(5)
    assert t["type"] == "web_search_20260209" and t["name"] == "web_search" and t["max_uses"] == 5


# ── Stage 4 orchestration with a fake client ────────────────────────────────

class FakeClient:
    """Mimics AnthropicClient: triage keeps tickers starting with 'A'; rubric scores by ticker."""

    def __init__(self):
        self.spent_usd = 1.23
        self.calls = 0

    def score_realtime(self, model, system, schema, bundle, max_tokens, *, validate=None,
                       max_retries=3, tools=None, max_pause_turns=4):
        self.calls += 1
        if schema is rubric.TRIAGE_SCHEMA:
            keep = (bundle.get("ticker") or "").startswith("A")
            return {"keep": keep, "prior": 0.6 if keep else 0.1, "reason": "fake"}
        return _good_rubric(ticker=bundle.get("ticker"))      # finalize re-score

    def score_batch(self, model, system, schema, bundles, max_tokens, *, validate=None, tools=None,
                    **kw):
        self.calls += 1
        # one in-band (contested) and one clearly high, by ticker
        out = {}
        for cid, b in bundles.items():
            if (b.get("ticker") or "") == "ACON":             # contested -> low scores
                out[cid] = _good_rubric(ticker="ACON",
                                        A_proprietary_data={"score": 3, "modality": "", "scale_evidence": "", "citation": ""},
                                        C_validation={"score": 3, "validation_type": "", "citation": ""})
            else:
                out[cid] = _good_rubric(ticker=b.get("ticker"))
        return out


@pytest.fixture
def store(tmp_path):
    s = Store.open(tmp_path / "store.db", config=CONFIG)
    yield s
    s.close()


def _seed_scoring(store):
    for cid, tk in [("a1", "ACRV"), ("a2", "ACON"), ("x1", "XYZZ")]:
        store.upsert_company(Company(company_id=cid, name=f"{tk} Co", primary_ticker=tk,
                                     business_description="platform", source_nets=["sector"]))
        store.upsert_evidence(Evidence(company_id=cid, source="ctgov", payload={"trial_count": 2}))


def test_stage4_estimate_mode_no_dispatch(store):
    _seed_scoring(store)
    out = stage4.run(store, CONFIG, dispatch=False)
    assert out["mode"] == "estimate" and out["candidates"] == 3


def test_rescore_ttl_skips_recently_scored(store):
    from platform_discoverer.models import Score
    from platform_discoverer.store import now_iso
    _seed_scoring(store)                                  # a1/a2/x1
    store.record_score(Score(company_id="a1", run_id=now_iso(), model="claude-sonnet-4-6",
                             composite=0.8), run_id=now_iso())
    # a1 scored just now -> excluded from due candidates; a2/x1 still due
    due = {c.company_id for c in stage4._candidates(store, CONFIG)}
    assert "a1" not in due and {"a2", "x1"} <= due
    # --force-rescore re-includes a1 (the reset)
    assert "a1" in {c.company_id for c in stage4._candidates(store, CONFIG, force=True)}


def test_rescore_ttl_expired_is_due(store):
    from platform_discoverer.models import Score
    _seed_scoring(store)
    store.record_score(Score(company_id="a1", run_id="2020-01-01T00:00:00+00:00",
                             model="x", composite=0.8), run_id="2020-01-01T00:00:00+00:00")
    assert "a1" in {c.company_id for c in stage4._candidates(store, CONFIG)}   # >1yr ago -> due


def test_stage4_dispatch_tiers_and_persists(store):
    _seed_scoring(store)
    fake = FakeClient()
    summary = stage4.run(store, CONFIG, dispatch=True, client=fake, run_id="r1")
    # XYZZ killed at triage (not 'A'); ACRV + ACON scored; ACON is contested -> Opus finalized
    assert summary["triage_killed"] == 1
    assert summary["scored"] == 2
    assert summary["opus_finalized"] == 1
    assert summary["cost_usd"] == 1.23
    rows = {r["company_id"]: r["composite"] for r in
            store.conn.execute("SELECT company_id, composite FROM scores").fetchall()}
    assert set(rows) == {"a1", "a2"}            # XYZZ not scored
    # a triage-kill audit row exists for XYZZ
    assert store.conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='cut' AND reason='triage_kill'").fetchone()[0] == 1
