"""Stage-3 cost estimate + re-score identity hashes — pure, offline."""

from momentum_parser.scoring.cost import estimate
from momentum_parser.scoring.identity import config_hash, evidence_fingerprint

CFG = {"probability": {"target": {"vol_band_mult": 0.5}}, "blend": {}, "harvest": {}, "sources": {},
       "claude": {"triage_model": "claude-haiku-4-5-20251001", "rubric_model": "claude-sonnet-4-6",
                  "finalize_model": "claude-opus-4-8", "contested_band": [0.45, 0.65],
                  "web_search_variant": "web_search_20250305", "max_output_tokens": 12500,
                  "max_searches_per_company": 5, "use_batch": True,
                  "cost": {"cost_calibration_factor": 0.10}}}


def test_estimate_monotonic_in_n():
    assert estimate(40, CFG)["total"] > estimate(10, CFG)["total"]
    assert estimate(0, CFG)["total"] == 0.0


def test_batch_discount_lowers_rubric():
    batch = estimate(20, CFG)["rubric"]
    no_batch = estimate(20, {**CFG, "claude": {**CFG["claude"], "use_batch": False}})["rubric"]
    assert batch < no_batch


def test_calibration_factor_scales():
    hot = estimate(20, {**CFG, "claude": {**CFG["claude"], "cost": {"cost_calibration_factor": 1.0}}})["total"]
    cal = estimate(20, CFG)["total"]
    assert abs(cal - 0.10 * hot) < 1e-3        # within the estimate's 4-dp rounding


def test_config_hash_stable_and_sensitive():
    a = config_hash(CFG, "v1")
    assert a == config_hash(CFG, "v1")                          # stable
    assert a != config_hash(CFG, "v2")                          # prompt version matters
    bumped = {**CFG, "probability": {"target": {"vol_band_mult": 0.75}}}
    assert a != config_hash(bumped, "v1")                       # target change re-opens


def test_evidence_fingerprint_changes_with_evidence():
    rows_a = [{"dimension": "media", "score": 0.6, "features_json": "{}"}]
    rows_b = [{"dimension": "media", "score": 0.9, "features_json": "{}"}]
    assert evidence_fingerprint(rows_a) != evidence_fingerprint(rows_b)
    assert evidence_fingerprint(rows_a) == evidence_fingerprint(list(rows_a))
