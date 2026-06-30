"""p_model leg (vol-normalized label) + pure blend/confidence — offline."""

from momentum_parser import model
from momentum_parser.blend import blend, confidence
from momentum_parser.models import Bar

CFG = {"signals": {"min_bars": 60},
       "probability": {"horizon_days": 5, "target": {"vol_band_mult": 0.5, "vol_window_weeks": 12},
                       "beta": 2.2, "use_empirical": True, "empirical_min_samples": 5}}


def _bars(closes):
    return [Bar(f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", c, c, c, c, 1000)
            for i, c in enumerate(closes)]


def test_p_model_bullish_on_uptrend():
    p, er, comps = model.p_up(_bars([100 + i for i in range(120)]), CFG)
    assert 0.5 < p <= 0.99 and er > 0
    assert comps["composite"] > 0


def test_p_model_bearish_on_downtrend():
    p, er, comps = model.p_up(_bars([220 - i for i in range(120)]), CFG)
    assert 0.01 <= p < 0.5 and er < 0


def test_blend_weighting_and_clamp():
    pf, d, review = blend(0.8, 0.4, w=0.6, disagree_threshold=0.25)
    assert abs(pf - (0.6 * 0.8 + 0.4 * 0.4)) < 1e-9
    assert abs(d - 0.4) < 1e-9 and review is True          # 0.4 > 0.25


def test_blend_model_only_when_no_claude():
    pf, d, review = blend(None, 0.7, w=0.6, disagree_threshold=0.25)
    assert pf == 0.7 and d is None and review is False


def test_blend_agreement_no_review():
    _, d, review = blend(0.62, 0.58, w=0.6, disagree_threshold=0.25)
    assert review is False and d < 0.25


def test_confidence_formula_and_bounds():
    full = confidence(0.9, 1.0, 0.0, 1.0)
    assert abs(full - 0.9) < 1e-9
    docked = confidence(0.9, 1.0, 0.5, 1.0)                 # disagreement halves it
    assert abs(docked - 0.45) < 1e-9
    assert 0.0 <= confidence(2.0, 2.0, -1.0, 2.0) <= 1.0    # clamped
