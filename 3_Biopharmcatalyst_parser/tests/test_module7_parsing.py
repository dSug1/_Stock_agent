"""Module 7 — m7-v1 response parser + 10 HARD RULES unit tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.parsing import (  # noqa: E402
    ParseError,
    extract_json_block,
    parse_deep_dive,
)


# ───────────────────────── minimal valid response ──────────────────────


def _minimal_valid(**override) -> dict:
    base = {
        "ticker": "TCRX",
        "reasoning_trace": "...",
        "drug_profile": {
            "moa": "ActRII ligand trap",
            "moa_class_precedent": "sotatercept",
            "differentiation": "half-life",
            "competition_landscape": "follower",
            "competition_bar_set_by_others": "STELLAR p<0.001",
            "patent_moat": {"composition_patent_expiry": "2039-04",
                            "method_patent_expiry": "2041-08",
                            "summary": "..."},
            "fda_designations": ["FTD", "ODD"],
            "regulatory_pathway": "accelerated_approval",
            "tam_usd": 4_200_000_000,
            "tam_rationale": "...",
        },
        "clinical_evidence": {
            "preclinical_summary": "...",
            "phase1_results": "...",
            "phase2_interim": "...",
            "phase2_final": None,
            "prior_class_successes": [],
            "prior_class_failures": [],
        },
        "rnpv_by_indication": [
            {"indication": "PAH",
             "pos_base_rate": 0.28, "pos_adjusted": 0.42,
             "rnpv_contribution_usd": 1_200_000_000,
             "peak_sales_year": 2032, "rationale": "..."},
            {"indication": "HFpEF",
             "pos_base_rate": 0.15, "pos_adjusted": 0.18,
             "rnpv_contribution_usd": 800_000_000,
             "rationale": "..."},
        ],
        "rnpv_total_usd": 2_000_000_000,
        "rnpv_per_share_usd": 46.84,
        "lead_indication": "PAH",
        "catalyst_outcome": {
            "p_clinical": 0.42, "p_clinical_low": 0.30, "p_clinical_high": 0.55,
            "expected_move_on_hit_pct": 115.0,
            "expected_move_on_miss_pct": -68.0,
            "move_anchor_rationale": "...",
        },
        "financial_overhang": {
            "cash_runway_quarters": 8,
            "dilution_risk": "low",
            "near_term_raise_likely": False,
            "rationale": "...",
        },
        "management_track_record": {"score": 0.65, "summary": "..."},
        "acquisition_target":      {"score": 0.55, "rationale": "..."},
        "thesis_summary": "Mid-conviction PAH thesis.",
        "key_risks": ["competition", "FDA delay"],
        "catalyst_date_sanity_check": {
            "ir_page_consistent": True,
            "catalyst_passed_already": False,
            "notes": "Reaffirmed in Q1 call",
        },
    }
    for k, v in override.items():
        if v is _DELETE:
            base.pop(k, None)
        else:
            base[k] = v
    return base


_DELETE = object()


def _fenced(obj: dict) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


# ─────────────────────── JSON extraction ────────────────────────────


def test_extract_simple_fenced():
    raw = '```json\n{"a": 1}\n```'
    assert extract_json_block(raw) == {"a": 1}


def test_extract_no_fence():
    """Some models forget the fence — accept bare JSON."""
    raw = '{"a": 2}'
    assert extract_json_block(raw) == {"a": 2}


def test_extract_truncated_recovered():
    """A response cut off mid-object should still parse via brace balancing."""
    raw = '```json\n{"a": 1, "b": [1, 2, 3'
    obj = extract_json_block(raw)
    assert obj["a"] == 1
    assert obj["b"] == [1, 2, 3]


def test_extract_truncated_inside_string():
    raw = '```json\n{"a": "hello wor'
    obj = extract_json_block(raw)
    assert obj["a"].startswith("hello wor")


def test_extract_empty_raises():
    with pytest.raises(ParseError):
        extract_json_block("")


def test_extract_garbage_raises():
    with pytest.raises(ParseError):
        extract_json_block("this is not json {{{")


# ─────────────────────── HARD RULES happy path ──────────────────────


def test_happy_path_parses():
    parsed = parse_deep_dive(_fenced(_minimal_valid()))
    assert parsed.ticker == "TCRX"
    assert parsed.lead_indication == "PAH"
    assert parsed.rnpv_per_share_usd == 46.84
    assert parsed.catalyst_outcome["p_clinical"] == 0.42


def test_lead_indication_substring_match_ok():
    """HARD RULE #7 allows substring match either direction."""
    obj = _minimal_valid(lead_indication="Pulmonary Arterial Hypertension")
    obj["rnpv_by_indication"][0]["indication"] = "PAH (pulmonary arterial hypertension)"
    parsed = parse_deep_dive(_fenced(obj))
    assert parsed.lead_indication == "Pulmonary Arterial Hypertension"


