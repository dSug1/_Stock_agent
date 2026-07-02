"""M20 — leading microstructure signals (OHLCV-only, pre-move setup): coil, CMF, divergence, breakout. Offline."""

from momentum_parser import microstructure as ms
from momentum_parser.models import Bar
from momentum_parser.scoring import rubric
from momentum_parser.store import Store

LCFG = {"min_bars": 30, "coil_short": 5, "coil_long": 60, "cmf_window": 20,
        "divergence_min": 0.05, "breakout_window": 20}


def _bar(c, i=0, hi=None, lo=None, v=1e6):
    date = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"              # unique per i (for store round-trips)
    return Bar(date, c, hi if hi is not None else c + 0.5, lo if lo is not None else c - 0.5, c, v)


def test_coil_high_when_recent_vol_compresses():
    volatile = [_bar(100 + 5 * (-1) ** i) for i in range(45)]      # big alternating moves
    quiet = [_bar(100 + 0.1 * (-1) ** i) for i in range(20)]       # tiny moves now
    assert ms.coil(volatile + quiet, 5, 60) > 0.8                  # current vol at the low end -> coiled
    # persistent high vol -> not coiled
    assert ms.coil([_bar(100 + 5 * (-1) ** i) for i in range(65)], 5, 60) < 0.5


def test_cmf_sign_tracks_close_position():
    up = [_bar(100, hi=100, lo=99) for _ in range(20)]             # close at the HIGH -> accumulation
    dn = [_bar(100, hi=101, lo=100) for _ in range(20)]            # close at the LOW -> distribution
    assert ms.cmf(up, 20) > 0.9 and ms.cmf(dn, 20) < -0.9


def test_bullish_divergence_accumulation_into_weakness():
    # price drifts DOWN but each bar closes near its high (intrabar buying) -> CMF>0, divergence True
    bars = [_bar(110 - i * 0.5, hi=110 - i * 0.5, lo=108 - i * 0.5) for i in range(40)]
    feats, _ = ms.leading_features(bars, LCFG)
    assert feats["accumulation_cmf"] > 0 and feats["bullish_divergence"] is True


def test_breakout_pressure_high_near_range_top():
    rising = [_bar(100 + i) for i in range(20)]                    # last close = window high
    assert ms.breakout_pressure(rising, 20) > 0.9
    falling = [_bar(120 - i) for i in range(20)]                   # last close = window low
    assert ms.breakout_pressure(falling, 20) < 0.2


def test_leading_features_no_data_when_short():
    feats, score = ms.leading_features([_bar(100) for _ in range(10)], LCFG)
    assert feats.get("no_data") is True and score == 0.0         # absent, not bearish


def test_bundle_includes_leading_block(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_bars("AAA", [_bar(100 + 0.1 * i, i=i) for i in range(40)])
    b = rubric.build_bundle(s, "AAA", "2026-06-01",
                            {"probability": {"target": {"vol_band_mult": 0.5}}, "leading": LCFG})
    assert b["leading"] is not None and "coil" in b["leading"] and "accumulation_cmf" in b["leading"]
    assert "LEADING SETUP" in rubric.system_prompt({})            # prompt tells Claude to use it forward
