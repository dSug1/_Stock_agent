"""M15 — §9 validation harness offline tests (no network, no Claude, no spend)."""

from __future__ import annotations

import textwrap

import pytest

from early_detection.config import Config
from early_detection.models import Entity, SignalRecord
from early_detection.store import Store
from early_detection.validation import (
    Case, _norm_name, evaluate, load_cases, match_case, write_report,
)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "val.db")
    yield s
    s.close()


@pytest.fixture
def cfg(tmp_path):
    return Config(db_path=tmp_path / "val.db", scoring_prompt_version="v1",
                  scoring_model="claude-sonnet-5", prefilter_min_independent=1)


def _ent(store, eid, ticker=None, cik=None, name="Co", **kw):
    store.upsert_entity(Entity(entity_id=eid, legal_name=name, ticker_primary=ticker, cik=cik,
                               jurisdiction="US", **kw))


def _score(store, eid, flag, score, pv="v1"):
    store.save_score(entity_id=eid, run_id="r1", model="m", conviction_flag=flag,
                     conviction_score=score, data={"conviction_flag": flag, "conviction_score": score},
                     prompt_version=pv)


def _cite(eid, i, indep="independent"):
    return SignalRecord(signal_id=f"lit_{eid}_{i}", entity_id=eid, signal_type="literature",
                        source="openalex",
                        raw_payload={"kind": "citation", "independence": indep, "work_id": f"W{i}"})


def _cases_file(tmp_path, body: str):
    p = tmp_path / "cases.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# ── load_cases ───────────────────────────────────────────────────────────────────

def test_load_cases_parses_list_and_mapping(tmp_path):
    p = _cases_file(tmp_path, """
        cases:
          - name: Alpha
            ticker: ALPH
            label: positive
            aliases: [AlphaBio, "ALPH.V"]
            verified: true
          - name: Beta
            label: negative
    """)
    cases = load_cases(p)
    assert [c.name for c in cases] == ["Alpha", "Beta"]
    assert cases[0].is_positive and cases[0].aliases == ("AlphaBio", "ALPH.V")
    assert cases[0].verified and not cases[1].is_positive


def test_load_cases_rejects_bad_label(tmp_path):
    p = _cases_file(tmp_path, """
        - name: Bad
          label: maybe
    """)
    with pytest.raises(ValueError):
        load_cases(p)


# ── name normalization + matching ─────────────────────────────────────────────────

def test_norm_name_drops_corporate_suffix_not_industry_word():
    assert _norm_name("Satellos Bioscience Inc.") == "satellos bioscience"
    assert _norm_name("Wave Life Sciences Ltd") == "wave life sciences"
    # industry words are KEPT (dropping them would collapse distinct companies)
    assert "therapeutics" in _norm_name("Acme Therapeutics Corp")


def test_match_by_cik_then_ticker_then_name(store):
    _ent(store, "cik:1", ticker="AAA", cik="0000000001", name="Aaa Therapeutics Inc")
    _ent(store, "cik:2", ticker="BBB", cik="0000000002", name="Beta Bio")
    _ent(store, "cik:3", ticker="CCC", name="Gamma Sciences Ltd")

    e, how = match_case(store, Case(name="x", label="positive", cik="0000000001"))
    assert how == "cik" and e.entity_id == "cik:1"

    e, how = match_case(store, Case(name="x", label="positive", ticker="bbb"))  # case-insensitive
    assert how == "ticker" and e.entity_id == "cik:2"

    e, how = match_case(store, Case(name="Gamma Sciences", label="positive"))   # suffix-normalized name
    assert how == "name" and e.entity_id == "cik:3"


def test_match_ticker_alias_with_exchange_suffix(store):
    _ent(store, "cik:9", ticker="MSLE", name="Satellos")
    e, how = match_case(store, Case(name="Satellos Bioscience", label="positive",
                                    aliases=("MSLE.V",)))
    assert how == "ticker" and e.entity_id == "cik:9"


def test_match_returns_none_when_absent(store):
    e, how = match_case(store, Case(name="Nowhere", label="positive", ticker="ZZZ"))
    assert e is None and how is None


# ── funnel stages ──────────────────────────────────────────────────────────────────

