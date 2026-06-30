"""Isotonic calibration (M6) — pure + store round-trip + blend integration. Offline."""

from momentum_parser import calibration, metrics
from momentum_parser.blend import blend
from momentum_parser.calibration import Calibrator, fit_isotonic, identity
from momentum_parser.store import Store


def test_pav_is_monotone():
    out = calibration._pav([0, 1, 0, 0, 1, 1])
    assert out == sorted(out)                       # non-decreasing
    assert abs(sum(out) - 3) < 1e-9                 # PAV preserves the total


def test_identity_returns_input():
    c = identity()
    assert c.apply(0.42) == 0.42 and c.bp == []


def test_fit_identity_until_min_n():
    pairs = [(0.9, 0) for _ in range(10)]
    assert fit_isotonic(pairs, min_n=50).bp == []   # too few -> identity


def test_calibrator_fixes_overconfidence_and_lowers_brier():
    # raw says 0.9 but only ~30% actually happen (overconfident, like the real MRNA backtest)
    pairs = [(0.9, 1) for _ in range(30)] + [(0.9, 0) for _ in range(70)]
    cal = fit_isotonic(pairs, min_n=20)
    assert cal.apply(0.9) < 0.5                      # pulled down toward the observed 0.3
    before = metrics.brier(pairs)
    after = metrics.brier([(cal.apply(p), o) for p, o in pairs])
    assert after < before                            # calibration improves the Brier score


def test_apply_is_monotone_and_clamped():
    pairs = [(0.1, 0), (0.3, 0), (0.5, 1), (0.7, 1), (0.9, 1)] * 20
    cal = fit_isotonic(pairs, min_n=20)
    assert cal.apply(0.1) <= cal.apply(0.9)
    assert 0.01 <= cal.apply(2.0) <= 0.99


def test_store_roundtrip(tmp_path):
    s = Store(tmp_path / "t.db")
    pairs = [(0.9, 0) for _ in range(80)] + [(0.9, 1) for _ in range(20)]
    cal = fit_isotonic(pairs, min_n=20)
    s.save_calibrator("model", cal, len(pairs), "2026-06-30T00:00:00Z")
    loaded = s.load_calibrator("model")
    assert abs(loaded.apply(0.9) - cal.apply(0.9)) < 1e-9
    assert s.load_calibrator("claude").bp == []     # unfitted -> identity


def test_blend_applies_calibrator():
    # a calibrator that maps everything to 0.3
    cal = Calibrator([(0.0, 0.3)])
    fn = lambda p, leg: cal.apply(p)
    pf, _, _ = blend(None, 0.9, w=0.6, disagree_threshold=0.25, calibrate=fn)
    assert abs(pf - 0.3) < 1e-9                      # model leg calibrated before standing alone
