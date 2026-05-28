"""Module 6.5 — PFW heuristic v1 unit tests."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6_5.pfw_estimator import (  # noqa: E402
    DEFAULT_DILUTION_WARNING_PCT,
    estimate_pfw,
)


TODAY = dt.date(2026, 5, 28)


def _pfw(date_iso: str, shares: int) -> dict:
    return {"filing_date": date_iso, "shares_issued": shares}


# ─────────────────────────── basic cases ────────────────────────────


def test_no_pfw_raises_returns_zero_with_no_raises_source():
    r = estimate_pfw("TCRX", pfw_raises=[],
                     basic_shares_count=38_500_000,
                     today=TODAY)
    assert r.prefunded_warrants_count == 0
    assert r.pfw_source == "no_raises"
    assert r.n_pfw_raises == 0
    assert r.pfw_share_dilution_warning is False


def test_single_pfw_in_window():
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[_pfw("2026-04-15", 4_200_000)],
        basic_shares_count=38_500_000,
        today=TODAY,
    )
    assert r.prefunded_warrants_count == 4_200_000
    assert r.pfw_source == "capital_raises_sum_2yr"
    assert r.n_pfw_raises == 1
    # 4.2M / 38.5M ≈ 10.9% — below 25% default → no warning
    assert r.pfw_share_dilution_warning is False


def test_multiple_pfw_summed():
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[_pfw("2026-04-15", 3_000_000),
                    _pfw("2025-11-20", 2_000_000)],
        basic_shares_count=20_000_000,
        today=TODAY,
    )
    assert r.prefunded_warrants_count == 5_000_000
    assert r.n_pfw_raises == 2


# ─────────────────────────── lookback window ────────────────────────


def test_out_of_window_excluded():
    """Default 730-day window; a 3-year-old PFW should be excluded."""
    old = (TODAY - dt.timedelta(days=1100)).isoformat()
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[_pfw(old, 999_999)],
        basic_shares_count=38_500_000,
        today=TODAY,
    )
    assert r.prefunded_warrants_count == 0
    assert r.n_pfw_raises == 0


def test_custom_lookback_extends_window():
    old = (TODAY - dt.timedelta(days=1100)).isoformat()
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[_pfw(old, 999_999)],
        basic_shares_count=38_500_000,
        today=TODAY,
        lookback_days=1500,
    )
    assert r.prefunded_warrants_count == 999_999


# ─────────────────────────── dilution warning ───────────────────────


def test_dilution_warning_fires_above_threshold():
    """26% PFW/basic → above 25% default warning threshold."""
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[_pfw("2026-04-15", 26_000_000)],
        basic_shares_count=100_000_000,
        today=TODAY,
    )
    assert r.pfw_share_dilution_warning is True


def test_dilution_warning_below_threshold_off():
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[_pfw("2026-04-15", 5_000_000)],
        basic_shares_count=100_000_000,
        today=TODAY,
    )
    assert r.pfw_share_dilution_warning is False


def test_dilution_warning_none_when_basic_shares_missing():
    """Cannot compute pct without basic_shares — warning is None, not False."""
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[_pfw("2026-04-15", 5_000_000)],
        basic_shares_count=None,
        today=TODAY,
    )
    assert r.pfw_share_dilution_warning is None


def test_custom_dilution_threshold():
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[_pfw("2026-04-15", 11_000_000)],
        basic_shares_count=100_000_000,
        today=TODAY,
        dilution_warning_pct=10.0,
    )
    # 11M / 100M = 11% → above the 10% custom threshold
    assert r.pfw_share_dilution_warning is True


# ─────────────────────────── shape ──────────────────────────────────


def test_missing_shares_issued_skipped():
    """Raises with shares_issued=None are excluded from the sum."""
    r = estimate_pfw(
        "TCRX",
        pfw_raises=[
            {"filing_date": "2026-04-15", "shares_issued": None},
            _pfw("2026-03-01", 1_000_000),
        ],
        basic_shares_count=38_500_000,
        today=TODAY,
    )
    assert r.prefunded_warrants_count == 1_000_000
    assert r.n_pfw_raises == 1


def test_default_dilution_constant_in_range():
    """Sanity: the shipped default matches what the spec describes."""
    assert 10.0 <= DEFAULT_DILUTION_WARNING_PCT <= 50.0
