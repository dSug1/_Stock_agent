"""Stage 5 blend write-path: prediction row (with blend components) + ledger append — offline."""

from momentum_parser import stage5_blend
from momentum_parser.models import Bar
from momentum_parser.store import Store

CFG = {
    "signals": {"min_bars": 60},
    "probability": {"horizon_days": 5, "target": {"vol_band_mult": 0.5, "vol_window_weeks": 12},
                    "beta": 2.2, "use_empirical": True, "empirical_min_samples": 5},
    "blend": {"claude_weight": 0.6, "disagree_threshold": 0.25},
    "validation": {"up_call_threshold": 0.5},
}


def _seed_uptrend(s, t):
    s.upsert_bars(t, [Bar(f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", 100 + i, 100 + i, 100 + i,
                          100 + i, 1000) for i in range(120)])
    return s.latest_bar_date(t)


def _store(tmp_path):
    return Store(tmp_path / "t.db")


def test_blend_uses_claude_leg_and_writes_ledger(tmp_path):
    s = _store(tmp_path)
    asof = _seed_uptrend(s, "AAA")
    # a prior Claude score provides the p_claude leg
    s.write_score("AAA", asof, "scoreRun", tier="rubric", p_up=0.7, p_down=0.2, p_flat=0.1,
                  expected_return=0.02, conviction=0.8, config_hash="h", evidence_fingerprint="f",
                  prompt_version="v")
    out = stage5_blend.run(s, ["AAA"], CFG, "blendRun")
    assert out == {"written": 1, "with_claude": 1, "flagged_review": 0} or out["with_claude"] == 1

    pred = s.predictions_for_run("blendRun")[0]
    assert pred["p_claude"] == 0.7
    assert pred["p_model"] is not None
    # p_final is the weighted blend of the two legs
    assert abs(pred["p_up"] - (0.6 * 0.7 + 0.4 * pred["p_model"])) < 1e-6
    # ledger opened with the directional call
    led = s.open_ledger()
    assert len(led) == 1 and led[0]["ticker"] == "AAA" and led[0]["predicted_label"] in ("up", "flat")


def test_blend_model_only_when_unscored(tmp_path):
    s = _store(tmp_path)
    _seed_uptrend(s, "BBB")
    out = stage5_blend.run(s, ["BBB"], CFG, "blendRun")
    assert out["with_claude"] == 0
    pred = s.predictions_for_run("blendRun")[0]
    assert pred["p_claude"] is None
    assert abs(pred["p_up"] - pred["p_model"]) < 1e-9        # model stands alone
    assert pred["review"] == 0


def test_review_flag_on_disagreement(tmp_path):
    s = _store(tmp_path)
    asof = _seed_uptrend(s, "CCC")                            # uptrend -> p_model high
    s.write_score("CCC", asof, "scoreRun", tier="rubric", p_up=0.1, p_down=0.8, p_flat=0.1,
                  expected_return=-0.02, conviction=0.7, config_hash="h", evidence_fingerprint="f",
                  prompt_version="v")
    out = stage5_blend.run(s, ["CCC"], CFG, "blendRun")
    assert out["flagged_review"] == 1                         # claude bearish vs model bullish -> review
    pred = s.predictions_for_run("blendRun")[0]
    assert pred["review"] == 1 and pred["disagreement"] > 0.25
