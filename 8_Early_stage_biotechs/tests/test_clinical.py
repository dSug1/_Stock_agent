"""M16 — clinical-trials signal (§3.2) offline tests (injected CT.gov client; no network)."""

from __future__ import annotations

import pytest

from early_detection.clients import clinicaltrials as ct
from early_detection.config import Config
from early_detection.models import Entity, SignalRecord
from early_detection.signals.clinical import _core, _sponsor_role, ingest_clinical
from early_detection.store import Store


def _raw(nct, lead, phases, status="RECRUITING", collaborators=None, title="t", start="2024-01-01"):
    """A nested CT.gov v2 study payload (what the API returns, before parse_studies)."""
    return {"protocolSection": {
        "identificationModule": {"nctId": nct, "briefTitle": title},
        "statusModule": {"overallStatus": status, "startDateStruct": {"date": start}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": lead},
                                       "collaborators": [{"name": c} for c in (collaborators or [])]},
        "designModule": {"phases": phases}}}


def _study(nct, lead, phases, status="RECRUITING", collaborators=None, title="t", start="2024-01-01"):
    """A PARSED (flat) study — what search_studies yields and ingest_clinical/_sponsor_role consume."""
    return ct.parse_studies({"studies": [_raw(nct, lead, phases, status, collaborators, title, start)]})[0]


# ── parser ─────────────────────────────────────────────────────────────────────
def test_parse_studies_flattens_and_ranks():
    payload = {"studies": [_raw("NCT01", "Acrivon Therapeutics", ["PHASE2"], collaborators=["Moffitt"])]}
    s = ct.parse_studies(payload)[0]
    assert s["nct_id"] == "NCT01" and s["lead_sponsor"] == "Acrivon Therapeutics"
    assert s["phase_rank"] == ct.PHASE_RANK["PHASE2"] and s["collaborators"] == ["Moffitt"]
    assert s["status"] == "RECRUITING" and s["start_date"] == "2024-01-01"


def test_phase_rank_takes_furthest():
    assert ct.phase_rank(["PHASE1", "PHASE2"]) == ct.PHASE_RANK["PHASE2"]
    assert ct.phase_rank([]) == 0 and ct.phase_rank(None) == 0


# ── sponsor matching precision ───────────────────────────────────────────────────
def test_core_strips_suffixes():
    assert _core("Acrivon Therapeutics, Inc.") == "acrivon"
    assert _core("TScan Therapeutics, Inc.") == "tscan"


def test_sponsor_role_lead_collaborator_and_nomatch():
    cores = [_core("Acrivon Therapeutics")]
    assert _sponsor_role(cores, _study("N", "Acrivon Therapeutics Inc", ["PHASE1"])) == "lead"
    assert _sponsor_role(cores, _study("N", "Moffitt Cancer Center", ["PHASE2"],
                                       collaborators=["Acrivon Therapeutics"])) == "collaborator"
    assert _sponsor_role(cores, _study("N", "Pfizer", ["PHASE3"])) is None


def test_sponsor_role_short_core_guard():
    # a 2-char core must not substring-match a random sponsor (the _MIN_CORE guard)
    assert _sponsor_role([_core("Co")], _study("N", "Moderna", ["PHASE1"])) is None


# ── ingest + evidence ────────────────────────────────────────────────────────────
@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "c.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "c.db", clinical_max_studies=100)


def _seed(store):
    store.upsert_entity(Entity(entity_id="cik:1", legal_name="Acrivon Therapeutics", ticker_primary="ACRV",
                               jurisdiction="US", in_existing_universe=True))


def test_ingest_matches_only_own_trials(store, cfg):
    _seed(store)
    studies = [
        _study("NCT01", "Acrivon Therapeutics", ["PHASE2"]),                       # lead → matched
        _study("NCT02", "Moffitt Cancer Center", ["PHASE2"], collaborators=["Acrivon Therapeutics"]),  # collaborator
        _study("NCT03", "Unrelated Pharma", ["PHASE3"]),                           # no match → dropped
    ]
    res = ingest_clinical(store, cfg, search=lambda name: studies)
    assert res.entities == 1 and res.with_trials == 1
    assert res.signals == 2 and res.lead == 1                                      # NCT03 excluded
    roles = {s["raw_payload"]["nct_id"]: s["raw_payload"]["role"]
             for s in store.signals_for("cik:1")}
    assert roles == {"NCT01": "lead", "NCT02": "collaborator"}


