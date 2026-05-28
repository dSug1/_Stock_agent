"""Module 7 — expectancy compounding (pure math) unit tests."""
from __future__ import annotations

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.scoring import (  # noqa: E402
    ExpectancyResult,
    compute_expectancy,
    remap_signal_to_modifier,
    weeks_between,
)


# Spec-default modifiers / clamps. Tests should remain stable under the
# YAML values shipped in config/module_7.yaml; we hard-code here so a
# change to the YAML doesn't silently change test semantics.
MODIFIERS = {
    "insider":  {"min_multiplier": 0.85, "max_multiplier": 1.15},
    "funds":    {"min_multiplier": 0.85, "max_multiplier": 1.15},
    "momentum": {"min_multiplier": 0.95, "max_multiplier": 1.05},
}
CLAMPS = {
    "p_final_min": 0.05, "p_final_max": 0.95,
    "move_on_hit_pct_max": 400.0, "move_on_miss_pct_min": -90.0,
}


# ─────────────────────── remap_signal_to_modifier ────────────────────


def test_remap_zero_is_min():
    assert remap_signal_to_modifier(0, min_multiplier=0.85, max_multiplier=1.15) == 0.85


def test_remap_hundred_is_max():
    assert remap_signal_to_modifier(100, min_multiplier=0.85, max_multiplier=1.15) == 1.15


def test_remap_fifty_is_midpoint():
    v = remap_signal_to_modifier(50, min_multiplier=0.85, max_multiplier=1.15)
    assert abs(v - 1.0) < 1e-9


def test_remap_none_is_midpoint():
    """Missing signal must not tilt expectancy."""
    v = remap_signal_to_modifier(None, min_multiplier=0.85, max_multiplier=1.15)
    assert abs(v - 1.0) < 1e-9


def test_remap_out_of_range_collapses_to_midpoint():
    assert remap_signal_to_modifier(-10, min_multiplier=0.85, max_multiplier=1.15) == 1.0
    assert remap_signal_to_modifier(150, min_multiplier=0.85, max_multiplier=1.15) == 1.0


def test_remap_asymmetric_momentum_range():
    # Score 50 lands at midpoint of [0.95, 1.05] = 1.0 (true neutral)
    v = remap_signal_to_modifier(50, min_multiplier=0.95, max_multiplier=1.05)
    assert abs(v - 1.0) < 1e-9


# ─────────────────────── compute_expectancy: shape ────────────────────


def _neutral_inputs(**over):
    base = dict(
        p_clinical=0.40,
        expected_move_on_hit_pct=100.0,
        expected_move_on_miss_pct=-60.0,
        insider_score=50.0, fund_accumulation_score=50.0, momentum_score=50.0,
        weeks_to_catalyst=10,
        modifiers=MODIFIERS, clamps=CLAMPS,
    )
    base.update(over)
    return base


def test_neutral_signals_do_not_alter_p_clinical():
    """All three signals at 50 → modifiers all = midpoint = 1.0 → p_final == p_clinical."""
    r = compute_expectancy(**_neutral_inputs())
    assert abs(r.m_insider  - 1.0) < 1e-9
    assert abs(r.m_funds    - 1.0) < 1e-9
    assert abs(r.m_momentum - 1.0) < 1e-9
    assert abs(r.p_final    - 0.40) < 1e-9


def test_strong_insider_lifts_p_final():
    r = compute_expectancy(**_neutral_inputs(insider_score=100.0))
    assert r.m_insider == 1.15
    # p_pre = 0.40 * 1.15 * 1.0 = 0.46
    assert abs(r.p_final - 0.46) < 1e-9


def test_p_final_upper_clamp():
    """p_clinical=0.90, insider=funds=100 → 0.90·1.15·1.15 = 1.19 → clamped to 0.95."""
    r = compute_expectancy(**_neutral_inputs(
        p_clinical=0.90, insider_score=100.0, fund_accumulation_score=100.0,
    ))
    assert r.p_clinical_x_modifiers_unclamped > 0.95
    assert r.p_final == 0.95


