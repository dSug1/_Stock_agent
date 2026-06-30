"""Probability core — monotonicity, bounds, empirical estimator (offline)."""

from momentum_parser.probability import (
    empirical_probability,
    estimate,
    forward_returns,
    logistic_probability,
    realized_drift,
)


def test_logistic_monotone_and_bounded():
    cfg = {"beta": 2.2}
    lo = logistic_probability(-1.0, cfg)
    mid = logistic_probability(0.0, cfg)
    hi = logistic_probability(1.0, cfg)
    assert lo < mid < hi
    assert mid == 0.5
    assert 0.01 <= lo and hi <= 0.99


def test_logistic_clamped():
    assert logistic_probability(100.0, {"beta": 50}) <= 0.99
    assert logistic_probability(-100.0, {"beta": 50}) >= 0.01


def test_forward_returns_length_and_value():
    fr = forward_returns([100, 0, 0, 0, 0, 110], 5)
    assert len(fr) == 1
    assert abs(fr[0] - 0.10) < 1e-9


def test_empirical_none_when_thin():
    assert empirical_probability([1, 2, 3], [0.1, 0.1, 0.1], 0.1, horizon=5, min_samples=20) is None


def test_empirical_detects_uptrend():
    closes = [100 + i for i in range(60)]           # every forward window is up
    comps = [0.5] * len(closes)
    p = empirical_probability(closes, comps, 0.5, horizon=5, band=0.25, min_samples=10)
    assert p is not None and p > 0.9


def test_realized_drift_sign():
    assert realized_drift([100 + i for i in range(40)], 20) > 0
    assert realized_drift([140 - i for i in range(40)], 20) < 0


def test_estimate_returns_triplet_in_bounds():
    closes = [100 + i for i in range(120)]
    comps = [0.4] * len(closes)
    p, er, conf = estimate(closes, 0.4, {"horizon_days": 5}, comps)
    assert 0.0 <= p <= 1.0
    assert 0.0 <= conf <= 1.0
    assert er > 0                # bullish composite + uptrend -> positive expected return
