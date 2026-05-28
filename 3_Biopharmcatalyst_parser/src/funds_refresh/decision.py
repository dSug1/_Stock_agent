"""Pure-function logic for deciding whether to auto-refresh 2_Funds_parser.

13F deadlines (45 calendar days after quarter end, per SEC rule 13f-1):

==============   =============   ==============
Quarter end      Filing deadline Plain-English
==============   =============   ==============
March 31         May 15          Q1 of same year
June 30          August 14       Q2 of same year
September 30     November 14     Q3 of same year
December 31      February 14     Q4 (next year)
==============   =============   ==============

Trigger rules per user spec:

1.  If today falls within ±7 days of any deadline → RUN.
2.  If today is past a deadline + 7d AND the 2_Funds_parser DB does not
    yet hold the matching quarter → RUN (catch-up case for the user who
    skipped the window).

Otherwise → SKIP.

The function never raises on a missing DB row — pass ``latest_period`` as
``None`` and rule (2) treats that as "DB is empty, refresh required."
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

WINDOW_DAYS = 7
FILING_LAG_DAYS = 45  # SEC rule 13f-1: 45 calendar days after quarter end


@dataclass(frozen=True)
class RefreshDecision:
    should_run: bool
    reason: str
    target_quarter_end: date | None  # quarter we'd be ingesting (or None if not in scope)
    previous_quarter_end: date | None  # for the consensus_builds delta report
    deadline: date | None            # 45-day filing deadline for target
    window_start: date | None        # deadline − 7 days
    window_end: date | None          # deadline + 7 days


# ---------- helpers ----------

def quarter_ends_through(year: int, *, also_prior: int = 1, also_after: int = 1) -> list[date]:
    """Quarter-end dates spanning ``year - also_prior`` .. ``year + also_after``."""
    out: list[date] = []
    for y in range(year - also_prior, year + also_after + 1):
        out.extend([
            date(y, 3, 31),
            date(y, 6, 30),
            date(y, 9, 30),
            date(y, 12, 31),
        ])
    return out


def filing_deadline(quarter_end: date) -> date:
    """45 calendar days after the quarter-end."""
    return quarter_end + timedelta(days=FILING_LAG_DAYS)


def previous_quarter_end(quarter_end: date) -> date:
    """The quarter immediately before ``quarter_end``."""
    qe_month = quarter_end.month
    qe_year = quarter_end.year
    if qe_month == 3:
        return date(qe_year - 1, 12, 31)
    if qe_month == 6:
        return date(qe_year, 3, 31)
    if qe_month == 9:
        return date(qe_year, 6, 30)
    if qe_month == 12:
        return date(qe_year, 9, 30)
    raise ValueError(f"not a quarter-end month: {quarter_end}")


# ---------- core decision ----------

def decide(today: date, latest_period_in_db: date | None) -> RefreshDecision:
    """Return a :class:`RefreshDecision` for the supplied date pair."""

    # All quarter-end candidates whose pre-deadline window has started.
    candidates: list[tuple[date, date]] = []
    for q_end in quarter_ends_through(today.year):
        deadline = filing_deadline(q_end)
        window_start = deadline - timedelta(days=WINDOW_DAYS)
        if window_start <= today:
            candidates.append((q_end, deadline))
    candidates.sort(key=lambda t: t[1])  # by deadline ascending

    if not candidates:
        # Today is before the earliest window we considered — nothing to do.
        return RefreshDecision(
            should_run=False,
            reason="no 13F filing window has opened yet within our 3-year scan",
            target_quarter_end=None,
            previous_quarter_end=None,
            deadline=None, window_start=None, window_end=None,
        )

    # The most recent candidate is the one whose window we are in or past.
    target_q_end, deadline = candidates[-1]
    window_start = deadline - timedelta(days=WINDOW_DAYS)
    window_end = deadline + timedelta(days=WINDOW_DAYS)
    prev_q_end = previous_quarter_end(target_q_end)

    base = dict(
        target_quarter_end=target_q_end,
        previous_quarter_end=prev_q_end,
        deadline=deadline,
        window_start=window_start,
        window_end=window_end,
    )

    if window_start <= today <= window_end:
        return RefreshDecision(
            should_run=True,
            reason=(
                f"today {today} is within the 13F filing window "
                f"[{window_start}, {window_end}] for quarter ending {target_q_end}"
            ),
            **base,
        )

    # today > window_end
    if latest_period_in_db is None or latest_period_in_db < target_q_end:
        latest_str = "empty" if latest_period_in_db is None else str(latest_period_in_db)
        return RefreshDecision(
            should_run=True,
            reason=(
                f"today {today} is past the {target_q_end} filing window "
                f"(deadline {deadline}); 2_Funds_parser latest period={latest_str} "
                f"< target {target_q_end}"
            ),
            **base,
        )

    return RefreshDecision(
        should_run=False,
        reason=(
            f"today {today} is past the {target_q_end} filing window "
            f"(deadline {deadline}); 2_Funds_parser already holds {latest_period_in_db}"
        ),
        **base,
    )


def quarter_label(q_end: date) -> str:
    """ISO-like quarter label, e.g. ``2026Q1`` for 2026-03-31."""
    q = (q_end.month - 1) // 3 + 1
    return f"{q_end.year}Q{q}"
