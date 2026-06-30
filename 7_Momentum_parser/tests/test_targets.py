"""Vol-normalized dead-band labeling (spec Decision F) — offline."""

from momentum_parser.targets import (
    DOWN,
    FLAT,
    UP,
    forward_return,
    label_move,
    label_series,
    weekly_sigma,
)


def test_weekly_sigma_none_on_thin_history():
    assert weekly_sigma([1, 2, 3]) is None


def test_weekly_sigma_zero_for_flat_series():
    # perfectly flat -> every weekly return is 0 -> sigma 0
    assert weekly_sigma([100.0] * 80) == 0.0


def test_weekly_sigma_positive_for_volatile_series():
    closes = [100 * (1.1 if i % 10 < 5 else 0.9) ** (i // 10) for i in range(80)]
    s = weekly_sigma(closes)
    assert s is not None and s > 0


def test_forward_return_value_and_bounds():
    closes = [100, 0, 0, 0, 0, 110]
    assert abs(forward_return(closes, 0, 5) - 0.10) < 1e-9
    assert forward_return(closes, 5, 5) is None        # out of range


def test_label_move_deadband():
    sig = 0.04                                          # 4% weekly sigma, band_mult 0.5 -> +-2%
    assert label_move(0.05, sig, 0.5) == UP
    assert label_move(-0.05, sig, 0.5) == DOWN
    assert label_move(0.01, sig, 0.5) == FLAT          # inside the dead-band
    assert label_move(0.05, None, 0.5) is None         # no vol -> no label (fail-open)
    assert label_move(None, sig, 0.5) is None


def test_label_series_alignment_and_pit():
    closes = [100 + i for i in range(80)]               # steady uptrend
    labels = label_series(closes, horizon=5)
    assert len(labels) == len(closes)
    assert labels[-1] is None                           # last bar has no forward window
    # an early-trend bar with enough warm-up should be labelled up (steady climb)
    assert UP in labels
