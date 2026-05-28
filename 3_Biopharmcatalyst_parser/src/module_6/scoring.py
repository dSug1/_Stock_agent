"""Pure scoring functions for the three soft signals + composite.

No DB or filesystem I/O. Takes already-aggregated inputs (e.g., the
list of insider trades for a ticker) and returns a 0-100 score.

Spec §12.4; decisions D8 + D9.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import ScoringConfig


# -----------------------------------------------------------------------------
# Insider signal — CEO + CFO buys over 365 days, log-scaled
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class InsiderTrade:
    """One row from v_executive_open_market_trades within the lookback."""
    executive_role: str
    gross_usd: float


@dataclass(frozen=True)
class InsiderResult:
    insider_gross_weighted_usd: float
    insider_score: float


def compute_insider(trades: list[InsiderTrade], cfg: ScoringConfig) -> InsiderResult:
    """Apply role weights and log-scale to 0-100.

    Roles not present in ``cfg.insider.role_weights`` contribute 0
    (i.e., we don't penalise, we just ignore them — Director/10%-owner
    buys are silently dropped).
    """
    weights = cfg.insider.role_weights
    weighted = 0.0
    for t in trades:
        w = weights.get(t.executive_role, 0.0)
        if w > 0 and t.gross_usd and t.gross_usd > 0:
            weighted += w * t.gross_usd

    if weighted <= 0:
        return InsiderResult(insider_gross_weighted_usd=0.0, insider_score=0.0)

    cap = cfg.insider.normalisation_cap_weighted_usd
    score = math.log10(1 + weighted) / math.log10(1 + cap) * 100.0
    return InsiderResult(
        insider_gross_weighted_usd=weighted,
        insider_score=min(100.0, max(0.0, score)),
    )


# -----------------------------------------------------------------------------
# Momentum signal — 30d return from BPC price_history_30d string
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class MomentumResult:
    return_30d_pct: float | None
    momentum_score: float


def parse_price_history_30d(s: str | None) -> list[float]:
    """Parse the BPC semicolon-separated price string.

    Returns prices oldest-first (matching the CSV column order). Skips
    blank/unparseable tokens. Returns [] when the string is None/empty.
    """
    if not s:
        return []
    out: list[float] = []
    for tok in s.split(";"):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            continue
    return out


def _piecewise(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation. ``points`` is sorted ascending
    by x. Clamps to endpoint score outside the range.
    """
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return points[-1][1]  # unreachable; kept for type-checker happiness


def compute_momentum(price_history_30d: str | None, cfg: ScoringConfig) -> MomentumResult:
    """Compute 30d return from BPC's semicolon price string and map to
    a 0-100 score via the piecewise curve in ``cfg.momentum``.

    Treats <2 valid prices or first_price <= 0 as "no data" → ``null_score``.
    """
    prices = parse_price_history_30d(price_history_30d)
    if len(prices) < 2 or prices[0] <= 0:
        return MomentumResult(return_30d_pct=None, momentum_score=cfg.momentum.null_score)

    ret_pct = (prices[-1] / prices[0] - 1.0) * 100.0
    points = [(p.return_pct, p.score) for p in cfg.momentum.curve]
    score = _piecewise(ret_pct, points)
    return MomentumResult(return_30d_pct=ret_pct, momentum_score=min(100.0, max(0.0, score)))


# -----------------------------------------------------------------------------
# Fund accumulation signal — log-scaled positive Δshares × price proxy
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class FundAccumulationResult:
    fund_accumulation_usd: float
    fund_accumulation_score: float


def compute_fund_accumulation(
    accumulation_usd: float | None, cfg: ScoringConfig,
) -> FundAccumulationResult:
    """Log-scale the per-ticker positive-delta accumulation USD to 0-100.

    Negative or NULL accumulation → score 0 (no signal; no penalty).
    """
    if accumulation_usd is None or accumulation_usd <= 0:
        return FundAccumulationResult(
            fund_accumulation_usd=0.0 if accumulation_usd is None else accumulation_usd,
            fund_accumulation_score=0.0,
        )
    cap = cfg.funds.normalisation_cap_usd
    score = math.log10(1 + accumulation_usd) / math.log10(1 + cap) * 100.0
    return FundAccumulationResult(
        fund_accumulation_usd=accumulation_usd,
        fund_accumulation_score=min(100.0, max(0.0, score)),
    )


# -----------------------------------------------------------------------------
# Composite — three-signal weighted sum
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class CompositeResult:
    composite_score: float


def composite(
    *,
    insider_score: float,
    momentum_score: float,
    fund_accumulation_score: float,
    cfg: ScoringConfig,
    skip_funds: bool = False,
) -> CompositeResult:
    """Weighted sum of the three signals.

    When ``skip_funds`` is True, the funds weight is set to 0 and the
    remaining two weights are renormalised so the composite still lands
    in [0, 100].
    """
    w_i = cfg.composite.weight_insider
    w_m = cfg.composite.weight_momentum
    w_f = 0.0 if skip_funds else cfg.composite.weight_funds

    total_w = w_i + w_m + w_f
    if total_w <= 0:
        return CompositeResult(composite_score=0.0)

    if skip_funds:
        # Renormalise the two remaining weights to sum to 1.
        w_i_eff = w_i / (w_i + w_m) if (w_i + w_m) > 0 else 0.0
        w_m_eff = w_m / (w_i + w_m) if (w_i + w_m) > 0 else 0.0
        score = w_i_eff * insider_score + w_m_eff * momentum_score
    else:
        score = w_i * insider_score + w_m * momentum_score + w_f * fund_accumulation_score

    return CompositeResult(composite_score=min(100.0, max(0.0, score)))
