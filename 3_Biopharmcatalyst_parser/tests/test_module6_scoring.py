"""Module 6 — pure scoring function unit tests."""
from __future__ import annotations

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6.config import default_config_path, load_scoring_config  # noqa: E402
from module_6.scoring import (  # noqa: E402
    InsiderTrade,
    composite,
    compute_fund_accumulation,
    compute_insider,
    compute_momentum,
    parse_price_history_30d,
)

CFG = load_scoring_config(default_config_path())


# =====================================================================
# Insider
# =====================================================================

def test_insider_no_trades_zero():
    r = compute_insider([], CFG)
    assert r.insider_score == 0.0
    assert r.insider_gross_weighted_usd == 0.0


def test_insider_only_ceo_counts_with_2x_weight():
    r = compute_insider([InsiderTrade("CEO", 500_000.0)], CFG)
    assert r.insider_gross_weighted_usd == 1_000_000.0   # 500k * 2.0
    # Score should be > 0 and < 100
    assert 0 < r.insider_score < 100


def test_insider_director_buys_excluded():
    r = compute_insider(
        [InsiderTrade("Director", 10_000_000.0),
         InsiderTrade("Chair", 5_000_000.0),
         InsiderTrade("10% owner", 50_000_000.0)],
        CFG,
    )
    assert r.insider_gross_weighted_usd == 0.0
    assert r.insider_score == 0.0


def test_insider_score_reaches_100_at_cap():
    cap = CFG.insider.normalisation_cap_weighted_usd
    # Need weighted gross >= cap; CEO×2 = 5M means raw 2.5M
    r = compute_insider([InsiderTrade("CEO", cap)], CFG)  # 5M * 2 = 10M, > cap
    assert r.insider_score == 100.0


def test_insider_score_monotonic():
    a = compute_insider([InsiderTrade("CEO", 100_000.0)], CFG).insider_score
    b = compute_insider([InsiderTrade("CEO", 1_000_000.0)], CFG).insider_score
    assert b > a


def test_insider_negative_gross_ignored():
    r = compute_insider([InsiderTrade("CEO", -100_000.0)], CFG)
    assert r.insider_gross_weighted_usd == 0.0


# =====================================================================
# Momentum
# =====================================================================

def test_parse_empty():
    assert parse_price_history_30d(None) == []
    assert parse_price_history_30d("") == []


def test_parse_handles_spaces_and_blanks():
    assert parse_price_history_30d("1.0; 2.0 ;; 3.0;abc;4.0") == [1.0, 2.0, 3.0, 4.0]


def test_momentum_null_when_no_history():
    r = compute_momentum(None, CFG)
    assert r.return_30d_pct is None
    assert r.momentum_score == CFG.momentum.null_score


def test_momentum_null_when_only_one_price():
    r = compute_momentum("10.0", CFG)
    assert r.return_30d_pct is None
    assert r.momentum_score == CFG.momentum.null_score


def test_momentum_flat_peaks_at_100():
    r = compute_momentum("10.0;10.0", CFG)
    assert r.return_30d_pct == 0.0
    assert r.momentum_score == 100.0


def test_momentum_at_minus_10_pct():
    # 9.0 / 10.0 -> -10% which is the plateau start
    r = compute_momentum("10.0;9.0", CFG)
    assert math.isclose(r.return_30d_pct, -10.0)
    assert math.isclose(r.momentum_score, 60.0, abs_tol=1e-6)


def test_momentum_at_plus_30_pct():
    r = compute_momentum("10.0;13.0", CFG)
    assert math.isclose(r.return_30d_pct, 30.0)
    assert math.isclose(r.momentum_score, 70.0, abs_tol=1e-6)


def test_momentum_clamped_below_curve_minimum():
    r = compute_momentum("10.0;1.0", CFG)   # -90%
    assert r.momentum_score == 0.0


def test_momentum_clamped_above_curve_maximum():
    r = compute_momentum("10.0;50.0", CFG)  # +400%
    assert r.momentum_score == 0.0


def test_momentum_zero_first_price_treated_as_no_data():
    r = compute_momentum("0;5.0", CFG)
    assert r.return_30d_pct is None
    assert r.momentum_score == CFG.momentum.null_score


# =====================================================================
# Fund accumulation
# =====================================================================

def test_fund_accumulation_zero_when_none():
    r = compute_fund_accumulation(None, CFG)
    assert r.fund_accumulation_usd == 0.0
    assert r.fund_accumulation_score == 0.0


def test_fund_accumulation_zero_when_negative():
    r = compute_fund_accumulation(-100_000.0, CFG)
    assert r.fund_accumulation_score == 0.0


def test_fund_accumulation_reaches_100_at_cap():
    cap = CFG.funds.normalisation_cap_usd
    r = compute_fund_accumulation(cap, CFG)
    assert r.fund_accumulation_score == 100.0


def test_fund_accumulation_monotonic():
    a = compute_fund_accumulation(1e6, CFG).fund_accumulation_score
    b = compute_fund_accumulation(10e6, CFG).fund_accumulation_score
    assert b > a


# =====================================================================
# Composite
# =====================================================================

def test_composite_three_signals():
    r = composite(insider_score=80, momentum_score=60, fund_accumulation_score=40, cfg=CFG)
    expected = 0.35 * 80 + 0.35 * 60 + 0.30 * 40
    assert math.isclose(r.composite_score, expected, abs_tol=1e-9)


def test_composite_skip_funds_renormalises():
    # With skip_funds, weights should renormalise to 0.5/0.5
    r = composite(insider_score=100, momentum_score=80, fund_accumulation_score=0,
                  cfg=CFG, skip_funds=True)
    assert math.isclose(r.composite_score, 0.5 * 100 + 0.5 * 80, abs_tol=1e-9)


def test_composite_bounded_in_0_100():
    r = composite(insider_score=100, momentum_score=100, fund_accumulation_score=100, cfg=CFG)
    assert r.composite_score == 100.0
    r0 = composite(insider_score=0, momentum_score=0, fund_accumulation_score=0, cfg=CFG)
    assert r0.composite_score == 0.0