# ─────────────────────── HARD RULES failures ────────────────────────


def test_p_clinical_above_0_90_rejected():
    obj = _minimal_valid()
    obj["catalyst_outcome"]["p_clinical"] = 0.95
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert ei.value.kind == "schema_violation"


def test_p_clinical_below_0_10_rejected():
    obj = _minimal_valid()
    obj["catalyst_outcome"]["p_clinical"] = 0.05
    obj["catalyst_outcome"]["p_clinical_low"] = 0.03
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert ei.value.kind == "schema_violation"


def test_p_clinical_band_consistency_violation():
    """HARD RULE #2 — low <= p <= high."""
    obj = _minimal_valid()
    obj["catalyst_outcome"]["p_clinical"] = 0.50
    obj["catalyst_outcome"]["p_clinical_low"]  = 0.55
    obj["catalyst_outcome"]["p_clinical_high"] = 0.60
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "HARD RULE #2" in str(ei.value)


def test_move_on_hit_negative_rejected():
    """HARD RULE #3 — sign discipline."""
    obj = _minimal_valid()
    obj["catalyst_outcome"]["expected_move_on_hit_pct"] = -10.0
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "HARD RULE #3" in str(ei.value)


def test_move_on_miss_positive_rejected():
    obj = _minimal_valid()
    obj["catalyst_outcome"]["expected_move_on_miss_pct"] = 5.0
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "HARD RULE #3" in str(ei.value)


def test_outlier_hit_above_400_rejected():
    obj = _minimal_valid()
    obj["catalyst_outcome"]["expected_move_on_hit_pct"] = 500.0
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "HARD RULE #4" in str(ei.value)


def test_outlier_miss_below_minus_90_rejected():
    obj = _minimal_valid()
    obj["catalyst_outcome"]["expected_move_on_miss_pct"] = -95.0
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "HARD RULE #4" in str(ei.value)


def test_empty_rnpv_rejected():
    """HARD RULE #5 — non-empty list."""
    obj = _minimal_valid()
    obj["rnpv_by_indication"] = []
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "HARD RULE #5" in str(ei.value)


def test_pos_base_rate_out_of_range_rejected():
    """HARD RULE #6 — POS values in [0, 1]."""
    obj = _minimal_valid()
    obj["rnpv_by_indication"][0]["pos_base_rate"] = 1.2
    with pytest.raises(ParseError):
        parse_deep_dive(_fenced(obj))


def test_lead_indication_not_in_list_rejected():
    """HARD RULE #7."""
    obj = _minimal_valid()
    obj["lead_indication"] = "Diabetes"
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "HARD RULE #7" in str(ei.value)


def test_catalyst_already_passed_distinct_kind():
    """HARD RULE #8 raises with kind='catalyst_already_passed' (not schema_violation)."""
    obj = _minimal_valid()
    obj["catalyst_date_sanity_check"]["catalyst_passed_already"] = True
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert ei.value.kind == "catalyst_already_passed"


def test_first_to_market_consistency():
    """HARD RULE #10 — first_to_market requires null bar."""
    obj = _minimal_valid()
    obj["drug_profile"]["competition_landscape"] = "first_to_market"
    obj["drug_profile"]["competition_bar_set_by_others"] = "some prior trial"
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "HARD RULE #10" in str(ei.value)


def test_first_to_market_null_bar_accepted():
    obj = _minimal_valid()
    obj["drug_profile"]["competition_landscape"] = "first_to_market"
    obj["drug_profile"]["competition_bar_set_by_others"] = None
    parsed = parse_deep_dive(_fenced(obj))
    assert parsed.drug_profile["competition_landscape"] == "first_to_market"


def test_unknown_competition_landscape_rejected():
    obj = _minimal_valid()
    obj["drug_profile"]["competition_landscape"] = "monopoly"
    with pytest.raises(ParseError):
        parse_deep_dive(_fenced(obj))


def test_missing_top_level_key_rejected():
    obj = _minimal_valid(catalyst_outcome=_DELETE)
    with pytest.raises(ParseError) as ei:
        parse_deep_dive(_fenced(obj))
    assert "missing 'catalyst_outcome'" in str(ei.value)


def test_management_score_out_of_range_rejected():
    obj = _minimal_valid()
    obj["management_track_record"]["score"] = 1.5
    with pytest.raises(ParseError):
        parse_deep_dive(_fenced(obj))


def test_acquisition_target_missing_rationale():
    obj = _minimal_valid()
    del obj["acquisition_target"]["rationale"]
    with pytest.raises(ParseError):
        parse_deep_dive(_fenced(obj))


def test_thesis_summary_empty_rejected():
    obj = _minimal_valid()
    obj["thesis_summary"] = ""
    with pytest.raises(ParseError):
        parse_deep_dive(_fenced(obj))
