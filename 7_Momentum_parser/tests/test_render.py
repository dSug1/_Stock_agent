"""HTML report — shows blend columns + Claude reasoning when scored, model-only notice otherwise."""

from momentum_parser import render, stage5_blend
from momentum_parser.models import Bar
from momentum_parser.store import Store

CFG = {
    "signals": {"min_bars": 40},
    "probability": {"horizon_days": 5, "target": {"vol_band_mult": 0.5, "vol_window_weeks": 12},
                    "beta": 2.2, "use_empirical": True, "empirical_min_samples": 5},
    "blend": {"claude_weight": 0.6, "disagree_threshold": 0.25},
    "validation": {"up_call_threshold": 0.5},
}


def _seed(s, t):
    s.upsert_bars(t, [Bar(f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", 100 + i, 0, 0, 100 + i, 1000)
                      for i in range(120)])
    return s.latest_bar_date(t)


def test_report_shows_claude_memo(tmp_path):
    s = Store(tmp_path / "t.db")
    asof = _seed(s, "AAA")
    s.write_score("AAA", asof, "scoreRun", tier="rubric", p_up=0.7, p_down=0.2, p_flat=0.1,
                  expected_return=0.02, conviction=0.8, memo="Forward PDUFA + building search interest.",
                  dimensions_json='{"technical":0.6,"media":0.3,"search":0.1,"catalyst":0.4}',
                  config_hash="h", evidence_fingerprint="f", prompt_version="v")
    stage5_blend.run(s, ["AAA"], CFG, "run1", log=lambda m: None)
    html = render.render(s, CFG, "run1").read_text(encoding="utf-8")
    assert "Forward PDUFA" in html and "Claude analysis" in html
    assert "p_claude" in html and "p_final" in html
    assert "technical" in html and "catalyst" in html


def test_report_model_only_notice(tmp_path):
    s = Store(tmp_path / "t.db")
    _seed(s, "BBB")
    stage5_blend.run(s, ["BBB"], CFG, "run1", log=lambda m: None)   # no score -> model-only
    html = render.render(s, CFG, "run1").read_text(encoding="utf-8")
    assert "model-only run" in html.lower() or "Model-only" in html
    assert "--dispatch" in html
