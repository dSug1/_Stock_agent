"""Python-side deterministic score computation (D34 / D42).

The LLM returns three raw inputs per horizon (target_price_usd,
time_to_catalyst_weeks, probability) plus a per-ticker entry-price-range
block. Python computes:

    fair_mid = (fair_entry_low + fair_entry_high) / 2
    full_mid = (full_reward_low + full_reward_high) / 2
    months   = max(1.0, time_to_catalyst_weeks / 4.33)
    appreciation_from_fair_pct  = (target - fair_mid) / fair_mid * 100
    appreciation_from_full_pct  = (target - full_mid) / full_mid * 100
    score_at_fair         = (appreciation_from_fair_pct / months) * probability
    score_at_full_reward  = (appreciation_from_full_pct / months) * probability

    final_horizon = argmax_H(score_at_fair_H)
    final_score   = max_H(score_at_fair_H)
    final_horizon = "either"   when |3mo - 12mo| / max(...) < tie_tolerance

Spec: spec/module_6_spec.md § Python-side deterministic score.
"""
from __future__ import annotations

from dataclasses import dataclass

_WEEKS_PER_MONTH = 4.33


@dataclass(frozen=True)
class HorizonScore:
    horizon: str                              # '3mo' | '12mo'
    target_price_usd: float
    time_to_catalyst_weeks: int
    probability: float
    appreciation_from_fair_pct: float
    appreciation_from_full_reward_pct: float
    score_at_fair_pct_per_month: float
    score_at_full_reward_pct_per_month: float


@dataclass(frozen=True)
class TickerScore:
    ticker: str
    fair_entry_low_usd: float
    fair_entry_high_usd: float
    full_reward_low_usd: float
    full_reward_high_usd: float
    near_term_3mo: HorizonScore
    long_term_12mo: HorizonScore
    final_horizon: str                        # '3mo' | '12mo' | 'either'
    final_score: float                        # max(score_at_fair_3mo, score_at_fair_12mo)


def _safe_div(num: float, den: float) -> float:
    return num / den if abs(den) > 1e-12 else 0.0


def compute_horizon_score(
    horizon: str,
    target_price_usd: float,
    time_to_catalyst_weeks: int,
    probability: float,
    fair_mid: float,
    full_mid: float,
) -> HorizonScore:
    """Per-horizon Python computation. No LLM-side rate calc allowed."""
    months = max(1.0, time_to_catalyst_weeks / _WEEKS_PER_MONTH)
    appr_fair = _safe_div(target_price_usd - fair_mid, fair_mid) * 100.0
    appr_full = _safe_div(target_price_usd - full_mid, full_mid) * 100.0
    score_fair = (appr_fair / months) * probability
    score_full = (appr_full / months) * probability
    return HorizonScore(
        horizon=horizon,
        target_price_usd=float(target_price_usd),
        time_to_catalyst_weeks=int(time_to_catalyst_weeks),
        probability=float(probability),
        appreciation_from_fair_pct=appr_fair,
        appreciation_from_full_reward_pct=appr_full,
        score_at_fair_pct_per_month=score_fair,
        score_at_full_reward_pct_per_month=score_full,
    )


def compute_ticker_score(
    ticker: str,
    entry_price_ranges: dict,
    near_term_3mo: dict,
    long_term_12mo: dict,
    tie_tolerance: float = 0.05,
) -> TickerScore:
    """Aggregate per-ticker scoring + final_horizon pick."""
    fl = float(entry_price_ranges["fair_entry_low_usd"])
    fh = float(entry_price_ranges["fair_entry_high_usd"])
    rl = float(entry_price_ranges["full_reward_low_usd"])
    rh = float(entry_price_ranges["full_reward_high_usd"])
    fair_mid = (fl + fh) / 2.0
    full_mid = (rl + rh) / 2.0

    h3 = compute_horizon_score(
        "3mo",
        float(near_term_3mo["target_price_usd"]),
        int(near_term_3mo["time_to_catalyst_weeks"]),
        float(near_term_3mo["probability"]),
        fair_mid, full_mid,
    )
    h12 = compute_horizon_score(
        "12mo",
        float(long_term_12mo["target_price_usd"]),
        int(long_term_12mo["time_to_catalyst_weeks"]),
        float(long_term_12mo["probability"]),
        fair_mid, full_mid,
    )

    s3, s12 = h3.score_at_fair_pct_per_month, h12.score_at_fair_pct_per_month
    denom = max(abs(s3), abs(s12), 1e-9)
    if abs(s3 - s12) / denom < tie_tolerance:
        final_horizon = "either"
    else:
        final_horizon = "3mo" if s3 > s12 else "12mo"
    final_score = max(s3, s12)

    return TickerScore(
        ticker=ticker,
        fair_entry_low_usd=fl, fair_entry_high_usd=fh,
        full_reward_low_usd=rl, full_reward_high_usd=rh,
        near_term_3mo=h3, long_term_12mo=h12,
        final_horizon=final_horizon, final_score=final_score,
    )