def test_funnel_stage_classification(store, cfg, tmp_path):
    _ent(store, "cik:scored", ticker="SCOR", name="Scored Co")
    _score(store, "cik:scored", "deep-dive-candidate", 80)

    # cleared the pre-filter (indep citation + capital signal) but NOT scored
    _ent(store, "cik:clear", ticker="CLR", name="Cleared Co")
    store.insert_signal(_cite("cik:clear", 1))
    store.insert_signal(SignalRecord(signal_id="own_c", entity_id="cik:clear",
                                     signal_type="ownership_crossing", source="edgar_fts",
                                     raw_payload={"fund": "RA Capital", "form": "SCHEDULE 13D"}))

    _ent(store, "cik:plain", ticker="PLN", name="Plain Co")          # in universe, no signals
    _ent(store, "cik:floor", ticker="FLR", name="Floor Co", below_floor=True)

    p = _cases_file(tmp_path, """
        - {name: Scored Co, ticker: SCOR, label: positive}
        - {name: Cleared Co, ticker: CLR, label: positive}
        - {name: Plain Co, ticker: PLN, label: positive}
        - {name: Floor Co, ticker: FLR, label: positive}
        - {name: Missing Co, ticker: MISS, label: positive}
    """)
    rep = evaluate(store, cfg, load_cases(p))
    stage = {e.case.name: e.stage for e in rep.evals}
    assert stage == {"Scored Co": "scored", "Cleared Co": "prefilter_cleared",
                     "Plain Co": "in_universe", "Floor Co": "below_floor",
                     "Missing Co": "not_in_universe"}


# ── classification metrics ─────────────────────────────────────────────────────────

def _confusion_fixture(store):
    _ent(store, "e_tp", ticker="TPP", name="TP Co");   _score(store, "e_tp", "deep-dive-candidate", 82)
    _ent(store, "e_fn", ticker="FNN", name="FN Co");   _score(store, "e_fn", "surveil", 50)
    _ent(store, "e_fp", ticker="FPP", name="FP Co");   _score(store, "e_fp", "deep-dive-candidate", 75)
    _ent(store, "e_tn", ticker="TNN", name="TN Co");   _score(store, "e_tn", "deprioritize", 20)


def test_classification_confusion_and_recalls(store, cfg, tmp_path):
    _confusion_fixture(store)
    p = _cases_file(tmp_path, """
        - {name: TP Co, ticker: TPP, label: positive}
        - {name: FN Co, ticker: FNN, label: positive}
        - {name: FP Co, ticker: FPP, label: negative}
        - {name: TN Co, ticker: TNN, label: negative}
        - {name: Ghost Co, ticker: GHO, label: positive}
    """)
    rep = evaluate(store, cfg, load_cases(p), rule="flag")
    m = rep.classification
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (1, 1, 1, 1)
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    # end-to-end funnel recall counts the unmatched Ghost positive in the denominator
    assert rep.total_positives == 3 and rep.scored_positives == 2
    assert rep.funnel_recall == pytest.approx(2 / 3)
    assert not rep.sufficient          # far below the ≥3 pos / ≥2 neg bar


def test_score_rule_threshold_changes_prediction(store, cfg, tmp_path):
    _confusion_fixture(store)
    p = _cases_file(tmp_path, """
        - {name: TP Co, ticker: TPP, label: positive}
        - {name: FN Co, ticker: FNN, label: positive}
        - {name: FP Co, ticker: FPP, label: negative}
        - {name: TN Co, ticker: TNN, label: negative}
    """)
    # threshold 60: TP(82)✓ FN(50)✗ FP(75)✓ TN(20)✗  → same 1/1/1/1
    rep = evaluate(store, cfg, load_cases(p), rule="score", threshold=60)
    assert (rep.classification["tp"], rep.classification["fp"]) == (1, 1)
    # threshold 90: nothing predicted positive → recall 0, precision undefined (None, not 0)
    rep2 = evaluate(store, cfg, load_cases(p), rule="score", threshold=90)
    assert rep2.classification["tp"] == 0 and rep2.classification["recall"] == 0.0
    assert rep2.classification["precision"] is None


def test_threshold_sweep_present(store, cfg, tmp_path):
    _confusion_fixture(store)
    p = _cases_file(tmp_path, """
        - {name: TP Co, ticker: TPP, label: positive}
        - {name: TN Co, ticker: TNN, label: negative}
    """)
    rep = evaluate(store, cfg, load_cases(p))
    row80 = next(s for s in rep.sweep if s["threshold"] == 80)
    assert row80["tp"] == 1 and row80["fp"] == 0        # TP(82)≥80, TN(20)<80


# ── report rendering ───────────────────────────────────────────────────────────────

def test_write_report_has_sections_and_warns(store, cfg, tmp_path):
    _confusion_fixture(store)
    p = _cases_file(tmp_path, """
        - {name: TP Co, ticker: TPP, label: positive}
        - {name: Ghost Co, ticker: GHO, label: positive, verified: false}
    """)
    rep = evaluate(store, cfg, load_cases(p))
    out = tmp_path / "rep.md"
    write_report(rep, out)
    text = out.read_text(encoding="utf-8")
    assert "Funnel (survivorship view)" in text
    assert "Classification (scored subset only)" in text
    assert "INSUFFICIENT DATA" in text                  # tiny n → the honesty banner fires
    assert "Ghost Co" in text and "not_in_universe" in text
