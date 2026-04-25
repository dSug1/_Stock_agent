"""Python-side deterministic score computation (D34 / D42 / **D46**).

The LLM returns three raw inputs per horizon (target_price_usd,
time_to_catalyst_weeks, probability) plus a per-ticker entry-price-range
block. Python computes:

    current_price_usd = pack["market_snapshot"]["last_close_usd"]   # at scoring time
    fair_mid = (fair_entry_low + fair_entry_high) / 2
    full_mid = (full_reward_low + full_reward_high) / 2
    months   = max(1.0, time_to_catalyst_weeks / 4.33)

    appreciation_from_current_pct = (target - current_price) / current_price * 100
    appreciation_from_fair_pct    = (target - fair_mid)      / fair_mid      * 100
    appreciation_from_full_pct    = (target - full_mid)      / full_mid      * 100

    score_at_current     = (appreciation_from_current_pct / months) * probability   # PRIMARY (D46)
    score_at_fair        = (appreciation_from_fair_pct    / months) * probability   # reference
    score_at_full_reward = (appreciation_from_full_pct    / months) * probability   # reference

    final_horizon = argmax_H(score_at_current_H)        # primary ranking key (D46)
    final_score   = max_H(score_at_current_H)
    final_horizon = "either"   when |3mo - 12mo| / max(...) < tie_tolerance

D46 (2026-04-25) re-anchored the primary ranking score from fair_mid to
current_price_usd so the score reflects what an investor can actually
capture buying today.
"""
from __future__ import annotations

from dataclasses import dataclass

_WEEKS_PER_MONTH = 4.33


@dataclass(frozen=True)
class HorizonScore:
    horizon: str                                           # '3mo' | '12mo'
    target_price_usd: float
    time_to_catalyst_weeks: int
    probability: float
    appreciation_from_current_pct: float                   # D46
    appreciation_from_fair_pct: float
    appreciation_from_full_reward_pct: float
    score_at_current_pct_per_month: float                  # D46 — PRIMARY
    score_at_fair_pct_per_month: float                     # reference
    score_at_full_reward_pct_per_month: float              # reference


@dataclass(frozen=True)
class TickerScore:
    ticker: str
    current_price_usd: float                               # D46 — pack snapshot at scoring time
    fair_entry_low_usd: float
    fair_entry_high_usd: float
    full_reward_low_usd: float
    full_reward_high_usd: float
    near_term_3mo: HorizonScore
    long_term_12mo: HorizonScore
    final_horizon: str                                     # '3mo' | '12mo' | 'either'
    final_score: float                                     # max(score_at_current_3mo, score_at_current_12mo)


def _safe_div(num: float, den: float) -> float:
    return num / den if abs(den) > 1e-12 else 0.0


def compute_horizon_score(
    horizon: str,
    target_price_usd: float,
    time_to_catalyst_weeks: int,
    probability: float,
    current_price_usd: float,                              # D46 — primary anchor
    fair_mid: float,
    full_mid: float,
) -> HorizonScore:
    """Per-horizon Python computation. All three score variants returned;
    the caller decides which one drives ranking (D46: it's score_at_current).
    """
    months = max(1.0, time_to_catalyst_weeks / _WEEKS_PER_MONTH)
    appr_curr = _safe_div(target_price_usd - current_price_usd, current_price_usd) * 100.0
    appr_fair = _safe_div(target_price_usd - fair_mid, fair_mid) * 100.0
    appr_full = _safe_div(target_price_usd - full_mid, full_mid) * 100.0
    score_curr = (appr_curr / months) * probability
    score_fair = (appr_fair / months) * probability
    score_full = (appr_full / months) * probability
    return HorizonScore(
        horizon=horizon,
        target_price_usd=float(target_price_usd),
        time_to_catalyst_weeks=int(time_to_catalyst_weeks),
        probability=float(probability),
        appreciation_from_current_pct=appr_curr,
        appreciation_from_fair_pct=appr_fair,
        appreciation_from_full_reward_pct=appr_full,
        score_at_current_pct_per_month=score_curr,
        score_at_fair_pct_per_month=score_fair,
        score_at_full_reward_pct_per_month=score_full,
    )


def compute_ticker_score(
    ticker: str,
    current_price_usd: float,                              # D46
    entry_price_ranges: dict,
    near_term_3mo: dict,
    long_term_12mo: dict,
    tie_tolerance: float = 0.05,
) -> TickerScore:
    """Aggregate per-ticker scoring + final_horizon pick.

    final_horizon is the argmax of score_at_current across horizons (D46).
    """
    fl = float(entry_price_ranges["fair_entry_low_usd"])
    fh = float(entry_price_ranges["fair_entry_high_usd"])
    rl = float(entry_price_ranges["full_reward_low_usd"])
    rh = float(entry_price_ranges["full_reward_high_usd"])
    fair_mid = (fl + fh) / 2.0
    full_mid = (rl + rh) / 2.0
    cp = float(current_price_usd)

    h3 = compute_horizon_score(
        "3mo",
        float(near_term_3mo["target_price_usd"]),
        int(near_term_3mo["time_to_catalyst_weeks"]),
        float(near_term_3mo["probability"]),
        cp, fair_mid, full_mid,
    )
    h12 = compute_horizon_score(
        "12mo",
        float(long_term_12mo["target_price_usd"]),
        int(long_term_12mo["time_to_catalyst_weeks"]),
        float(long_term_12mo["probability"]),
        cp, fair_mid, full_mid,
    )

    # D46: ranking uses score_at_current, not score_at_fair.
    s3 = h3.score_at_current_pct_per_month
    s12 = h12.score_at_current_pct_per_month
    denom = max(abs(s3), abs(s12), 1e-9)
    if abs(s3 - s12) / denom < tie_tolerance:
        final_horizon = "either"
    else:
        final_horizon = "3mo" if s3 > s12 else "12mo"
    final_score = max(s3, s12)

    return TickerScore(
        ticker=ticker, current_price_usd=cp,
        fair_entry_low_usd=fl, fair_entry_high_usd=fh,
        full_reward_low_usd=rl, full_reward_high_usd=rh,
        near_term_3mo=h3, long_term_12mo=h12,
        final_horizon=final_horizon, final_score=final_score,
    )
