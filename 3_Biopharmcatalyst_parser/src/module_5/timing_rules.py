"""Module 5 timing rules — regex patterns + BPC placeholder map.

Bump ``RULES_VERSION`` whenever any pattern, resolver, or placeholder
mapping changes — the value is persisted per row in
``catalyst_timing.rules_version`` so you can query which rows were
computed under which rules. After a bump, run
``scripts/3_5_compute_timing.py --all-snapshots``.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date


RULES_VERSION = "v1.0"


MONTHS: dict[str, int] = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


# Spec §7.5: dates BPC uses as quarter-/half-/year-end placeholders.
# (month, day) tuples — wildly over-represented vs. a natural date
# distribution.
BPC_PLACEHOLDER_DATES: frozenset[tuple[int, int]] = frozenset({
    (12, 31),  # full year / FY end
    (6, 30),   # 1H end
    (3, 31),   # Q1 end
    (9, 30),   # Q3 end
    (8, 31),   # ad-hoc "end of summer" BPC bucket
})


# Lane 1: conference column carries a parseable date range.
# BPC format: "<Conf Name> DD/MM/YYYY ET - DD/MM/YYYY ET Conference Calendar"
CONFERENCE_DATE_RANGE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4})\s+ET\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{4})\s+ET"
)


def bucket_from_placeholder(d: date) -> tuple[date, date, str] | None:
    """Spec §7.8 — implied range when ``Catalyst Date`` is a placeholder."""
    key = (d.month, d.day)
    if key == (12, 31):
        return date(d.year, 1, 1), date(d.year, 12, 31), "year"
    if key == (6, 30):
        return date(d.year, 1, 1), date(d.year, 6, 30), "half"
    if key == (3, 31):
        return date(d.year, 1, 1), date(d.year, 3, 31), "quarter"
    if key == (9, 30):
        return date(d.year, 7, 1), date(d.year, 9, 30), "quarter"
    if key == (8, 31):
        return date(d.year, 7, 1), date(d.year, 8, 31), "quarter"
    return None


# ----- text-match container ----------------------------------------------

@dataclass(frozen=True)
class TextMatch:
    date_min: date
    date_max: date
    precision_tier: str   # 'specific'|'month'|'quarter'|'half'|'year'
    matched_phrase: str
    span: tuple[int, int]


# ----- resolvers (one per pattern in PATTERNS) ----------------------------

def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _quarter_range(q: int, year: int, m: re.Match[str]) -> TextMatch | None:
    if not 1 <= q <= 4:
        return None
    first = (q - 1) * 3 + 1
    last = q * 3
    return TextMatch(
        date(year, first, 1),
        date(year, last, _last_day(year, last)),
        "quarter", m.group(0), m.span(),
    )


def _half_range(h: int, year: int, m: re.Match[str]) -> TextMatch | None:
    if h == 1:
        return TextMatch(date(year, 1, 1), date(year, 6, 30), "half",
                         m.group(0), m.span())
    if h == 2:
        return TextMatch(date(year, 7, 1), date(year, 12, 31), "half",
                         m.group(0), m.span())
    return None


def _resolve_specific_month_first(m: re.Match[str]) -> TextMatch | None:
    # "May 24, 2026" / "May 24 2026" / "Sept 24, 2026"
    mon = MONTHS.get(m.group(1).lower()[:3])
    if mon is None:
        return None
    try:
        d = date(int(m.group(3)), mon, int(m.group(2)))
    except ValueError:
        return None  # day out of range for that month
    return TextMatch(d, d, "specific", m.group(0), m.span())


def _resolve_specific_day_first(m: re.Match[str]) -> TextMatch | None:
    # "24 May 2026"
    mon = MONTHS.get(m.group(2).lower()[:3])
    if mon is None:
        return None
    try:
        d = date(int(m.group(3)), mon, int(m.group(1)))
    except ValueError:
        return None
    return TextMatch(d, d, "specific", m.group(0), m.span())


def _resolve_quarter_q_first(m: re.Match[str]) -> TextMatch | None:
    return _quarter_range(int(m.group(1)), int(m.group(2)), m)


def _resolve_quarter_num_first(m: re.Match[str]) -> TextMatch | None:
    return _quarter_range(int(m.group(1)), int(m.group(2)), m)


_QUARTER_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def _resolve_quarter_word(m: re.Match[str]) -> TextMatch | None:
    q = _QUARTER_WORDS.get(m.group(1).lower())
    if q is None:
        return None
    return _quarter_range(q, int(m.group(2)), m)


def _resolve_half_num_first(m: re.Match[str]) -> TextMatch | None:
    return _half_range(int(m.group(1)), int(m.group(2)), m)


def _resolve_half_h_first(m: re.Match[str]) -> TextMatch | None:
    return _half_range(int(m.group(1)), int(m.group(2)), m)


def _resolve_half_word(m: re.Match[str]) -> TextMatch | None:
    h = 1 if m.group(1).lower() == "first" else 2
    return _half_range(h, int(m.group(2)), m)


def _resolve_relative_year(m: re.Match[str]) -> TextMatch | None:
    word = m.group(1).lower()
    year = int(m.group(2))
    if word == "early":
        return TextMatch(date(year, 1, 1), date(year, 3, 31), "quarter",
                         m.group(0), m.span())
    if word == "mid":
        # Apr–Sep → 6-month span → 'half' tier per spec §7.7
        return TextMatch(date(year, 4, 1), date(year, 9, 30), "half",
                         m.group(0), m.span())
    if word == "late":
        return TextMatch(date(year, 10, 1), date(year, 12, 31), "quarter",
                         m.group(0), m.span())
    return None


def _resolve_year_end(m: re.Match[str]) -> TextMatch | None:
    year = int(m.group(1))
    return TextMatch(date(year, 1, 1), date(year, 12, 31), "year",
                     m.group(0), m.span())


def _resolve_month_year(m: re.Match[str]) -> TextMatch | None:
    mon = MONTHS.get(m.group(1).lower()[:3])
    if mon is None:
        return None
    year = int(m.group(2))
    return TextMatch(
        date(year, mon, 1),
        date(year, mon, _last_day(year, mon)),
        "month", m.group(0), m.span(),
    )


def _resolve_year_only(m: re.Match[str]) -> TextMatch | None:
    year = int(m.group(1))
    return TextMatch(date(year, 1, 1), date(year, 12, 31), "year",
                     m.group(0), m.span())


# Pattern order = precision order, highest first. Span-overlap detection
# in compute.py ensures a lower-precision pattern can't reclaim text
# already matched by a higher-precision one (e.g., MONTH_YEAR cannot
# match "May 2026" inside "May 24, 2026").
PATTERNS: list[tuple[re.Pattern[str], object]] = [
    # --- specific dates (single day) ---
    (re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\s+"
        r"(\d{1,2}),?\s+(\d{4})\b",
        re.I,
    ), _resolve_specific_month_first),
    (re.compile(
        r"\b(\d{1,2})\s+"
        r"(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\s+"
        r"(\d{4})\b",
        re.I,
    ), _resolve_specific_day_first),
    # --- quarter ---
    (re.compile(r"\bQ([1-4])\s*[/\-]?\s*(\d{4})\b", re.I),
     _resolve_quarter_q_first),
    (re.compile(r"\b([1-4])Q\s*[/\-]?\s*(\d{4})\b", re.I),
     _resolve_quarter_num_first),
    (re.compile(r"\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?(\d{4})\b", re.I),
     _resolve_quarter_word),
    # --- half ---
    (re.compile(r"\b([1-2])H[\s/\-]+(\d{4})\b", re.I),
     _resolve_half_num_first),
    (re.compile(r"\bH([1-2])[\s/\-]+(\d{4})\b", re.I),
     _resolve_half_h_first),
    (re.compile(r"\b(first|second)\s+half\s+(?:of\s+)?(\d{4})\b", re.I),
     _resolve_half_word),
    # --- relative year (early/mid/late) ---
    (re.compile(r"\b(early|mid|late)[\s-]+(\d{4})\b", re.I),
     _resolve_relative_year),
    # --- year-end family ---
    (re.compile(
        r"\b(?:YE|year[\s-]?end|FY|by\s+(?:the\s+)?end\s+of)\s+(\d{4})\b",
        re.I,
    ), _resolve_year_end),
    # --- month-year (lower precision than specific-date; ordering matters) ---
    (re.compile(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\s+(\d{4})\b",
        re.I,
    ), _resolve_month_year),
    # --- year-only fallback ---
    (re.compile(r"\b(?:in|during|expected\s+in)\s+(\d{4})\b", re.I),
     _resolve_year_only),
]
