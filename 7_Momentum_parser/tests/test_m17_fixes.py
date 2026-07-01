"""M17 — post-mortem fixes: stub/absent dims ≠ negative, megacap gate flag, bundle no_data collapse. Offline."""

from momentum_parser import features
from momentum_parser.models import Bar
from momentum_parser.scoring import rubric
from momentum_parser.stage0_gate import gate_ticker
from momentum_parser.store import Store


# --- Fix 1/2: absent dimensions are no_data, not negative ---------------------------------------
def test_search_stub_is_no_data_not_negative():
    feats, score = features.search_features([], {"baseline_window": 20})
    assert score is None and feats["no_data"] is True         # never a negative score for an absent feed


def test_media_near_empty_is_no_data():
    # a couple of stray articles below the volume floor -> no_data, not a bearish tone read
    feats, score = features.media_features([0] * 20 + [0], [-0.6], {"baseline_window": 20, "media_min_volume": 1})
    assert score is None and feats.get("no_data") is True


def test_media_with_real_coverage_still_scores():
    feats, score = features.media_features([10] * 20 + [40], [0.4], {"baseline_window": 20})
    assert score is not None and score > 0 and not feats.get("no_data")


def test_bundle_collapses_no_data_dimension(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_bars("AAA", [Bar(f"2026-06-{i + 1:02d}", 100, 100, 100, 100, 1e6) for i in range(40)])
    s.upsert_evidence("AAA", "2026-06-28", "search", None, {"no_data": True})    # absent feed
    s.upsert_evidence("AAA", "2026-06-28", "media", 0.5, {"surge_ratio": 2.0})   # real
    b = rubric.build_bundle(s, "AAA", "2026-06-28", {"probability": {"target": {"vol_band_mult": 0.5}}})
    assert b["search"] == {"status": "no_data"}              # collapsed -> absent, not a score
    assert b["media"]["score"] == 0.5                        # real dimension preserved


# --- Fix 4: megacap gate flag -------------------------------------------------------------------
def _bars(price, vol, n=30):
    # oscillate the close so weekly sigma clears the volatility floor (else 'placid' fires first)
    return [Bar(f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}", price, price * 1.05, price * 0.95,
                price * (1.03 if i % 2 else 0.97), vol) for i in range(n)]


def test_megacap_flagged_above_adv_ceiling():
    cfg = {"liquidity": {"min_session_usd": 1e5, "safety_mult": 20, "adv_window": 20},
           "universe": {"min_price": 1.0, "min_weekly_vol": 0.001, "max_adv_usd": 2e9}}
    # $-ADV = price*vol = 100 * 1e9 = 1e11 >> 2e9 ceiling -> megacap (flagged, excluded)
    status, m = gate_ticker(_bars(100, 1e9), cfg)
    assert status == "megacap"
    # a mid-cap below the ceiling still passes
    status2, _ = gate_ticker(_bars(20, 5e5), cfg)             # ADV = 1e7, above the $2M floor, below ceiling
    assert status2 == "pass"


def test_megacap_disabled_when_ceiling_zero():
    cfg = {"liquidity": {"min_session_usd": 1e5, "safety_mult": 20, "adv_window": 20},
           "universe": {"min_price": 1.0, "min_weekly_vol": 0.001, "max_adv_usd": 0}}
    status, _ = gate_ticker(_bars(100, 1e9), cfg)
    assert status == "pass"                                   # 0 = disabled, no ceiling
