"""M11 — stack-convergence scoring offline tests (fake Claude client; NO real API/spend)."""

from __future__ import annotations

import pytest

from early_detection.config import Config
from early_detection.models import Entity, SignalRecord
from early_detection.scoring import SCHEMA, SYSTEM_PROMPT, score_candidates, write_digest
from early_detection.store import Store


class FakeClient:
    def __init__(self, results):
        self.results = results
        self.spent_usd = 0.0
        self.web_searches = 0

    def submit_batch(self, model, system, schema, bundles, max_tokens, *, tools=None):
        self._bundles = dict(bundles)
        return "sbatch_1"

    def collect_batch(self, batch_id, model, *, validate=None, on_result=None, **kw):
        out = {}
        for cid in self._bundles:
            data = self.results.get(cid)
            if data is None:
                continue
            if validate:
                data = validate(data)
            out[cid] = data
            if on_result:
                on_result(cid, data)
        return out

    def run_realtime_many(self, model, system, schema, items, max_tokens, *, validate=None,
                          tools=None, concurrency=4, on_result=None, **kw):
        out = {}
        for cid in items:
            data = self.results.get(cid)
            if data is None:
                continue
            if validate:
                data = validate(data)
            out[cid] = data
            if on_result:
                on_result(cid, data)
        return out


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "sc.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "sc.db", scoring_prompt_version="v1", scoring_model="claude-sonnet-5",
                  cost_calibration_factor=0.1, prefilter_min_independent=1)


def _cite(eid, indep, i):
    return SignalRecord(signal_id=f"lit_{eid}_{i}", entity_id=eid, signal_type="literature",
                        source="openalex",
                        raw_payload={"kind": "citation", "independence": indep, "work_id": f"W{i}"})


def _seed(store):
    # QUAL: 2 independent citations + a capital signal → clears the pre-filter
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Qual Bio", ticker_primary="QUAL",
                               jurisdiction="US", in_existing_universe=True))
    store.insert_signal(_cite("cik:1", "independent", 1))
    store.insert_signal(_cite("cik:1", "independent", 2))
    store.insert_signal(SignalRecord(signal_id="own_1", entity_id="cik:1", signal_type="ownership_crossing",
                                     source="edgar_fts", raw_payload={"fund": "RA Capital", "form": "SCHEDULE 13D"}))
    store.save_founders("cik:1", founders=[{"name": "A Founder", "role": "CSO", "institution": "MIT",
                                            "is_company_officer": True}], affiliations=["MIT"],
                        prompt_version="v1")
    # NOCITE: capital signal but NO independent citation → excluded
    store.upsert_entity(Entity(entity_id="cik:2", legal_name="NoCite Bio", ticker_primary="NOCT",
                               jurisdiction="US"))
    store.insert_signal(SignalRecord(signal_id="cap_2", entity_id="cik:2", signal_type="capital_markets",
                                     source="edgar_submissions", raw_payload={"form": "8-K"}))
    # NOCAP: independent citation but NO capital/ownership signal → excluded
    store.upsert_entity(Entity(entity_id="cik:3", legal_name="NoCap Bio", ticker_primary="NCAP",
                               jurisdiction="US"))
    store.insert_signal(_cite("cik:3", "independent", 1))


def test_schema_and_prompt():
    assert set(SCHEMA["required"]) >= {"conviction_flag", "conviction_score", "independent_validation_status"}
    assert SCHEMA["properties"]["conviction_flag"]["enum"] == ["surveil", "deep-dive-candidate", "deprioritize"]
    assert "VARIANT PERCEPTION" in SYSTEM_PROMPT and "NEVER invent" in SYSTEM_PROMPT


def test_prefilter_selects_only_cornered_candidates(store, cfg):
    _seed(store)
    cands = {e.entity_id for e in store.scoring_candidates("v1", min_independent=1)}
    assert cands == {"cik:1"}          # NoCite (no independent cite) + NoCap (no capital signal) excluded


def test_evidence_summary_aggregates(store, cfg):
    _seed(store)
    ev = store.evidence_summary("cik:1")
    assert ev["literature"]["independent_citations"] == 2
    assert ev["specialist_fund_crossings"] == ["RA Capital"]
    assert ev["founders"][0]["name"] == "A Founder"


def test_score_persists_and_ranks(store, cfg):
    _seed(store)
    fake = FakeClient({"cik:1": {
        "mechanism_summary": "Novel MOA [V]", "independent_validation_status": "multi-lab-independent",
        "stack_convergence_dimensions": ["independent citations", "specialist fund"],
        "base_rate_context": "most fail", "conviction_flag": "deep-dive-candidate",
        "conviction_score": 82, "confidence_caveats": ["small n"]}})
    res = score_candidates(store, cfg, client=fake)
    assert res.candidates == 1 and res.scored == 1 and res.deep_dive == 1
    top = store.top_scores("v1")
    assert top[0]["conviction_flag"] == "deep-dive-candidate" and top[0]["conviction_score"] == 82
    assert store.count_scores("v1") == 1


def test_score_skip_cache_unless_force(store, cfg):
    _seed(store)
    fake = FakeClient({"cik:1": {"mechanism_summary": "x", "independent_validation_status": "none",
                                 "stack_convergence_dimensions": [], "base_rate_context": "",
                                 "conviction_flag": "surveil", "conviction_score": 55, "confidence_caveats": []}})
    score_candidates(store, cfg, client=fake)
    assert len(store.scoring_candidates("v1", min_independent=1)) == 0          # already scored
    assert len(store.scoring_candidates("v1", min_independent=1, force=True)) == 1
    assert len(store.scoring_candidates("v2", min_independent=1)) == 1          # new prompt re-opens


def test_write_digest(store, cfg, tmp_path):
    _seed(store)
    fake = FakeClient({"cik:1": {"mechanism_summary": "Novel MOA", "independent_validation_status": "multi-lab-independent",
                                 "stack_convergence_dimensions": ["citations"], "base_rate_context": "most fail",
                                 "conviction_flag": "deep-dive-candidate", "conviction_score": 82,
                                 "confidence_caveats": []}})
    score_candidates(store, cfg, client=fake)
    out = tmp_path / "digest.md"
    n = write_digest(store, cfg, out)
    assert n == 1
    text = out.read_text(encoding="utf-8")
    assert "QUAL" in text and "deep-dive-candidate" in text and "existing-universe" in text