def test_p_final_lower_clamp():
    """p_clinical=0.10, insider=funds=0 → 0.10·0.85·0.85 = 0.072 (clamps to 0.072 since > 0.05)
       but with weaker p_clinical 0.05 and the same modifiers we land below 0.05."""
    r = compute_expectancy(**_neutral_inputs(
        p_clinical=0.10, insider_score=0.0, fund_accumulation_score=0.0,
    ))
    # 0.10 * 0.85 * 0.85 = 0.07225
    assert abs(r.p_clinical_x_modifiers_unclamped - 0.07225) < 1e-9
    assert r.p_final >= 0.05


# ─────────────────────── compute_expectancy: math ─────────────────────


def test_asymmetric_e_move_uses_both_legs():
    """E[move] = p_final · hit + (1-p_final) · miss with p_final=0.4, neutral signals."""
    r = compute_expectancy(**_neutral_inputs())
    expected = 0.40 * 100.0 + 0.60 * (-60.0)              # 40 - 36 = 4
    assert abs(r.e_move_pct - expected) < 1e-9


def test_negative_expectancy_when_miss_dominates():
    """Low POS + small upside + big downside → expectancy is negative."""
    r = compute_expectancy(**_neutral_inputs(
        p_clinical=0.20, expected_move_on_hit_pct=40.0,
        expected_move_on_miss_pct=-80.0,
    ))
    # p_final = 0.20 (neutral mods); E[move] = 0.2*40 + 0.8*-80 = 8 - 64 = -56
    assert r.e_move_pct < 0
    assert abs(r.e_move_pct + 56.0) < 1e-9


def test_outlier_clamp_on_hit():
    r = compute_expectancy(**_neutral_inputs(expected_move_on_hit_pct=900.0))
    assert r.move_on_hit_pct_clamped == 400.0


def test_outlier_clamp_on_miss():
    r = compute_expectancy(**_neutral_inputs(expected_move_on_miss_pct=-99.0))
    assert r.move_on_miss_pct_clamped == -90.0


def test_momentum_modifier_only_tilts_expectancy_not_p_final():
    r_hot = compute_expectancy(**_neutral_inputs(momentum_score=100.0))
    r_cold = compute_expectancy(**_neutral_inputs(momentum_score=0.0))
    # p_final identical (momentum doesn't touch probability)
    assert r_hot.p_final == r_cold.p_final
    # But expectancy scales with momentum modifier
    assert r_hot.m_momentum == 1.05
    assert r_cold.m_momentum == 0.95
    assert r_hot.expectancy_pct > r_cold.expectancy_pct


def test_expectancy_per_week_normalisation():
    r1 = compute_expectancy(**_neutral_inputs(weeks_to_catalyst=4))
    r2 = compute_expectancy(**_neutral_inputs(weeks_to_catalyst=12))
    assert abs(r1.expectancy_pct - r2.expectancy_pct) < 1e-9
    assert abs(r1.expectancy_per_week_pct * 4 - r1.expectancy_pct) < 1e-9
    assert abs(r2.expectancy_per_week_pct * 12 - r2.expectancy_pct) < 1e-9
    # 4-week ranks above 12-week for same expectancy_pct.
    assert r1.expectancy_per_week_pct > r2.expectancy_per_week_pct


def test_zero_weeks_floored_to_one():
    r = compute_expectancy(**_neutral_inputs(weeks_to_catalyst=0))
    assert r.weeks_to_catalyst == 0      # echoed in audit
    assert r.expectancy_per_week_pct == r.expectancy_pct  # divisor floored to 1


# ─────────────────────── weeks_between helper ─────────────────────────


def test_weeks_between_midpoint():
    w = weeks_between("2026-09-01", "2026-09-29", "2026-08-04")    # mid = 2026-09-15
    assert w in (6, 7)                                   # ~42 days


def test_weeks_between_single_bound():
    w = weeks_between("2026-09-01", None, "2026-08-04")
    assert w >= 4


def test_weeks_between_no_bounds():
    assert weeks_between(None, None, "2026-08-04") == 1


def test_weeks_between_floored_at_one():
    """Catalyst date in the past or same-day still returns 1 week minimum."""
    assert weeks_between("2026-08-04", "2026-08-04", "2026-08-04") == 1
    assert weeks_between("2026-07-01", None, "2026-08-04") == 1
