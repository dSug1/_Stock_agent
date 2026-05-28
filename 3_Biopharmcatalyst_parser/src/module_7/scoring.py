"""Module 7 — expectancy compounding (pure math, no I/O).

Combines:
  • Claude's clinical probability + asymmetric move estimates  (per-ticker)
  • M6's three existing 0-100 signal scores                    (per-ticker)
  • config-supplied bounded modifier ranges                    (per run)

Returns a fully audit-trail-friendly `ExpectancyResult` carrying every
intermediate value so the renderer can show the breakdown without
having to recompute.

Formula (spec §5.2; D26 simplification 2026-05-28 — dropped m_momentum):
    m_insider  = remap(insider_score      ∈ [0,100] → [min_mult, max_mult])
    m_funds    = remap(fund_accum_score    ∈ [0,100] → [min_mult, max_mult])
    p_final    = clamp(p_clinical · m_insider · m_funds, p_final_min, p_final_max)

    move_on_hit_pct  = clamp(claude_hit,  -inf, move_on_hit_pct_max)
    move_on_miss_pct = clamp(claude_miss, move_on_miss_pct_min, +inf)
    E[move_pct]      = p_final · move_on_hit_pct + (1 − p_final) · move_on_miss_pct
    expectancy/week  = E[move_pct] / max(weeks_to_catalyst, 1)

D26 — momentum_score is already in M6's composite_score; double-using
it here was redundant AND the [0.95, 1.05] band moved the needle by <5%
in practice. m_momentum is no longer computed. expectancy_pct (was
E[move] · m_momentum) is dropped — expectancy_per_week_pct comes
directly from E[move_pct].

Spec: spec/module_7_spec.md §5.2 + §5.6.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


# ─────────────────────────── modifier remap ──────────────────────────


def remap_signal_to_modifier(
    signal_score: Optional[float],
    *,
    min_multiplier: float,
    max_multiplier: float,
    neutral_score: float = 50.0,
) -> float:
    """Linear remap of an M6 signal score (0..100) onto [min_mult, max_mult].

    A None or out-of-range input collapses to the neutral midpoint of the
    output range so a missing signal cannot tilt expectancy. Score 50 maps
    to 1.0 only when the range is symmetric around 1.0; otherwise it lands
    at the band midpoint, which is the cleanest neutral choice given an
    arbitrary asymmetric range.
    """
    if min_multiplier > max_multiplier:
        raise ValueError(f"min_multiplier {min_multiplier} > max {max_multiplier}")
    neutral_mid = (min_multiplier + max_multiplier) / 2.0
    if signal_score is None:
        return neutral_mid
    try:
        s = float(signal_score)
    except (TypeError, ValueError):
        return neutral_mid
    if s < 0.0 or s > 100.0:
        # Out-of-range = anomalous; refuse to amplify it.
        return neutral_mid
    return min_multiplier + (s / 100.0) * (max_multiplier - min_multiplier)


# ───────────────────────────── result type ───────────────────────────


@dataclass(frozen=True)
class ExpectancyResult:
    # Inputs (echoed for audit)
    p_clinical: float
    expected_move_on_hit_pct: float
    expected_move_on_miss_pct: float
    insider_score_input: Optional[float]
    fund_accumulation_score_input: Optional[float]
    weeks_to_catalyst: int

    # Modifiers (D33 — m_momentum field dropped; was always 1.0 after D26)
    m_insider: float
    m_funds: float

    # Compounded
    p_clinical_x_modifiers_unclamped: float    # p_clinical * m_insider * m_funds  (pre-clamp)
    p_final: float                              # clamped to [p_min, p_max]
    move_on_hit_pct_clamped: float              # outlier-clamped
    move_on_miss_pct_clamped: float
    e_move_pct: float                           # p_final · hit + (1-p_final) · miss
    expectancy_per_week_pct: float              # e_move_pct / max(weeks, 1)


# ───────────────────────── public compute fn ─────────────────────────


def compute_expectancy(
    *,
    p_clinical: float,
    expected_move_on_hit_pct: float,
    expected_move_on_miss_pct: float,
    insider_score: Optional[float],
    fund_accumulation_score: Optional[float],
    weeks_to_catalyst: int,
    modifiers: dict,                            # {insider: {min,max}, funds: {min,max}, momentum: {min,max}}
    clamps: dict,                               # {p_final_min, p_final_max, move_on_hit_pct_max, move_on_miss_pct_min}
) -> ExpectancyResult:
    """Compound Claude's outputs with M6 modifiers into expectancy/week.

    D33 (2026-05-28): `momentum_score` parameter, `m_momentum`/`expectancy_pct`/
    `momentum_score_input` return fields, and the corresponding `modifiers.momentum`
    block are dropped. Pre-D33 callers passing `momentum_score=` will get a
    TypeError; that's intentional — the formula doesn't use it.
    """
    m_ins = remap_signal_to_modifier(
        insider_score,
        min_multiplier=modifiers["insider"]["min_multiplier"],
        max_multiplier=modifiers["insider"]["max_multiplier"],
    )
    m_fnd = remap_signal_to_modifier(
        fund_accumulation_score,
        min_multiplier=modifiers["funds"]["min_multiplier"],
        max_multiplier=modifiers["funds"]["max_multiplier"],
    )

    p_pre = float(p_clinical) * m_ins * m_fnd
    p_final = max(
        float(clamps["p_final_min"]),
        min(float(clamps["p_final_max"]), p_pre),
    )

    hit_clamped = min(float(expected_move_on_hit_pct),
                      float(clamps["move_on_hit_pct_max"]))
    miss_clamped = max(float(expected_move_on_miss_pct),
                       float(clamps["move_on_miss_pct_min"]))

    e_move = p_final * hit_clamped + (1.0 - p_final) * miss_clamped
    weeks_input = int(weeks_to_catalyst) if weeks_to_catalyst is not None else 0
    weeks_divisor = max(weeks_input, 1)
    expectancy_per_week = e_move / float(weeks_divisor)

    return ExpectancyResult(
        p_clinical=float(p_clinical),
        expected_move_on_hit_pct=float(expected_move_on_hit_pct),
        expected_move_on_miss_pct=float(expected_move_on_miss_pct),
        insider_score_input=(None if insider_score is None
                             else float(insider_score)),
        fund_accumulation_score_input=(None if fund_accumulation_score is None
                                       else float(fund_accumulation_score)),
        weeks_to_catalyst=weeks_input,
        m_insider=m_ins, m_funds=m_fnd,
        p_clinical_x_modifiers_unclamped=p_pre,
        p_final=p_final,
        move_on_hit_pct_clamped=hit_clamped,
        move_on_miss_pct_clamped=miss_clamped,
        e_move_pct=e_move,
        expectancy_per_week_pct=expectancy_per_week,
    )


# ─────────────────────────── helper: weeks ───────────────────────────


def weeks_between(date_min_iso: Optional[str],
                  date_max_iso: Optional[str],
                  snapshot_date_iso: str) -> int:
    """Compute mid-window weeks_to_catalyst from M5's date_min/date_max.

    Falls back to whichever bound exists; floors at 1 to avoid div-by-zero
    in expectancy_per_week.
    """
    import datetime as dt
    try:
        snap = dt.date.fromisoformat(snapshot_date_iso)
    except (TypeError, ValueError):
        return 1
    candidates = []
    for s in (date_min_iso, date_max_iso):
        if not s:
            continue
        try:
            candidates.append(dt.date.fromisoformat(s))
        except ValueError:
            continue
    if not candidates:
        return 1
    if len(candidates) == 2:
        mid = candidates[0] + (candidates[1] - candidates[0]) / 2
    else:
        mid = candidates[0]
    days = (mid - snap).days
    return max(1, (days + 3) // 7)               # round-half-up to weeks
