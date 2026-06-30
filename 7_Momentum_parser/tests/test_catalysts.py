"""Forward-catalyst proximity — pure, offline (scheduled dates only)."""

from momentum_parser.catalysts import days_to_next, proximity_score


def test_days_to_next_forward_only():
    dates = ["2025-01-01", "2025-01-20", "2025-02-10"]
    assert days_to_next(dates, "2025-01-05") == 15       # soonest on/after asof (Jan 20)
    assert days_to_next(dates, "2025-03-01") is None      # nothing forward
    assert days_to_next([], "2025-01-05") is None


def test_proximity_score_ramp():
    cfg = {"catalyst_horizon_days": 20}
    s0, _ = proximity_score(0, cfg)
    s10, _ = proximity_score(10, cfg)
    s_far, f_far = proximity_score(40, cfg)              # beyond horizon
    assert s0 == 1.0
    assert 0 < s10 < 1.0
    assert s_far == 0.0 and f_far["days_to_catalyst"] == 40


def test_proximity_score_none():
    s, f = proximity_score(None, {})
    assert s == 0.0 and f["days_to_catalyst"] is None
