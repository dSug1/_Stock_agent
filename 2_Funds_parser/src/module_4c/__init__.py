"""Module 4c — Fundamentals enrichment (biotech, financials-only).

Sits between M4b (rank) and M5 (packs). Fetches free fundamentals from
SEC EDGAR (companyfacts XBRL, submissions, Form 4, 8-K capital raises,
S-3 shelves) into `data/fundamentals.db`.

Clinical-trial data is intentionally OUT of scope (per D54 — stays with
M6's web_search).

Spec: spec/module_4c_spec.md
Decisions: spec/decisions.md § D54
"""
from __future__ import annotations

from .fundamentals_db import (
    init_fundamentals_db,
    get_fetch_log,
    upsert_fetch_log,
    upsert_financials_row,
    upsert_capital_raise,
    upsert_insider_transaction,
    latest_financials_for_tickers,
    capital_raises_for_tickers_since,
    insider_txns_for_tickers_since,
    fetch_log_for_tickers,
)
from .industries import is_biotech_ticker, load_biotech_industries
from .enrich import run_enrichment_4c, EnrichResult, SOURCES_ALL

__all__ = [
    "init_fundamentals_db",
    "get_fetch_log",
    "upsert_fetch_log",
    "upsert_financials_row",
    "upsert_capital_raise",
    "upsert_insider_transaction",
    "latest_financials_for_tickers",
    "capital_raises_for_tickers_since",
    "insider_txns_for_tickers_since",
    "fetch_log_for_tickers",
    "is_biotech_ticker",
    "load_biotech_industries",
    "run_enrichment_4c",
    "EnrichResult",
    "SOURCES_ALL",
]
