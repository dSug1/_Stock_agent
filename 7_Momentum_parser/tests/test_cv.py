"""Walk-forward out-of-sample calibration validation (M9) — pure, offline."""

from momentum_parser import cv


def _rows(specs):
    """specs = list of (p_up, up?bool); asof dates are assigned in order (time-ordered)."""
    return [{"asof": f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", "p_up": p,
             "realized_label": "up" if up else "flat", "predicted_label": "up" if p >= 0.5 else "flat"}
            for i, (p, up) in enumerate(specs)]


def test_insufficient_when_tiny():
    assert cv.walk_forward(_rows([(0.9, True)] * 4), n_folds=5).get("insufficient") is True


def test_persistent_overconfidence_generalizes():
    # raw always says 0.9 but only ~30% happen, consistently across time -> calibration learned on
    # earlier folds should lower OOS Brier on later folds.
    specs = [(0.9, i % 10 < 3) for i in range(200)]            # 30% up, stable over time
    out = cv.walk_forward(_rows(specs), n_folds=5, min_cal=20)
    assert out["generalizes"] is True
    assert out["brier_cal_oos"] < out["brier_raw_oos"]


def test_already_calibrated_no_harm():
    # p_up matches the outcome rate within each bucket -> calibration ~ identity, no big OOS change.
    specs = []
    for i in range(200):
        p = 0.2 if i % 2 else 0.8
        up = (i % 10 < 2) if p == 0.2 else (i % 10 < 8)        # 20% / 80% realised
        specs.append((p, up))
    out = cv.walk_forward(_rows(specs), n_folds=5, min_cal=20)
    assert out["brier_cal_oos"] <= out["brier_raw_oos"] + 0.05  # not materially worse


def test_no_lookahead_ordering():
    # rows given out of order are sorted by asof before folding (no future leaks into a train window)
    specs = [(0.9, i % 10 < 3) for i in range(120)]
    rows = _rows(specs)
    shuffled = rows[60:] + rows[:60]
    a = cv.walk_forward(rows, n_folds=4, min_cal=20)
    b = cv.walk_forward(shuffled, n_folds=4, min_cal=20)
    assert a["brier_cal_oos"] == b["brier_cal_oos"]            # order-independent (sorted internally)
