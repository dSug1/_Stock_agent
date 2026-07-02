"""M21 — driver-type feedback learning: settle generated forward drivers, learn per-type skill + novelty-edge."""

import json

from momentum_parser import feedback
from momentum_parser.scoring import rubric
from momentum_parser.store import Store


def test_driver_schema_has_type_enum():
    di = rubric.OUTPUT_SCHEMA["properties"]["forward_drivers"]["items"]
    assert "type" in di["properties"] and "type" in di["required"]
    assert set(rubric.DRIVER_TYPES) == set(di["properties"]["type"]["enum"])


def _settled_with_drivers(s, ticker, run_id, realized, drivers):
    asof = "2026-06-01"
    s.write_score(ticker, asof, run_id, tier="rubric", p_up=0.6, conviction=0.5, memo="x",
                  variant_json=json.dumps({"forward_drivers": drivers}))
    s.append_ledger(ticker, asof, run_id, 5, p_up=0.6, p_final=0.6, predicted_label="up", sigma_week=0.05)
    s.settle_ledger(ticker, asof, run_id, realized_return=realized, realized_label="up", scored_at="t")


def test_learn_drivers_scores_types_and_novelty(tmp_path):
    s = Store(tmp_path / "t.db")
    # a breakout driver bets UP and the move was UP (hit); a squeeze driver bets DOWN (miss)
    _settled_with_drivers(s, "AAA", "run1", +0.04, [
        {"type": "breakout", "expected_impact": 0.05, "novelty": 0.8},
        {"type": "squeeze", "expected_impact": -0.03, "novelty": 0.9},
    ])
    out = feedback.learn_drivers(s, {}, now="t", log=lambda m: None)
    assert out["drivers_settled"] == 2 and out["driver_types"] == 2

    stats = {r["driver_type"]: r for r in s.driver_stats()}
    assert stats["breakout"]["hits"] == 1 and stats["breakout"]["skill"] > 0    # right direction -> +skill
    assert stats["squeeze"]["hits"] == 0 and stats["squeeze"]["skill"] < 0       # wrong direction -> -skill
    assert len(s.driver_outcomes()) == 2
    # both drivers were high-novelty; hit-rate 1/2 in that bucket (validates novelty edge tracking)
    assert out["novelty_edge"]["high"] == {"n": 2, "hit_rate": 0.5}


def test_learn_drivers_skips_zero_impact_and_missing_scores(tmp_path):
    s = Store(tmp_path / "t.db")
    _settled_with_drivers(s, "BBB", "run1", +0.02, [{"type": "other", "expected_impact": 0.0, "novelty": 0.5}])
    out = feedback.learn_drivers(s, {}, now="t", log=lambda m: None)
    assert out["drivers_settled"] == 0                              # zero-impact driver has no directional bet


def test_feedback_run_includes_driver_learning(tmp_path):
    s = Store(tmp_path / "t.db")
    _settled_with_drivers(s, "CCC", "run1", -0.05, [{"type": "mean_reversion", "expected_impact": -0.04,
                                                     "novelty": 0.2}])
    out = feedback.run(s, {"topdown": {"taxonomy_file": None}}, now="t", log=lambda m: None)
    assert out["drivers_settled"] == 1                              # wired into the daily feedback pass
    assert s.driver_stats()[0]["driver_type"] == "mean_reversion"
    assert out["novelty_edge"]["low"]["n"] == 1                     # novelty 0.2 -> low bucket


def test_validation_report_surfaces_driver_types(tmp_path):
    from momentum_parser import validation
    s = Store(tmp_path / "t.db")
    _settled_with_drivers(s, "AAA", "run1", +0.04, [{"type": "breakout", "expected_impact": 0.05, "novelty": 0.8}])
    feedback.learn_drivers(s, {}, now="t", log=lambda m: None)
    path, _ = validation.build_report(s, {"outputs": {"dir": str(tmp_path)}, "validation": {"min_samples": 100}})
    doc = path.read_text(encoding="utf-8")
    assert "which TYPES precede moves" in doc and "breakout" in doc and "Novelty edge" in doc