def test_ingest_idempotent_and_fetch_failure_skips(store, cfg):
    _seed(store)
    studies = [_study("NCT01", "Acrivon Therapeutics", ["PHASE2"])]
    ingest_clinical(store, cfg, search=lambda name: studies)
    ingest_clinical(store, cfg, search=lambda name: studies)                       # re-run
    assert store.count_signals("clinical_trial") == 1                              # idempotent (stable id)
    # a fetch failure (None) must not write anything / not crash
    res = ingest_clinical(store, cfg, search=lambda name: None)
    assert res.signals == 0 and res.with_trials == 0


def test_evidence_summary_clinical_aggregate(store, cfg):
    _seed(store)
    studies = [_study("NCT01", "Acrivon Therapeutics", ["PHASE2"], status="RECRUITING"),
               _study("NCT02", "Acrivon Therapeutics", ["PHASE1"], status="COMPLETED"),
               _study("NCT03", "Moffitt", ["PHASE3"], status="COMPLETED",
                      collaborators=["Acrivon Therapeutics"])]
    ingest_clinical(store, cfg, search=lambda name: studies)
    clin = store.evidence_summary("cik:1")["clinical_trials"]
    assert clin["trial_count"] == 3 and clin["as_lead"] == 2
    assert clin["active_trials"] == 1                                              # only NCT01 recruiting
    assert clin["highest_phase"] == "PHASE3"                                       # collaborator NCT03
    assert clin["highest_phase_as_lead"] == "PHASE2"                               # best of the two led


def test_stalled_trials_excluded_from_phase_and_prefilter(store, cfg):
    # A company-led Phase-2 trial that was WITHDRAWN must NOT count as de-risking evidence: it can't set
    # highest_phase, and it can't admit the name through the clinical pre-filter widening (the TScan
    # "8 trials" case — several are withdrawn/unknown, not running).
    store.upsert_entity(Entity(entity_id="cik:5", legal_name="Stall Bio", ticker_primary="STL",
                               jurisdiction="US"))
    from early_detection.models import SignalRecord as SR
    store.insert_signal(SR(signal_id="cap5", entity_id="cik:5", signal_type="capital_markets",
                           source="edgar", raw_payload={"form": "8-K"}))
    ingest_clinical(store, cfg, search=lambda name: [
        _study("NCT_w", "Stall Bio", ["PHASE2"], status="WITHDRAWN"),
        _study("NCT_u", "Stall Bio", ["PHASE2"], status="UNKNOWN"),
        _study("NCT_t", "Stall Bio", ["PHASE1"], status="TERMINATED")])
    clin = store.evidence_summary("cik:5")["clinical_trials"]
    assert clin["trial_count"] == 3 and clin["stalled_trials"] == 2                # WITHDRAWN + TERMINATED
    assert clin["active_trials"] == 0 and clin["completed_trials"] == 0
    assert clin["highest_phase"] is None and clin["highest_phase_as_lead"] is None  # nothing meaningful
    # clinical widening must NOT admit it — the Phase-2 trial is withdrawn, not live
    assert store.scoring_candidates("v1", min_independent=1, force=True, clinical_min_phase=2) == []


def test_prefilter_clinical_widening(store, cfg):
    # entity with a company-led Phase 2 trial + a capital signal but NO independent citation
    store.upsert_entity(Entity(entity_id="cik:9", legal_name="ClinCo Therapeutics", ticker_primary="CLNC",
                               jurisdiction="US"))
    store.insert_signal(SignalRecord(signal_id="cap9", entity_id="cik:9", signal_type="capital_markets",
                                     source="edgar", raw_payload={"form": "8-K"}))
    ingest_clinical(store, cfg, search=lambda name: [_study("NCT9", "ClinCo Therapeutics", ["PHASE2"])])
    # default (clinical gate OFF) → does NOT clear (no independent citation)
    assert store.scoring_candidates("v1", min_independent=1, force=True) == []
    # opt-in clinical widening at min phase 2 → clears via the company-led Phase 2 trial
    got = store.scoring_candidates("v1", min_independent=1, force=True, clinical_min_phase=2)
    assert [e.entity_id for e in got] == ["cik:9"]
    # a higher bar (phase 3) excludes it again
    assert store.scoring_candidates("v1", min_independent=1, force=True, clinical_min_phase=3) == []
