"""Module 3 - Data Bridge.

Reads Module 2's per-filing 13F holdings from 2_fundparser.db, classifies
share types, aggregates per-fund then across funds, and writes the
per-ticker universe + audit Parquets that Module 4 consumes.
"""
from .bridge import (
    KEPT_CLASSES,
    DROPPED_CLASSES,
    VALID_CLASSES,
    aggregate_fund_positions,
    aggregate_per_ticker,
    build_universe,
    classify_share_type,
    load_holdings_for_period,
    load_share_type_rules,
    write_outputs,
)

__all__ = [
    "KEPT_CLASSES",
    "DROPPED_CLASSES",
    "VALID_CLASSES",
    "aggregate_fund_positions",
    "aggregate_per_ticker",
    "build_universe",
    "classify_share_type",
    "load_holdings_for_period",
    "load_share_type_rules",
    "write_outputs",
]
