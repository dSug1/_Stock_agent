"""Module 6.5 — FDSC enrichment orchestrator.

For each (ticker) in the feed:
  1. Resolve ticker → CIK via the existing ticker_cik_map cache.
  2. Fetch companyfacts XBRL (basic_shares + cash + R&D + burn + runway).
  3. Fetch recent 8-K Items 1.01/3.02 + S-3 + 424B5 filings (capital raises).
  4. Fetch last close from yfinance.
  5. Estimate PFW count from PFW-classified raises (heuristic v1).
  6. Compute FDSC market cap = (basic + PFW) × last_close.
  7. Upsert one financials row + N capital_raises rows.

Fail-open: a per-ticker failure logs to fetch_log and continues. The
caller's pack builder treats a missing financials row as "no fundamentals
available; Claude must web_search".

TTLs (gated against fetch_log.last_fetched_at):
  - companyfacts:    30 days
  - capital_raises:  14 days  (8-K cadence is high during raise season)
  - prices:          1 day    (close-of-business refresh; intraday not v1)

Spec: spec/module_7_spec.md §4.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .edgar_client import (
    CompanyFactsResult,
    FilingsResult,
    fetch_capital_raise,
    fetch_companyfacts,
    fetch_recent_filings,
)
from .fundamentals_db import (
    db_connect,
    get_fetch_log,
    upsert_capital_raise,
    upsert_fetch_log,
    upsert_financials_row,
)
from .pfw_estimator import (
    DEFAULT_DILUTION_WARNING_PCT,
    DEFAULT_LOOKBACK_DAYS,
    estimate_pfw,
)
from .price_client import fetch_last_close_batch

log = logging.getLogger(__name__)


# Forms that potentially carry a capital raise / PFW.
_RAISE_FORMS = ("8-K", "S-3", "S-3/A", "424B5")

# Per-source TTLs (days).
DEFAULT_TTLS = {
    "companyfacts":    30,
    "capital_raises":  14,
    "price":            1,
}


@dataclass
class EnrichStats:
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    finished_at: Optional[dt.datetime] = None
    n_tickers: int = 0
    n_unresolved: int = 0
    n_companyfacts_ok: int = 0
    n_companyfacts_skipped_ttl: int = 0
    n_companyfacts_failed: int = 0
    n_raises_written: int = 0
    n_raises_skipped_ttl: int = 0
    n_price_ok: int = 0
    n_price_failed: int = 0
    failed: list[tuple[str, str, str]] = field(default_factory=list)  # (ticker, source, error)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _days_old(iso_ts: Optional[str], now: dt.datetime) -> float:
    if not iso_ts:
        return float("inf")
    s = iso_ts.rstrip("Z").split(".")[0]
    if "+" in s:
        s = s.split("+")[0]
    try:
        ts = dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc) \
             if "T" in s else dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return float("inf")
    return (now - ts).total_seconds() / 86400.0


def _resolve_ciks(
    biotech_db_path: Path, tickers: list[str], *, allow_refresh: bool = True
) -> tuple[dict[str, str], list[str]]:
    """Use the existing 3_Biopharm M2 ticker_cik_map cache."""
    # Local import to avoid a top-level circular import (database.db
    # initializes the M0 schema as a side-effect).
    from database.db import get_connection                          # type: ignore
    from module_2.ticker_cik import resolve_tickers                 # type: ignore
    conn = get_connection(biotech_db_path)
    try:
        return resolve_tickers(conn, tickers, allow_refresh=allow_refresh)
    finally:
        conn.close()


def _hard_pass_tickers(biotech_db_path: Path) -> list[str]:
    """Default feed: distinct tickers from the **rolling view** of
    catalyst_scores — i.e., the latest score row per unique catalyst PK
    (ticker, drug, nct_number, next_catalyst_type) across ALL snapshots,
    where that latest row has hard_pass=1.

    This matches what `Outputs/catalyst_scores.html` displays by default
    (per D11+D12), which is what the user reviews before selecting tickers
    for the M7 deep-dive. Anchoring on the latest snapshot only would
    surface a strict subset (just 10 tickers on the 2026-05-28 v5 snapshot
    vs. 52 across the rolling window).
    """
    from database.db import get_connection                          # type: ignore
    conn = get_connection(biotech_db_path)
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT cs.ticker
            FROM catalyst_scores cs
            JOIN (
                SELECT ticker, drug, nct_number, next_catalyst_type,
                       MAX(snapshot_date) AS m
                FROM catalyst_scores
                GROUP BY 1, 2, 3, 4
            ) latest
              USING (ticker, drug, nct_number, next_catalyst_type)
            WHERE cs.snapshot_date = latest.m AND cs.hard_pass = 1
            ORDER BY cs.ticker
            """
        ).fetchall()
    finally:
        conn.close()
    return [r["ticker"] for r in rows]


