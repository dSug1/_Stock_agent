"""Quarter helpers. Internal format: YYYYQn (e.g. '2026Q1').

Quarters derive from period_of_report (the 13F reporting period-end date),
never from filing_date — matches the cross-cutting rule in Overall_specification.md.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import PipelineConfig

_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")

_QUARTER_END_MONTH_DAY = {
    1: "03-31",
    2: "06-30",
    3: "09-30",
    4: "12-31",
}


def is_valid_quarter(value: str) -> bool:
    return bool(_QUARTER_RE.match(value))


def _parse_quarter(value: str) -> tuple[int, int]:
    m = _QUARTER_RE.match(value)
    if not m:
        raise ValueError(f"quarter '{value}' is not in YYYYQn format")
    return int(m.group(1)), int(m.group(2))


def quarter_to_date_end(quarter: str) -> str:
    """'2025Q4' -> '2025-12-31'. Returns ISO YYYY-MM-DD string matching
    the 13F `period_of_report` convention.
    """
    year, q = _parse_quarter(quarter)
    return f"{year}-{_QUARTER_END_MONTH_DAY[q]}"


def prior_quarter(quarter: str) -> str:
    """'2026Q1' -> '2025Q4'. Wraps Q1 -> Q4 of prior year."""
    year, q = _parse_quarter(quarter)
    if q == 1:
        return f"{year - 1}Q4"
    return f"{year}Q{q - 1}"


def date_to_quarter(value: str | datetime | date) -> str:
    if isinstance(value, str):
        dt = datetime.fromisoformat(value[:10])
    elif isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    else:
        raise TypeError(f"date_to_quarter: unsupported type {type(value).__name__}")
    q = (dt.month - 1) // 3 + 1
    return f"{dt.year}Q{q}"


def resolve_quarter(
    config: "PipelineConfig",
    db_conn: sqlite3.Connection | None = None,
) -> str:
    """Resolve config.quarter to a concrete YYYYQn string.

    If already concrete, validate format and return. If 'auto-latest', read
    MAX(period_of_report) from the holdings table (interim choice; can switch
    to filings_log if a better source is identified — see decisions.md).
    """
    if config.quarter != "auto-latest":
        if not is_valid_quarter(config.quarter):
            from .config import ConfigError
            raise ConfigError(f"quarter '{config.quarter}' is not in YYYYQn format")
        return config.quarter

    close_after = False
    if db_conn is None:
        db_conn = sqlite3.connect(config.paths.fundparser_db)
        close_after = True
    try:
        row = db_conn.execute(
            f"SELECT MAX(period_of_report) FROM {config.db_schema.holdings_table}"
        ).fetchone()
    finally:
        if close_after:
            db_conn.close()
    if not row or row[0] is None:
        from .config import ConfigError
        raise ConfigError(
            f"quarter=auto-latest but {config.db_schema.holdings_table}.period_of_report is empty"
        )
    return date_to_quarter(row[0])
