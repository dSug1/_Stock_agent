"""Module 5 — fundamentals reader (consumes M4c output).

Pure read-side. No HTTP, no yfinance, no external deps beyond sqlite3.

Loads from `data/fundamentals.db` (M4c output) and returns a per-ticker
dict ready to be embedded into the context pack as `pack.fundamentals`.

Per D54: financials only — no clinical_trials block. Clinical-trial data
stays with M6's web_search.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


_FINANCIALS_PASSTHROUGH_FIELDS = (
    "form", "period", "period_end_date",
    "cash_and_equivalents_usd", "short_term_investments_usd", "cash_total_usd",
    "total_assets_usd", "total_liabilities_usd",
    "accounts_receivable_usd", "ppe_net_usd",
    "rd_expense_ttm_usd", "ga_expense_ttm_usd", "operating_cf_ttm_usd",
    "quarterly_burn_usd", "runway_months",
    "basic_shares_count", "diluted_shares_count",
    "prefunded_warrants_count", "fully_diluted_shares_count",
    "shelf_registration_usd_capacity",
)


def _date_iso_or_none(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    return s.split("T")[0]


def _format_capital_raise(r: dict) -> dict:
    return {
        "date_iso": _date_iso_or_none(r.get("filing_date")),
        "form": r.get("form"),
        "raise_type": r.get("raise_type"),
        "gross_proceeds_usd": r.get("gross_proceeds_usd"),
        "shares_issued": r.get("shares_issued"),
        "price_per_share_usd": r.get("price_per_share_usd"),
        "discount_to_market_pct": r.get("discount_to_market_pct"),
        "description": r.get("description"),
        "raw_filing_url": r.get("raw_filing_url"),
    }


def _format_insider_txn(r: dict) -> dict:
    return {
        "date_iso": _date_iso_or_none(r.get("transaction_date") or r.get("filing_date")),
        "insider_name": r.get("insider_name"),
        "role": r.get("role"),
        "txn_type": r.get("txn_type"),
        "shares": r.get("shares"),
        "price_usd": r.get("price_usd"),
        "total_value_usd": r.get("total_value_usd"),
    }


def load_fundamentals_for_tickers(
    db_path: Path,
    tickers: list[str],
    *,
    raises_lookback_days: int = 365,
    insiders_lookback_days: int = 365,
) -> dict[str, dict]:
    """Returns {ticker: fundamentals_dict}.

    Tickers absent from `data/fundamentals.db` (or with no fetch_log entries)
    receive an empty dict {} — caller's responsibility to detect via
    `fundamentals.get(ticker, {}).get("available", False)`.

    Returns {} when db_path doesn't exist (M4c never run).
    """
    if not db_path.exists() or not tickers:
        return {}

    today = dt.date.today()
    raises_since = (today - dt.timedelta(days=raises_lookback_days)).isoformat()
    insiders_since = (today - dt.timedelta(days=insiders_lookback_days)).isoformat()

    out: dict[str, dict] = {}

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(tickers))

        # Latest financials per ticker.
        latest_fin: dict[str, dict] = {}
        rows = conn.execute(
            f"""
            SELECT f.* FROM financials f
            JOIN (
                SELECT ticker, MAX(period_end_date) AS pmax
                FROM financials
                WHERE ticker IN ({placeholders})
                GROUP BY ticker
            ) m ON m.ticker = f.ticker AND m.pmax = f.period_end_date
            """,
            tickers,
        ).fetchall()
        for r in rows:
            latest_fin[r["ticker"]] = dict(r)

        # Recent capital raises.
        raises_by: dict[str, list[dict]] = {t: [] for t in tickers}
        rows = conn.execute(
            f"""
            SELECT * FROM capital_raises
            WHERE ticker IN ({placeholders}) AND filing_date >= ?
            ORDER BY filing_date ASC
            """,
            list(tickers) + [raises_since],
        ).fetchall()
        for r in rows:
            raises_by[r["ticker"]].append(_format_capital_raise(dict(r)))

        # Recent insider transactions.
        insiders_by: dict[str, list[dict]] = {t: [] for t in tickers}
        rows = conn.execute(
            f"""
            SELECT * FROM insider_transactions
            WHERE ticker IN ({placeholders})
              AND COALESCE(transaction_date, filing_date) >= ?
            ORDER BY COALESCE(transaction_date, filing_date) ASC
            """,
            list(tickers) + [insiders_since],
        ).fetchall()
        for r in rows:
            insiders_by[r["ticker"]].append(_format_insider_txn(dict(r)))

        # Per-source fetch log.
        log_by: dict[str, dict[str, dict]] = {t: {} for t in tickers}
        rows = conn.execute(
            f"SELECT * FROM fetch_log WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        for r in rows:
            log_by[r["ticker"]][r["source"]] = {
                "fetched_at": r["last_fetched_at"],
                "status": r["last_status"],
                "rows_written": r["rows_written"],
                "error": r["last_error"],
            }

    for t in tickers:
        fin = latest_fin.get(t)
        log_rows = log_by.get(t) or {}

        # If there's no fetch_log AND no financials row, this ticker hasn't
        # been touched by M4c. Emit a missing-marker.
        if not fin and not log_rows:
            out[t] = {"available": False, "reason": "no M4c data"}
            continue

        # If every M4c attempt for this ticker was 'skipped_non_biotech',
        # surface that explicitly so M6/M5 know why fundamentals are absent.
        statuses = {row.get("status") for row in log_rows.values()}
        if statuses and statuses.issubset({"skipped_non_biotech"}):
            out[t] = {"available": False, "reason": "skipped_non_biotech"}
            continue
        if statuses and statuses.issubset({"failed"}):
            out[t] = {"available": False, "reason": "all sources failed"}
            continue

        # Compute per-ticker fingerprint for M5 cache invalidation.
        # Use max(last_fetched_at) across this ticker's fetch_log rows.
        fetched_ats = [row.get("fetched_at") for row in log_rows.values() if row.get("fetched_at")]
        fundamentals_fetched_at = max(fetched_ats) if fetched_ats else None

        financials_block: dict = {}
        if fin:
            for k in _FINANCIALS_PASSTHROUGH_FIELDS:
                if k in fin:
                    financials_block[k] = fin[k]

        out[t] = {
            "available": bool(fin),
            "as_of": fin.get("period_end_date") if fin else None,
            "fundamentals_fetched_at": fundamentals_fetched_at,
            "financials": financials_block,
            "recent_capital_raises": raises_by.get(t, []),
            "recent_insider_transactions": insiders_by.get(t, []),
            "data_status": log_rows,
        }

    return out


def fundamentals_fetched_at_for_hash(fundamentals: Optional[dict]) -> str:
    """Returns a stable string for inclusion in M5 source_rank_hash.
    None or missing-marker -> '' (empty string is a stable sentinel)."""
    if not fundamentals:
        return ""
    if not fundamentals.get("available"):
        return f"unavailable:{fundamentals.get('reason') or ''}"
    return str(fundamentals.get("fundamentals_fetched_at") or "")