def run_enrichment(
    *,
    biotech_db_path: Path,
    fundamentals_db_path: Optional[Path] = None,
    tickers: Optional[list[str]] = None,
    force_refresh: bool = False,
    ttls_days: Optional[dict[str, int]] = None,
    raises_lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    pfw_dilution_warning_pct: float = DEFAULT_DILUTION_WARNING_PCT,
    today: Optional[dt.date] = None,
) -> EnrichStats:
    """Run the four enrichment steps for `tickers` (or the hard-pass feed)."""
    stats = EnrichStats()
    today = today or dt.date.today()
    ttls = {**DEFAULT_TTLS, **(ttls_days or {})}
    now_utc = dt.datetime.now(dt.timezone.utc)

    feed = list(tickers) if tickers else _hard_pass_tickers(biotech_db_path)
    feed = sorted({t.strip().upper() for t in feed if t and t.strip()})
    stats.n_tickers = len(feed)
    if not feed:
        stats.finished_at = dt.datetime.now(dt.timezone.utc)
        return stats

    cik_map, unresolved = _resolve_ciks(biotech_db_path, feed)
    stats.n_unresolved = len(unresolved)
    for t in unresolved:
        stats.failed.append((t, "ticker_cik", "unresolved"))

    resolved_tickers = sorted(cik_map.keys())

    # ── Step 1: prices (batched up front, cheap) ─────────────────────────────
    price_results = fetch_last_close_batch(resolved_tickers)
    for t, pr in price_results.items():
        if pr.status == "ok":
            stats.n_price_ok += 1
        else:
            stats.n_price_failed += 1
            stats.failed.append((t, "price", pr.error or pr.status))

    # ── Steps 2 + 3: per-ticker XBRL + capital raises + write ────────────────
    with db_connect(fundamentals_db_path) as conn:
        prior_log = get_fetch_log(conn, resolved_tickers)

        for ticker in resolved_tickers:
            cik = cik_map[ticker]

            # 2a. companyfacts (TTL-gated unless --force-refresh)
            cf_age = _days_old(
                (prior_log.get((ticker, "companyfacts")) or {}).get("last_fetched_at"),
                now_utc,
            )
            cf_rows: list[dict] = []
            cf_status = "skipped_ttl"
            cf_error: Optional[str] = None
            cf_raw_payload: Optional[str] = None
            if force_refresh or cf_age > ttls["companyfacts"]:
                res = fetch_companyfacts(cik)
                cf_status = res.status
                cf_error = res.error
                cf_rows = res.rows
                cf_raw_payload = res.raw_payload
                if res.status == "ok":
                    stats.n_companyfacts_ok += 1
                elif res.status == "failed":
                    stats.n_companyfacts_failed += 1
                    stats.failed.append((ticker, "companyfacts", res.error or ""))
            else:
                stats.n_companyfacts_skipped_ttl += 1

            # 2b. capital raises (TTL-gated)
            raises_age = _days_old(
                (prior_log.get((ticker, "capital_raises")) or {}).get("last_fetched_at"),
                now_utc,
            )
            raises_rows: list[dict] = []
            raises_status = "skipped_ttl"
            raises_error: Optional[str] = None
            if force_refresh or raises_age > ttls["capital_raises"]:
                since = (today - dt.timedelta(days=raises_lookback_days)).isoformat()
                fres: FilingsResult = fetch_recent_filings(
                    cik, since_date_iso=since, forms=_RAISE_FORMS,
                )
                if fres.status == "ok":
                    for filing in fres.filings:
                        row = fetch_capital_raise(cik, filing)
                        if row is None:
                            continue
                        raises_rows.append(row)
                    raises_status = "ok"
                else:
                    raises_status = "failed"
                    raises_error = fres.error
                    stats.failed.append((ticker, "capital_raises", fres.error or ""))
            else:
                stats.n_raises_skipped_ttl += 1

            # 3. Persist this run's capital_raises FIRST so the subsequent
            #    PFW estimate reads from a clean post-upsert view of the
            #    table. Pre-fix bug: prior-run bogus values (e.g. 48B PFW
            #    shares from buggy gross/pps math) lived in capital_raises
            #    and were summed into the new estimate before being
            #    overwritten — yielding ~48B PFW counts on the financials
            #    row even after the parser fix. Upserting before estimating
            #    breaks that cycle.
            now_str_raises = _now_iso()
            for r in raises_rows:
                row = dict(r)
                row["ticker"]     = ticker
                row["cik"]        = cik
                row["fetched_at"] = now_str_raises
                upsert_capital_raise(conn, row)
                stats.n_raises_written += 1

            # 4. PFW estimate — read post-upsert capital_raises so the sum
            #    reflects the LATEST parser's output, not any prior-run
            #    bogus values that have just been overwritten.
            all_pfw_rows = conn.execute(
                """
                SELECT shares_issued, filing_date FROM capital_raises
                WHERE ticker = ? AND raise_type = 'pfw'
                """, (ticker,),
            ).fetchall()
            all_pfw = [
                {"shares_issued": r["shares_issued"], "filing_date": r["filing_date"]}
                for r in all_pfw_rows
            ]

            # 5. Pull the latest companyfacts row to know basic_shares.
            latest_cf = cf_rows[0] if cf_rows else None
            if latest_cf is None:
                # Fall back to existing financials row if M6.5 ran previously.
                existing = conn.execute(
                    """
                    SELECT * FROM financials WHERE ticker = ?
                    ORDER BY period_end_date DESC LIMIT 1
                    """, (ticker,),
                ).fetchone()
                latest_cf = dict(existing) if existing else None

            basic_shares = (latest_cf or {}).get("basic_shares_count")
            pfw = estimate_pfw(
                ticker,
                pfw_raises=all_pfw,
                basic_shares_count=basic_shares,
                lookback_days=raises_lookback_days,
                today=today,
                dilution_warning_pct=pfw_dilution_warning_pct,
            )

            # 5. Live price + FDSC market cap.
            pr = price_results.get(ticker)
            last_price = pr.last_price_usd if pr and pr.status == "ok" else None
            last_price_as_of = pr.last_price_as_of if pr and pr.status == "ok" else None
            fdsc = None
            if basic_shares is not None and pfw.prefunded_warrants_count is not None:
                fdsc = int(basic_shares) + int(pfw.prefunded_warrants_count)
            mcap = (fdsc * last_price) if (fdsc and last_price) else None

            # 6. Persist financials (one row per period — latest_cf gets enriched
            #    with price + FDSC; older periods just get re-upserted with their
            #    original cf payload).
            now_str = _now_iso()
            for i, r in enumerate(cf_rows or ([latest_cf] if latest_cf else [])):
                if r is None:
                    continue
                row = dict(r)
                row.setdefault("ticker", ticker)
                row["cik"] = cik
                row["fetched_at"] = now_str
                row["fetch_status"] = cf_status
                row["fetch_error"] = cf_error
                # Only the latest period carries PFW + price + FDSC.
                if i == 0:
                    row["prefunded_warrants_count"] = pfw.prefunded_warrants_count
                    row["pfw_source"] = pfw.pfw_source
                    row["pfw_share_dilution_warning"] = (
                        None if pfw.pfw_share_dilution_warning is None
                        else int(pfw.pfw_share_dilution_warning)
                    )
                    bs = row.get("basic_shares_count")
                    if bs is not None and pfw.prefunded_warrants_count is not None:
                        row["fully_diluted_shares_count"] = int(bs) + int(pfw.prefunded_warrants_count)
                    row["last_price_usd"]      = last_price
                    row["last_price_as_of"]    = last_price_as_of
                    row["market_cap_fdsc_usd"] = mcap
                    row["companyfacts_raw_json"] = cf_raw_payload
                upsert_financials_row(conn, row)

            # 7. Update fetch_log (per source). capital_raises was already
            #    upserted in step 3 above.
            if cf_status != "skipped_ttl":
                upsert_fetch_log(
                    conn, ticker=ticker, source="companyfacts",
                    last_fetched_at=now_str, last_status=cf_status,
                    last_error=cf_error, rows_written=len(cf_rows),
                )
            if raises_status != "skipped_ttl":
                upsert_fetch_log(
                    conn, ticker=ticker, source="capital_raises",
                    last_fetched_at=now_str, last_status=raises_status,
                    last_error=raises_error, rows_written=len(raises_rows),
                )
            upsert_fetch_log(
                conn, ticker=ticker, source="price",
                last_fetched_at=now_str,
                last_status=(pr.status if pr else "failed"),
                last_error=(pr.error if pr else "no price result"),
                rows_written=1 if (pr and pr.status == "ok") else 0,
            )

            conn.commit()                              # per-ticker commit

    stats.finished_at = dt.datetime.now(dt.timezone.utc)
    return stats
