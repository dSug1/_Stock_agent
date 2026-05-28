"""Tests for funds_refresh.decision.decide() — the trigger logic for
auto-running 2_Funds_parser from the 3_Biopharmcatalyst pipeline."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from funds_refresh.decision import (  # noqa: E402
    FILING_LAG_DAYS,
    WINDOW_DAYS,
    decide,
    filing_deadline,
    previous_quarter_end,
    quarter_label,
)


# =====================================================================
# Calendar helpers
# =====================================================================

def test_filing_deadline_for_each_quarter():
    assert filing_deadline(date(2026, 3, 31)) == date(2026, 5, 15)
    assert filing_deadline(date(2026, 6, 30)) == date(2026, 8, 14)
    assert filing_deadline(date(2026, 9, 30)) == date(2026, 11, 14)
    # Q4 deadline rolls into next year
    assert filing_deadline(date(2026, 12, 31)) == date(2027, 2, 14)


def test_previous_quarter_end():
    assert previous_quarter_end(date(2026, 3, 31))  == date(2025, 12, 31)
    assert previous_quarter_end(date(2026, 6, 30))  == date(2026, 3, 31)
    assert previous_quarter_end(date(2026, 9, 30))  == date(2026, 6, 30)
    assert previous_quarter_end(date(2026, 12, 31)) == date(2026, 9, 30)


def test_quarter_label():
    assert quarter_label(date(2026, 3, 31)) == "2026Q1"
    assert quarter_label(date(2026, 6, 30)) == "2026Q2"
    assert quarter_label(date(2026, 9, 30)) == "2026Q3"
    assert quarter_label(date(2026, 12, 31)) == "2026Q4"


# =====================================================================
# Window: today within ±7 days of the deadline → RUN
# =====================================================================

@pytest.mark.parametrize("today, expected_target", [
    (date(2026, 5,  8), date(2026, 3, 31)),  # window start (May 15 - 7d)
    (date(2026, 5, 15), date(2026, 3, 31)),  # deadline itself
    (date(2026, 5, 22), date(2026, 3, 31)),  # window end (May 15 + 7d)
    (date(2026, 8,  7), date(2026, 6, 30)),  # Q2 window start
    (date(2026, 8, 14), date(2026, 6, 30)),  # Q2 deadline
    (date(2026, 8, 21), date(2026, 6, 30)),  # Q2 window end
    (date(2026, 11, 7), date(2026, 9, 30)),  # Q3 window
    (date(2027, 2,  7), date(2026, 12, 31)),  # Q4 window starts in Feb of next year
    (date(2027, 2, 14), date(2026, 12, 31)),
    (date(2027, 2, 21), date(2026, 12, 31)),
])
def test_within_window_triggers_run(today, expected_target):
    d = decide(today=today, latest_period_in_db=None)
    assert d.should_run is True
    assert d.target_quarter_end == expected_target
    assert d.deadline == filing_deadline(expected_target)
    assert "within the 13F filing window" in d.reason


def test_within_window_triggers_even_if_db_up_to_date():
    """Trigger #1 is calendar-based; DB freshness doesn't suppress it."""
    today = date(2026, 5, 15)
    d = decide(today=today, latest_period_in_db=date(2026, 3, 31))
    assert d.should_run is True


# =====================================================================
# Past window + DB stale → RUN (catch-up)
# =====================================================================

def test_past_window_db_stale_triggers_catchup():
    # Today is 2026-05-28: 6 days past Q1 window end (May 22)
    today = date(2026, 5, 28)
    d = decide(today=today, latest_period_in_db=date(2025, 12, 31))
    assert d.should_run is True
    assert d.target_quarter_end == date(2026, 3, 31)
    assert "latest period=2025-12-31" in d.reason
    assert "< target 2026-03-31" in d.reason


def test_past_window_db_empty_triggers_catchup():
    today = date(2026, 5, 28)
    d = decide(today=today, latest_period_in_db=None)
    assert d.should_run is True
    assert d.target_quarter_end == date(2026, 3, 31)
    assert "latest period=empty" in d.reason


def test_past_window_db_up_to_date_no_run():
    """Today=2026-05-28, DB already holds the 2026Q1 (target) → no run."""
    today = date(2026, 5, 28)
    d = decide(today=today, latest_period_in_db=date(2026, 3, 31))
    assert d.should_run is False
    assert d.target_quarter_end == date(2026, 3, 31)
    assert "already holds" in d.reason


def test_past_window_db_ahead_of_target_no_run():
    """If somehow the DB has a future quarter, we don't trigger."""
    today = date(2026, 5, 28)
    d = decide(today=today, latest_period_in_db=date(2026, 6, 30))
    assert d.should_run is False


# =====================================================================
# Between windows
# =====================================================================

def test_between_windows_db_up_to_date_no_run():
    """E.g. mid-July 2026: 2 months past Q1 window, 1 month before Q2 window.
    DB already has Q1 → no run."""
    today = date(2026, 7, 15)
    d = decide(today=today, latest_period_in_db=date(2026, 3, 31))
    assert d.should_run is False
    # Target is still Q1 (the most recent window we are past)
    assert d.target_quarter_end == date(2026, 3, 31)


def test_between_windows_db_stale_triggers_catchup():
    """Same date, but DB hasn't been refreshed since prior quarter."""
    today = date(2026, 7, 15)
    d = decide(today=today, latest_period_in_db=date(2025, 12, 31))
    assert d.should_run is True
    assert "past" in d.reason


# =====================================================================
# Edge: previous_quarter_end populated on every decision
# =====================================================================

def test_previous_quarter_end_set_on_run_decisions():
    today = date(2026, 5, 15)
    d = decide(today=today, latest_period_in_db=None)
    assert d.target_quarter_end == date(2026, 3, 31)
    assert d.previous_quarter_end == date(2025, 12, 31)
    today = date(2026, 8, 14)
    d = decide(today=today, latest_period_in_db=None)
    assert d.target_quarter_end == date(2026, 6, 30)
    assert d.previous_quarter_end == date(2026, 3, 31)
