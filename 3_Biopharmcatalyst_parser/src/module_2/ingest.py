"""Module 2 orchestrator — fetch EDGAR Form 4 filings for a ticker
universe, parse, upsert into edgar_form4_filings + edgar_form4_transactions.

Incremental by default (spec §4.4): an accession already in
edgar_form4_filings skips the fetch+parse entirely. `--full-refresh`
deletes existing rows for the targeted CIKs before re-loading.

Per-ticker fail-open: a network or parse failure for one ticker doesn't
abort the whole run — it logs the error in `ingest_log` (one row per
batch, not per ticker) and the per-ticker stats expose the breakdown.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from .edgar_client import HttpError, fetch_form4_xml, list_form_filings
from .form4_parser import Form4ParseError, parse_form4_xml
from .ticker_cik import resolve_tickers

log = logging.getLogger(__name__)


@dataclass
class TickerStats:
    ticker: str
    cik: Optional[str] = None
    filings_in_window: int = 0
    filings_already_in_db: int = 0
    filings_fetched: int = 0
    filings_failed: int = 0
    txns_inserted: int = 0
    error: Optional[str] = None


@dataclass
class IngestStats:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    lookback_days: int = 365
    tickers_requested: int = 0
    tickers_unresolved: int = 0
    tickers_processed: int = 0
    filings_inserted: int = 0
    transactions_inserted: int = 0
    status: str = "running"
    error_message: Optional[str] = None
    per_ticker: list[TickerStats] = field(default_factory=list)


_INSERT_FILING_SQL = """
INSERT OR IGNORE INTO edgar_form4_filings
    (accession_number, cik_issuer, ticker, issuer_name,
     reporting_owner_cik, reporting_owner_name,
     is_director, is_officer, is_ten_percent_owner, officer_title,
     filed_date, fetched_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_TXN_SQL = """
INSERT INTO edgar_form4_transactions
    (accession_number, transaction_date, transaction_code,
     transaction_code_meaning, acquired_disposed, shares, price_per_share,
     shares_owned_following, is_open_market, direct_or_indirect)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _existing_accessions(conn: sqlite3.Connection, cik: str) -> set[str]:
    return {
        r["accession_number"]
        for r in conn.execute(
            "SELECT accession_number FROM edgar_form4_filings WHERE cik_issuer = ?",
            (cik,),
        ).fetchall()
    }


def _full_refresh_drop(conn: sqlite3.Connection, cik: str) -> None:
    """Spec §4.4 --full-refresh: drop all rows for this CIK so the
    incremental skip is bypassed."""
    accns = [
        r["accession_number"] for r in conn.execute(
            "SELECT accession_number FROM edgar_form4_filings WHERE cik_issuer = ?",
            (cik,),
        ).fetchall()
    ]
    if not accns:
        return
    qmarks = ",".join("?" * len(accns))
    conn.execute(
        f"DELETE FROM edgar_form4_transactions WHERE accession_number IN ({qmarks})",
        accns,
    )
    conn.execute(
        f"DELETE FROM edgar_form4_filings WHERE accession_number IN ({qmarks})",
        accns,
    )


def _write_ingest_log(conn: sqlite3.Connection, stats: IngestStats,
                      input_ref: str) -> None:
    conn.execute(
        """
        INSERT INTO ingest_log
            (module, started_at, finished_at, status, input_ref,
             rows_in, rows_inserted, rows_updated, rows_rejected, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "edgar_form4",
            stats.started_at.isoformat(timespec="seconds"),
            (stats.finished_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            stats.status,
            input_ref,
            stats.tickers_processed,
            stats.filings_inserted,
            0,                                  # M2 never updates filings (INSERT OR IGNORE)
            stats.tickers_unresolved,           # rejected = unresolved tickers
            stats.error_message,
        ),
    )


def ingest_form4_for_tickers(
    tickers: list[str],
    conn: sqlite3.Connection,
    *,
    lookback_days: int = 365,
    full_refresh: bool = False,
    now_utc: Optional[datetime] = None,
) -> IngestStats:
    now = now_utc or datetime.now(timezone.utc)
    since_iso = (now.date() - timedelta(days=lookback_days)).isoformat()
    now_iso = now.isoformat(timespec="seconds")

    stats = IngestStats(lookback_days=lookback_days)
    stats.tickers_requested = len(tickers)

    ticker_to_cik, unresolved = resolve_tickers(conn, tickers)
    stats.tickers_unresolved = len(unresolved)

    try:
        for ticker, cik in ticker_to_cik.items():
            t_stats = TickerStats(ticker=ticker, cik=cik)
            stats.per_ticker.append(t_stats)
            try:
                if full_refresh:
                    with conn:
                        _full_refresh_drop(conn, cik)

                filings_result = list_form_filings(
                    cik, forms=["4", "4/A"], since_date_iso=since_iso,
                )
                if filings_result.status != "ok":
                    t_stats.error = f"submissions: {filings_result.error}"
                    log.warning("[%s] %s", ticker, t_stats.error)
                    continue
                t_stats.filings_in_window = len(filings_result.filings)

                existing = _existing_accessions(conn, cik)

                for f in filings_result.filings:
                    accn = f["accession_number"]
                    if accn in existing:
                        t_stats.filings_already_in_db += 1
                        continue
                    try:
                        xml = fetch_form4_xml(cik, accn, f.get("primary_doc"))
                    except HttpError as e:
                        t_stats.filings_failed += 1
                        log.warning("[%s] fetch %s failed: %s", ticker, accn, e)
                        continue
                    try:
                        filed = date.fromisoformat(f["filing_date"])
                        filing, txns = parse_form4_xml(
                            xml, accession_number=accn, filed_date=filed,
                        )
                    except Form4ParseError as e:
                        t_stats.filings_failed += 1
                        log.warning("[%s] parse %s failed: %s", ticker, accn, e)
                        continue

                    with conn:
                        cur = conn.execute(_INSERT_FILING_SQL, (
                            filing.accession_number,
                            filing.cik_issuer,
                            ticker,                           # resolved at fetch time
                            filing.issuer_name,
                            filing.reporting_owner_cik,
                            filing.reporting_owner_name,
                            1 if filing.is_director else 0,
                            1 if filing.is_officer else 0,
                            1 if filing.is_ten_percent_owner else 0,
                            filing.officer_title,
                            filing.filed_date.isoformat(),
                            now_iso,
                        ))
                        if cur.rowcount == 0:
                            # Lost an insert race (shouldn't happen single-threaded;
                            # defensive). Skip the transactions to avoid orphans.
                            continue
                        for txn in txns:
                            conn.execute(_INSERT_TXN_SQL, (
                                filing.accession_number,
                                txn.transaction_date.isoformat(),
                                txn.transaction_code,
                                txn.transaction_code_meaning,
                                txn.acquired_disposed,
                                txn.shares,
                                txn.price_per_share,
                                txn.shares_owned_following,
                                1 if txn.is_open_market else 0,
                                txn.direct_or_indirect,
                            ))
                            t_stats.txns_inserted += 1
                        t_stats.filings_fetched += 1
                        stats.filings_inserted += 1
            except Exception as e:  # noqa: BLE001
                t_stats.error = f"{type(e).__name__}: {e}"
                log.exception("[%s] unhandled error", ticker)
            stats.tickers_processed += 1
            log.info(
                "[%s] in_window=%d already=%d fetched=%d failed=%d txns=%d",
                ticker, t_stats.filings_in_window, t_stats.filings_already_in_db,
                t_stats.filings_fetched, t_stats.filings_failed, t_stats.txns_inserted,
            )
        # Single source of truth for the global transactions count.
        stats.transactions_inserted = sum(t.txns_inserted for t in stats.per_ticker)
        stats.status = "success"
    except Exception as e:  # noqa: BLE001
        stats.status = "failed"
        stats.error_message = f"{type(e).__name__}: {e}"
        raise
    finally:
        stats.finished_at = datetime.now(timezone.utc)
        try:
            _write_ingest_log(conn, stats, input_ref=f"{len(tickers)} tickers")
            conn.commit()
        except Exception:
            log.exception("failed to write ingest_log row for edgar_form4")

    return stats
