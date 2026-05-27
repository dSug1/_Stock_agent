"""Module 3 — EDGAR Schedule 13D / 13G metadata ingest.

Spec §5. For every ticker in the catalyst universe, walk SEC's
submissions index and record any filing where `form` matches one of
`SC 13D`, `SC 13G`, `SC 13D/A`, `SC 13G/A`. v1 stores only metadata
(accession, form, filed_date, primary_doc URL); the form body is NOT
fetched and `filer_name` / `percent_of_class` are left NULL per
spec §5.5.

Reuses ``module_2.edgar_client`` for the rate-limited HTTP session
and ``module_2.ticker_cik`` for ticker→CIK resolution. No new HTTP
plumbing.

Per-ticker incremental floor: identical to the D5 optimization used
by Module 2 — for an already-seen ticker we floor at
``MAX(filed_date) FROM edgar_ownership_filings WHERE cik_issuer = ?``,
so only new filings are inserted on re-runs.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from module_2.edgar_client import ARCHIVES_BASE, HttpError, list_form_filings, fetch_submissions
from module_2.ticker_cik import resolve_tickers

log = logging.getLogger(__name__)


# Spec §5.3 form-type set. SEC publishes ownership filings under TWO
# coexisting form-name conventions in the submissions API:
#   * Modern: "SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A"
#   * Older/alternative: "SCHEDULE 13D", "SCHEDULE 13G", "SCHEDULE 13D/A", "SCHEDULE 13G/A"
# Both formats can appear inside the *same* CIK's recent[] array — they're
# not era-stratified. Filtering only on the "SC" variants (as the original
# spec §5.3 listed) silently drops a large fraction of real filings.
# See decisions.md D6 calibration update.
OWNERSHIP_FORMS = (
    "SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A",
    "SCHEDULE 13D", "SCHEDULE 13G", "SCHEDULE 13D/A", "SCHEDULE 13G/A",
)


@dataclass
class TickerStats:
    ticker: str
    cik: Optional[str] = None
    mode: str = "new"                       # 'new' | 'incremental' | 'full_refresh'
    since_floor: Optional[str] = None
    filings_in_window: int = 0
    filings_already_in_db: int = 0
    filings_inserted: int = 0
    error: Optional[str] = None


@dataclass
class IngestStats:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    lookback_days: int = 365
    tickers_requested: int = 0
    tickers_unresolved: int = 0
    tickers_processed: int = 0
    tickers_new: int = 0
    tickers_incremental: int = 0
    filings_inserted: int = 0
    status: str = "running"
    error_message: Optional[str] = None
    per_ticker: list[TickerStats] = field(default_factory=list)


_INSERT_OWNERSHIP_SQL = """
INSERT OR IGNORE INTO edgar_ownership_filings
    (accession_number, cik_issuer, ticker, issuer_name, form_type,
     filed_date, filer_name, filing_url, percent_of_class, fetched_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def build_filing_url(cik: str, accession_number: str,
                     primary_doc: Optional[str]) -> str:
    """Construct the SEC archive URL for a filing's primary document.

    Falls back to the accession directory URL if `primary_doc` is
    missing. Always returns a non-empty string so the NOT NULL
    constraint on edgar_ownership_filings.filing_url is satisfied.
    """
    accn_raw = accession_number.replace("-", "")
    root = f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}"
    if primary_doc:
        # Strip any leading xsl-prefix path component (modern filings put
        # the rendered HTML under e.g. xslSCHEDULE_13G_X01/<name>).
        basename = primary_doc.rsplit("/", 1)[-1]
        return f"{root}/{basename}"
    return f"{root}/"


def _per_ticker_floor(
    conn: sqlite3.Connection, cik: str, full_window_floor_iso: str,
) -> tuple[str, str]:
    """Identical pattern to module_2.ingest._per_ticker_floor, but
    queries edgar_ownership_filings instead of edgar_form4_filings.
    See decisions.md D5/D6 for the rationale."""
    row = conn.execute(
        "SELECT MAX(filed_date) FROM edgar_ownership_filings WHERE cik_issuer = ?",
        (cik,),
    ).fetchone()
    last = row[0] if row else None
    if last is None:
        return full_window_floor_iso, "new"
    return (max(full_window_floor_iso, last), "incremental")


