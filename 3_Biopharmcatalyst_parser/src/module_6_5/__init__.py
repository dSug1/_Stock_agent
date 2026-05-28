"""Module 6.5 — Fundamentals + FDSC enrichment.

Sits between M6 (scoring) and M7 (Claude deep-dive). Fetches:
  • SEC EDGAR XBRL companyfacts (cash, R&D, G&A, burn, runway, shares)
  • 8-K / S-3 / 424B5 capital raises (incl. PFW classification)
  • yfinance last close

Writes `data/fundamentals.db` (schema mirrors 2_Funds_parser M4c +
3_Biopharm extensions: last_price_usd, market_cap_fdsc_usd, pfw_source).

Spec: spec/module_7_spec.md §4.
Decisions: spec/decisions.md § D15.
"""
from __future__ import annotations

from .edgar_client import (
    CompanyFactsResult,
    FilingsResult,
    classify_raise_type,
    fetch_capital_raise,
    fetch_companyfacts,
    fetch_recent_filings,
)
from .enrich import (
    DEFAULT_TTLS,
    EnrichStats,
    run_enrichment,
)
from .fundamentals_db import (
    DEFAULT_DB_PATH as FUNDAMENTALS_DB_PATH,
    capital_raises_for_tickers_since,
    db_connect,
    fetch_log_for_tickers,
    get_fetch_log,
    init_fundamentals_db,
    latest_financials_for_tickers,
    upsert_capital_raise,
    upsert_fetch_log,
    upsert_financials_row,
)
from .pfw_estimator import (
    DEFAULT_DILUTION_WARNING_PCT,
    DEFAULT_LOOKBACK_DAYS,
    PfwEstimate,
    estimate_pfw,
)
from .price_client import PriceResult, fetch_last_close, fetch_last_close_batch

__all__ = [
    # config + db
    "FUNDAMENTALS_DB_PATH",
    "init_fundamentals_db", "db_connect",
    "get_fetch_log", "upsert_fetch_log",
    "upsert_financials_row", "upsert_capital_raise",
    "latest_financials_for_tickers", "capital_raises_for_tickers_since",
    "fetch_log_for_tickers",
    # edgar
    "CompanyFactsResult", "FilingsResult",
    "fetch_companyfacts", "fetch_recent_filings", "fetch_capital_raise",
    "classify_raise_type",
    # prices
    "PriceResult", "fetch_last_close", "fetch_last_close_batch",
    # pfw
    "PfwEstimate", "estimate_pfw",
    "DEFAULT_DILUTION_WARNING_PCT", "DEFAULT_LOOKBACK_DAYS",
    # orchestrator
    "EnrichStats", "DEFAULT_TTLS", "run_enrichment",
]
