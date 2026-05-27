"""Module 5 compute layer — turn a catalyst_snapshots row into a
``TimingResult``.

Pure-function entry point ``compute_timing_for_row`` takes the four
fields the resolver needs (``conference``, ``catalyst_date``,
``catalyst_text``, ``today``) and returns a ``TimingResult``. DB I/O
lives in ``ingest.py`` so the resolver itself is trivially unit-testable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from .timing_rules import (
    BPC_PLACEHOLDER_DATES,
    CONFERENCE_DATE_RANGE,
    PATTERNS,
    TextMatch,
    bucket_from_placeholder,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimingResult:
    date_min: date | None
    date_max: date | None
    precision_tier: str   # 'specific'|'conference'|'month'|'quarter'|'half'|'year'|'unknown'
    source_lane: str      # 'conference'|'catalyst_date_specific'|'text_parse'|'catalyst_date_bucket'|'unknown'
    matched_phrase: str | None


def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def extract_text_match(text: str | None, today: date) -> TextMatch | None:
    """Run the PATTERNS set over ``text``; return the earliest future match.

    Higher-precision patterns claim spans first; lower-precision patterns
    whose match overlaps a claimed span are skipped. "Future" is defined
    as ``date_max >= today``.
    """
    if not text:
        return None

    claimed_spans: list[tuple[int, int]] = []
    matches: list[TextMatch] = []
    for pattern, resolver in PATTERNS:
        for m in pattern.finditer(text):
            span = m.span()
            if any(_spans_overlap(span, c) for c in claimed_spans):
                continue
            result = resolver(m)
            if result is None:
                continue
            matches.append(result)
            claimed_spans.append(span)

    future = [tm for tm in matches if tm.date_max >= today]
    if not future:
        return None
    # Earliest-future tie-break by date_min, then date_max (more specific wins).
    return min(future, key=lambda tm: (tm.date_min, tm.date_max))


def compute_timing_for_row(
    *,
    conference: str | None,
    catalyst_date: date | None,
    catalyst_text: str | None,
    today: date,
) -> TimingResult:
    """Apply the three-lane resolver (spec §7.3) in priority order.

    Lane priority is strict: the first lane that produces a result wins.
    Lane 1 (conference) beats Lane 2 even when both are present —
    BPC defaults the catalyst_date to the conference's last day, but
    the full window is more accurate than the (false-specific) date.
    """
    # --- Lane 1: conference-tied --------------------------------------
    if conference and conference.strip():
        m = CONFERENCE_DATE_RANGE.search(conference)
        if m:
            try:
                dmin = datetime.strptime(m.group(1), "%d/%m/%Y").date()
                dmax = datetime.strptime(m.group(2), "%d/%m/%Y").date()
                return TimingResult(
                    dmin, dmax, "conference", "conference", m.group(0),
                )
            except ValueError:
                log.warning(
                    "conference date range present but unparseable: %r",
                    conference[:120],
                )
                # fall through to Lane 2
        # else: conference populated but no recognisable date range
        #       — fall through silently (debug-level note)

    # --- Lane 2: specific company-disclosed date ----------------------
    if catalyst_date is not None:
        if (catalyst_date.month, catalyst_date.day) not in BPC_PLACEHOLDER_DATES:
            return TimingResult(
                catalyst_date, catalyst_date, "specific",
                "catalyst_date_specific", None,
            )

    # --- Lane 3: text parsing -----------------------------------------
    tm = extract_text_match(catalyst_text, today)
    if tm is not None:
        return TimingResult(
            tm.date_min, tm.date_max, tm.precision_tier,
            "text_parse", tm.matched_phrase,
        )

    # --- Lane 3b: bucket fallback -------------------------------------
    if catalyst_date is not None:
        bucket = bucket_from_placeholder(catalyst_date)
        if bucket is not None:
            dmin, dmax, tier = bucket
            return TimingResult(
                dmin, dmax, tier, "catalyst_date_bucket", None,
            )

    # --- Unknown ------------------------------------------------------
    return TimingResult(None, None, "unknown", "unknown", None)