def _full_refresh_drop(conn: sqlite3.Connection, cik: str) -> None:
    """Spec §5 full-refresh — drop all 13D/G rows for this CIK so
    the incremental skip is bypassed."""
    conn.execute(
        "DELETE FROM edgar_ownership_filings WHERE cik_issuer = ?",
        (cik,),
    )


def _issuer_name(conn: sqlite3.Connection, cik: str) -> Optional[str]:
    """Best-effort issuer name lookup — prefer the ticker_cik_map cache,
    which we populated when resolving the ticker. Avoids an extra
    submissions JSON fetch just for the company name."""
    row = conn.execute(
        "SELECT name FROM ticker_cik_map WHERE cik = ?", (cik,),
    ).fetchone()
    return row["name"] if row and row["name"] else None


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
            "edgar_13dg",
            stats.started_at.isoformat(timespec="seconds"),
            (stats.finished_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            stats.status,
            input_ref,
            stats.tickers_processed,
            stats.filings_inserted,
            0,                                # never updates (INSERT OR IGNORE)
            stats.tickers_unresolved,
            stats.error_message,
        ),
    )


def ingest_13dg_for_tickers(
    tickers: list[str],
    conn: sqlite3.Connection,
    *,
    lookback_days: int = 365,
    full_refresh: bool = False,
    now_utc: Optional[datetime] = None,
) -> IngestStats:
    now = now_utc or datetime.now(timezone.utc)
    full_window_floor = (now.date() - timedelta(days=lookback_days)).isoformat()
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
                    ticker_since_iso, mode = full_window_floor, "full_refresh"
                else:
                    ticker_since_iso, mode = _per_ticker_floor(
                        conn, cik, full_window_floor,
                    )
                t_stats.mode = mode
                t_stats.since_floor = ticker_since_iso
                if mode == "new":
                    stats.tickers_new += 1
                elif mode == "incremental":
                    stats.tickers_incremental += 1

                filings_result = list_form_filings(
                    cik, forms=OWNERSHIP_FORMS, since_date_iso=ticker_since_iso,
                )
                if filings_result.status != "ok":
                    t_stats.error = f"submissions: {filings_result.error}"
                    log.warning("[%s] %s", ticker, t_stats.error)
                    continue
                t_stats.filings_in_window = len(filings_result.filings)

                existing = {
                    r["accession_number"] for r in conn.execute(
                        "SELECT accession_number FROM edgar_ownership_filings "
                        "WHERE cik_issuer = ?",
                        (cik,),
                    ).fetchall()
                }

                issuer_name = _issuer_name(conn, cik)

                with conn:
                    for f in filings_result.filings:
                        accn = f["accession_number"]
                        if accn in existing:
                            t_stats.filings_already_in_db += 1
                            continue
                        filing_url = build_filing_url(
                            cik, accn, f.get("primary_doc"),
                        )
                        conn.execute(_INSERT_OWNERSHIP_SQL, (
                            accn,
                            cik,
                            ticker,
                            issuer_name,
                            f["form"],
                            f["filing_date"],
                            None,                         # filer_name (v1: NULL)
                            filing_url,
                            None,                         # percent_of_class (v1: NULL)
                            now_iso,
                        ))
                        t_stats.filings_inserted += 1
                        stats.filings_inserted += 1
            except Exception as e:  # noqa: BLE001
                t_stats.error = f"{type(e).__name__}: {e}"
                log.exception("[%s] unhandled error", ticker)
            stats.tickers_processed += 1
            log.info(
                "[%s] mode=%s since=%s in_window=%d already=%d inserted=%d",
                ticker, t_stats.mode, t_stats.since_floor,
                t_stats.filings_in_window, t_stats.filings_already_in_db,
                t_stats.filings_inserted,
            )
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
            log.exception("failed to write ingest_log row for edgar_13dg")

    return stats
