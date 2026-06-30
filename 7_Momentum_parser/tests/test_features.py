"""Stage-2 feature math — pure, offline."""

from momentum_parser.features import media_features, search_features, slope, surge_ratio


def test_surge_ratio_none_on_thin_or_zero_baseline():
    assert surge_ratio([1, 2], 5) is None              # too short
    assert surge_ratio([0, 0, 0, 5], 3) is None         # zero baseline


def test_surge_ratio_value():
    # baseline mean of [10,10,10] = 10, latest 20 -> ratio 2.0
    assert surge_ratio([10, 10, 10, 20], 3) == 2.0


def test_slope_sign():
    assert slope([1, 2, 3, 4, 5], 5) > 0
    assert slope([5, 4, 3, 2, 1], 5) < 0
    assert slope([3, 3, 3], 3) == 0.0


def test_media_features_bullish_on_surge_and_positive_tone():
    counts = [10] * 20 + [40]                            # 4x volume surge
    tones = [0.5] * 5
    feats, score = media_features(counts, tones, {"baseline_window": 20})
    assert feats["surge_ratio"] is not None and feats["surge_ratio"] > 1
    assert score > 0


def test_media_features_failopen_empty():
    feats, score = media_features([], [], {})
    assert score == 0.0 and feats["surge_ratio"] is None


def test_search_features_surge_positive():
    interest = [5] * 20 + [15]                           # 3x search surge
    feats, score = search_features(interest, {"baseline_window": 20})
    assert score > 0 and feats["surge_ratio"] == 3.0
