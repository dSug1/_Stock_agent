"""Hard filters H1-H5 + timing-bucket partition.

Pure functions — no DB or config I/O. Each function takes the relevant
catalyst fields plus the validated ScoringConfig and returns a verdict.
See spec §12.3 + decisions D8.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .config import ScoringConfig


@dataclass(frozen=True)
class FilterVerdict:
    hard_pass: bool
    fail_reasons: tuple[str, ...]
    timing_bucket: str | None


def _check_H1(market_cap_usd: float | None, cfg: ScoringConfig) -> bool:
    """Market cap band: [mcap_min_usd, mcap_max_usd)."""
    if market_cap_usd is None:
        return False
    return cfg.hard_filters.H1.mcap_min_usd <= market_cap_usd < cfg.hard_filters.H1.mcap_max_usd


def _check_H2(precision_tier: str) -> bool:
    """Timing resolvable."""
    return precision_tier != "unknown"


def _check_H3(date_min: date | None, snapshot_date: date, cfg: ScoringConfig) -> bool:
    """Forward-looking: date_min >= snapshot + window_start_days."""
    if date_min is None:
        return False
    delta = (date_min - snapshot_date).days
    return delta >= cfg.hard_filters.H3.window_start_days


def _check_H4(date_max: date | None, snapshot_date: date) -> bool:
    """Window not entirely past: date_max >= snapshot."""
    if date_max is None:
        return False
    return date_max >= snapshot_date


def _check_H5(stage: str | None, next_catalyst_type: str | None, cfg: ScoringConfig) -> bool:
    """Phase 1/2/3 clinical-readout event."""
    if stage is None or next_catalyst_type is None:
        return False
    if stage not in cfg.hard_filters.H5.allowed_stages:
        return False
    if next_catalyst_type not in cfg.hard_filters.H5.allowed_catalyst_types:
        return False
    return True


def _check_H6(ticker: str | None, delisted: frozenset[str] | set[str] | None) -> bool:
    """D34 — ticker not in the delisted allowlist.

    Returns True (= pass) when ticker is None (defensive — H5 will catch
    the missing-stage case) or when the delisted set is empty/None.
    """
    if not ticker or not delisted:
        return True
    return ticker.upper() not in delisted


def _timing_bucket(precision_tier: str, cfg: ScoringConfig) -> str | None:
    if precision_tier in cfg.timing_buckets.catalyst_date_defined:
        return "catalyst_date_defined"
    if precision_tier in cfg.timing_buckets.catalyst_date_undefined:
        return "catalyst_date_undefined"
    return None


def apply_hard_filters(
    *,
    market_cap_usd: float | None,
    precision_tier: str,
    date_min: date | None,
    date_max: date | None,
    stage: str | None,
    next_catalyst_type: str | None,
    snapshot_date: date,
    cfg: ScoringConfig,
    ticker: str | None = None,
    delisted_tickers: frozenset[str] | set[str] | None = None,
) -> FilterVerdict:
    """Run H1-H6 in order. Collect ALL failures (not just the first)
    so the audit row shows every reason a catalyst was excluded.

    D34 — H6 (delisted-ticker gate) added. ``ticker`` + ``delisted_tickers``
    are keyword-only with defaults of None so legacy call-sites that
    didn't pass them (tests, ad-hoc scripts) still work — they'll just
    skip the H6 check.
    """
    failures: list[str] = []

    if not _check_H1(market_cap_usd, cfg):
        failures.append("H1")
    if not _check_H2(precision_tier):
        failures.append("H2")
    if not _check_H3(date_min, snapshot_date, cfg):
        failures.append("H3")
    if not _check_H4(date_max, snapshot_date):
        failures.append("H4")
    if not _check_H5(stage, next_catalyst_type, cfg):
        failures.append("H5")
    if not _check_H6(ticker, delisted_tickers):
        failures.append("H6")

    hard_pass = not failures
    bucket = _timing_bucket(precision_tier, cfg) if hard_pass else None
    return FilterVerdict(
        hard_pass=hard_pass,
        fail_reasons=tuple(failures),
        timing_bucket=bucket,
    )
