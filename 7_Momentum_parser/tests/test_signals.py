"""Signal core — deterministic checks on synthetic series (offline)."""

from momentum_parser.models import Bar
from momentum_parser.signals import compute_signals, ema, rsi, roc, sma


def _bars(closes, vols=None):
    vols = vols or [1000.0] * len(closes)
    return [Bar(f"2025-01-{i+1:02d}", c, c, c, c, v) for i, (c, v) in enumerate(zip(closes, vols))]


def test_sma_warmup_and_value():
    s = sma([1, 2, 3, 4, 5], 3)
    assert s[0] is None and s[1] is None
    assert s[2] == 2.0 and s[4] == 4.0


def test_ema_seeded_with_sma():
    e = ema([1, 2, 3, 4, 5, 6], 3)
    assert e[1] is None
    assert e[2] == 2.0           # seed = SMA of first 3
    assert e[-1] > e[2]          # rising series -> rising ema


def test_rsi_all_gains_is_100():
    vals = rsi(list(range(1, 30)), 14)
    assert vals[-1] == 100.0


def test_roc_fraction():
    assert abs(roc([100, 110], 1) - 0.10) < 1e-9


def test_composite_bullish_on_uptrend():
    closes = [100 + i for i in range(120)]                 # steady uptrend
    signals, composite = compute_signals(_bars(closes), {})
    names = {s.name for s in signals}
    assert {"sma_cross", "roc", "rsi"} <= names
    assert composite > 0


def test_composite_bearish_on_downtrend():
    closes = [220 - i for i in range(120)]
    _, composite = compute_signals(_bars(closes), {})
    assert composite < 0


def test_breakout_fires_on_new_high():
    closes = [100.0] * 60 + [101.0, 102.0, 130.0]
    signals, _ = compute_signals(_bars(closes), {"breakout_window": 55})
    bk = [s for s in signals if s.name == "breakout"]
    assert bk and bk[0].fired and bk[0].score > 0


def test_short_history_omits_signals_gracefully():
    signals, composite = compute_signals(_bars([1, 2, 3]), {})
    assert composite == 0.0       # nothing computable, no crash
