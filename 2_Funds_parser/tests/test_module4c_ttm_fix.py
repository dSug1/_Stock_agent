"""Module 4c — TTM cumulative-YTD bug fix unit tests.

Ported from `3_Biopharmcatalyst_parser/tests/test_module6_5_edgar_client.py`
2026-05-28 as part of the cross-project fix tracked in
`spec/m4c_ttm_cumulative_ytd_bugfix.md`.

The bug: `_ttm_sum` summed the four most-recent quarterly XBRL values
without recognising that operating_cf is reported cumulatively-YTD
inside a fiscal year (Q1=3mo, Q2=6mo YTD, Q3=9mo YTD, 10-K=annual).
The naive sum double-counts periods inside Q3 and Q2 → ~2-4× inflated
TTM cash burn → ~2-4× under-stated runway across every biotech in the
M4c-enriched universe.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_4c.edgar_client import _row_span_days, _ttm_sum  # noqa: E402


# ───────────────────────── _row_span_days ──────────────────────────


def test_row_span_days_handles_iso_dates():
    assert _row_span_days({"start": "2026-01-01", "end": "2026-03-31"}) == 89
    assert _row_span_days({"start": "2025-01-01", "end": "2025-12-31"}) == 364


def test_row_span_days_missing_fields_returns_zero():
    assert _row_span_days({"end": "2026-03-31"}) == 0
    assert _row_span_days({}) == 0


# ───────────────────────────── _ttm_sum ────────────────────────────


def test_ttm_empty_rows_returns_none():
    assert _ttm_sum([]) is None


def test_ttm_picks_annual_row_when_latest_is_10k():
    rows = [
        {"end": "2025-12-31", "start": "2025-01-01", "val": -120_000_000,
         "form": "10-K", "fy": 2025, "fp": "FY"},
        {"end": "2025-09-30", "start": "2025-01-01", "val": -90_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q3"},
    ]
    assert _ttm_sum(rows) == -120_000_000


def test_ttm_picks_annual_via_span_when_form_not_10k():
    """Some XBRL rows carry the annual value with form='10-K/A' or similar."""
    rows = [
        {"end": "2025-12-31", "start": "2025-01-01", "val": -120_000_000,
         "form": "10-K/A", "fy": 2025, "fp": "FY"},
    ]
    # Span is ~365 → Strategy 1 catches it via span check.
    assert _ttm_sum(rows) == -120_000_000


def test_ttm_pure_quarterly_filer_sums_last_4():
    """When all rows have ~90-day spans (rare but it happens), sum them."""
    rows = [
        {"end": "2026-03-31", "start": "2026-01-01", "val": -30_000_000,
         "form": "10-Q", "fy": 2026, "fp": "Q1"},
        {"end": "2025-12-31", "start": "2025-10-01", "val": -32_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q4"},
        {"end": "2025-09-30", "start": "2025-07-01", "val": -28_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q3"},
        {"end": "2025-06-30", "start": "2025-04-01", "val": -25_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q2"},
    ]
    assert _ttm_sum(rows) == -115_000_000


def test_ttm_cumulative_ytd_derivation_correct():
    """The headline bug fix.

    KURA-like cumulative reporting:
      Q1 2026 row: span 90d,  val = -30M   (= Q1 alone)
      Q3 2025 row: span 270d, val = -90M   (= 9-mo YTD)
      Q2 2025 row: span 180d, val = -60M   (= 6-mo YTD)
      Q1 2025 row: span 90d,  val = -30M   (= Q1 alone)

    Buggy `sum(last 4 quarterly)` = -30 + -90 + -60 + -30 = -210M (wrong).
    Correct derivation:
      FY 2026: Q1 = -30 (no prior period in FY)
      FY 2025: Q3 derived = -90 - (-60) = -30
               Q2 derived = -60 - (-30) = -30
               Q1 = -30 (no prior period in FY)
      Sum of last 4 derived quarters = -30 + -30 + -30 + -30 = -120M.
    """
    rows = [
        {"end": "2026-03-31", "start": "2026-01-01", "val": -30_000_000,
         "form": "10-Q", "fy": 2026, "fp": "Q1"},
        {"end": "2025-09-30", "start": "2025-01-01", "val": -90_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q3"},
        {"end": "2025-06-30", "start": "2025-01-01", "val": -60_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q2"},
        {"end": "2025-03-31", "start": "2025-01-01", "val": -30_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q1"},
    ]
    assert _ttm_sum(rows) == -120_000_000


def test_ttm_kura_like_scenario_returns_sensible_runway():
    """End-to-end replay of the KURA bug pattern.

    Buggy implementation produced ~-$440M from cumulative YTD; the correct
    answer (derived from incremental quarters within FY 2025) is ~-$110M.
    """
    rows = [
        {"end": "2026-03-31", "start": "2026-01-01", "val": -27_500_000,
         "form": "10-Q", "fy": 2026, "fp": "Q1"},
        {"end": "2025-09-30", "start": "2025-01-01", "val": -82_500_000,
         "form": "10-Q", "fy": 2025, "fp": "Q3"},
        {"end": "2025-06-30", "start": "2025-01-01", "val": -55_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q2"},
        {"end": "2025-03-31", "start": "2025-01-01", "val": -27_500_000,
         "form": "10-Q", "fy": 2025, "fp": "Q1"},
    ]
    # 4 derived quarters: -27.5 (2026 Q1), -27.5 (2025 Q3), -27.5 (2025 Q2), -27.5 (2025 Q1)
    assert _ttm_sum(rows) == -110_000_000


def test_ttm_falls_back_partial_when_only_two_quarters():
    """With only 2 derived quarters, annualise from the average."""
    rows = [
        {"end": "2026-03-31", "start": "2026-01-01", "val": -30_000_000,
         "form": "10-Q", "fy": 2026, "fp": "Q1"},
        {"end": "2025-03-31", "start": "2025-01-01", "val": -28_000_000,
         "form": "10-Q", "fy": 2025, "fp": "Q1"},
    ]
    # avg = -29M; × 4 = -116M
    assert _ttm_sum(rows) == -116_000_000


def test_ttm_none_when_insufficient_data():
    """Empty / missing FY metadata → None (no fake number)."""
    rows = [
        {"end": "2026-03-31", "start": "2026-01-01", "val": -30_000_000,
         "form": "10-Q"},                            # no fy → can't bucket
    ]
    assert _ttm_sum(rows) is None
