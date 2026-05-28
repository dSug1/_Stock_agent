"""Module 6.5 — pure-function tests for edgar_client.

Covers the two production bugs fixed after the first M6.5 dry run:
  • PFW-specific share-count extractor (don't compute gross / pps for PFWs)
  • TTM cumulative-YTD bug in _ttm_sum
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6_5.edgar_client import (  # noqa: E402
    _row_span_days,
    _ttm_sum,
    classify_raise_type,
    extract_pfw_share_count,
)


# ─────────────────────── PFW share-count extractor ──────────────────


def test_pfw_extractor_count_warrants_pattern():
    """The most common biotech PFW prospectus phrasing."""
    text = "We are offering 4,500,000 pre-funded warrants in this offering."
    assert extract_pfw_share_count(text) == 4_500_000


def test_pfw_extractor_to_purchase_shares_pattern():
    """Per-share cover-page language."""
    text = ("We are offering pre-funded warrants to purchase up to "
            "6,250,000 shares of our common stock.")
    assert extract_pfw_share_count(text) == 6_250_000


def test_pfw_extractor_aggregate_underlying_pattern():
    text = ("an aggregate of 3,750,000 shares of common stock "
            "underlying our pre-funded warrants")
    assert extract_pfw_share_count(text) == 3_750_000


def test_pfw_extractor_no_match_returns_none():
    text = "We are offering shares of common stock at $5.00 per share."
    assert extract_pfw_share_count(text) is None


def test_pfw_extractor_rejects_absurd_counts():
    """Bogus regex matches like '12345678901234' must be rejected."""
    text = "purchaser 178125000000 pre-funded warrants"
    # 178 billion — above the 5e9 ceiling, must be rejected.
    assert extract_pfw_share_count(text) is None


def test_pfw_extractor_rejects_tiny_counts():
    """Reject < 1000 share matches — they're almost always noise."""
    text = "1 pre-funded warrant to purchase one share"
    assert extract_pfw_share_count(text) is None


def test_pfw_extractor_picks_first_match_on_multiple():
    """When multiple patterns hit, the first valid one wins (most specific)."""
    text = ("We are offering 4,500,000 pre-funded warrants. "
            "These warrants are exercisable for up to 4,500,000 shares.")
    assert extract_pfw_share_count(text) == 4_500_000


def test_classify_raise_type_pfw_when_body_mentions_pfw():
    assert classify_raise_type(
        form="424B5", items="",
        body_excerpt="… pre-funded warrants …",
    ) == "pfw"
    assert classify_raise_type(
        form="424B5", items="",
        body_excerpt="… prefunded warrants …",          # no hyphen
    ) == "pfw"


def test_classify_raise_type_shelf_for_s3_when_no_pfw():
    assert classify_raise_type(form="S-3", items="", body_excerpt="") == "shelf"


def test_classify_raise_type_equity_for_424b5_no_pfw():
    assert classify_raise_type(form="424B5", items="", body_excerpt="") == "equity"


# ───────────────────────────── TTM sum ──────────────────────────────


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
    # Not strictly 10-K but the span is ~365 → Strategy 1 catches it.
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
      Q1 2026 row: span 90d, val = -30M           (= Q1 alone)
      Q3 2025 row: span 270d, val = -90M          (= 9-mo YTD)
      Q2 2025 row: span 180d, val = -60M          (= 6-mo YTD)
      Q1 2025 row: span 90d, val = -30M           (= Q1 alone)
      10-K 2024 row: span 365d, val = -110M       (= annual)

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

    Buggy implementation produced -439M from cumulative YTD; the correct
    answer (derived from incremental quarters within FY 2025) is ~-110M.
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
    # avg = -29M; ×4 = -116M
    assert _ttm_sum(rows) == -116_000_000


def test_ttm_none_when_insufficient_data():
    """Empty / missing FY metadata → None."""
    rows = [
        {"end": "2026-03-31", "start": "2026-01-01", "val": -30_000_000,
         "form": "10-Q"},                            # no fy → can't bucket
    ]
    assert _ttm_sum(rows) is None


def test_row_span_days_handles_iso_dates():
    assert _row_span_days({"start": "2026-01-01", "end": "2026-03-31"}) == 89
    assert _row_span_days({"start": "2025-01-01", "end": "2025-12-31"}) == 364


def test_row_span_days_missing_fields_returns_zero():
    assert _row_span_days({"end": "2026-03-31"}) == 0
    assert _row_span_days({}) == 0
