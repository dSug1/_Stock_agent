"""Stage-2 feature engineering — pure functions over raw daily series (offline-testable).

Quantifiable **leading-attention** features harvested in code; the rich/contextual reading is delegated to
Claude's `web_search` at Stage 3 (spec §4). Each builder returns ``(features_dict, score)`` where
``score`` in [-1,1] feeds the evidence bundle. No network, no pandas.
"""

from __future__ import annotations

from typing import Optional, Sequence


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def surge_ratio(series: Sequence[float], baseline_window: int) -> Optional[float]:
    """Latest value vs the mean of the preceding ``baseline_window`` values.

    ``None`` when there isn't enough history or the baseline is non-positive (can't form a ratio).
    A ratio > 1 = attention building above its recent norm (the leading signal we want).
    """
    if len(series) < baseline_window + 1:
        return None
    base = series[-baseline_window - 1:-1]
    m = sum(base) / len(base)
    if m <= 0:
        return None
    return series[-1] / m


def slope(series: Sequence[float], window: int) -> float:
    """Least-squares slope over the last ``window`` points, normalized by the level (per-step % change)."""
    pts = list(series[-window:]) if len(series) >= window else list(series)
    n = len(pts)
    if n < 2:
        return 0.0
    xm = (n - 1) / 2.0
    ym = sum(pts) / n
    num = sum((i - xm) * (y - ym) for i, y in enumerate(pts))
    den = sum((i - xm) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    b = num / den
    level = abs(ym) if ym else 1.0
    return b / level


def media_features(counts: Sequence[float], tones: Sequence[float], cfg: dict) -> tuple[dict, float]:
    """Mainstream-media dimension: article/headline **volume surge** + **tone** + attention slope.

    Bullish attention = volume building above baseline AND positive average tone.
    """
    bw = int(cfg.get("baseline_window", 20))
    ratio = surge_ratio(counts, bw)
    att_slope = slope(counts, int(cfg.get("slope_window", 10)))
    tone_mean = (sum(tones) / len(tones)) if tones else 0.0
    feats = {
        "volume": counts[-1] if counts else 0,
        "surge_ratio": ratio,
        "attention_slope": att_slope,
        "tone_mean": tone_mean,
    }
    # M17: absent/near-empty coverage is NOT bearish — emit no_data (score None) so the rubric treats it as
    # ABSENT (coverage-reducing), never as negative evidence (post-mortem: stub/artifact media read as -0.6).
    min_vol = float(cfg.get("media_min_volume", 1))
    recent_mean = (sum(counts[-bw:]) / min(len(counts), bw)) if counts else 0.0
    if ratio is None or recent_mean < min_vol:
        feats["no_data"] = True
        return feats, None
    surge_component = _clip(ratio - 1.0)
    score = _clip(0.6 * surge_component + 0.4 * _clip(tone_mean))
    return feats, score


def search_features(interest: Sequence[float], cfg: dict) -> tuple[dict, float]:
    """Web-search-statistics dimension: search-interest **surge vs baseline** (the 5_Hype_parser idea).

    Direction-agnostic magnitude — a search spike means retail attention building; Stage 3 judges
    whether that resolves up. When there is NO feed (the stub, or no history) the dimension is ABSENT,
    not bearish — it returns no_data (score None), never a negative score (M17 / post-mortem fix).
    """
    bw = int(cfg.get("baseline_window", 20))
    ratio = surge_ratio(interest, bw)
    s_slope = slope(interest, int(cfg.get("slope_window", 10)))
    feats = {"latest": interest[-1] if interest else 0, "surge_ratio": ratio, "slope": s_slope}
    if ratio is None:                                      # no usable search history -> absent, not negative
        feats["no_data"] = True
        return feats, None
    return feats, _clip(ratio - 1.0)
