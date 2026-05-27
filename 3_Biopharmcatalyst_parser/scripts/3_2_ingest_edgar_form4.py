"""Module 2 — fetch SEC EDGAR Form 4 filings for the catalyst-snapshot
ticker universe and parse the non-derivative transactions into
edgar_form4_filings + edgar_form4_transactions.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_2_ingest_edgar_form4.py \\
        [--lookback-days 365] [--tickers AAPL,BMY,...] [--full-refresh] [-v]

Default: process every distinct ticker in the most recent snapshot of
catalyst_snapshots. `--tickers` overrides with an explicit subset.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402
from module_2.ingest import ingest_form4_for_tickers  # noqa: E402


def _tickers_from_latest_snapshot(conn) -> list[str]:
    snap = conn.execute(
        "SELECT MAX(snapshot_date) FROM catalyst_snapshots"
    ).fetchone()[0]
    if not snap:
        return []
    return [
        r["ticker"] for r in conn.execute(
            "SELECT DISTINCT ticker FROM catalyst_snapshots "
            "WHERE snapshot_date = ? ORDER BY ticker",
            (snap,),
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lookback-days", type=int, default=365,
        help="how far back to look for Form 4 filings (default: 365)",
    )
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="comma-separated subset of tickers (default: all tickers in the latest catalyst snapshot)",
    )
    parser.add_argument(
        "--full-refresh", action="store_true",
        help="delete existing rows for the targeted CIKs before re-fetching (default: incremental)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    conn = get_connection()
    try:
        if args.tickers:
            tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        else:
            tickers = _tickers_from_latest_snapshot(conn)
            if not tickers:
                print("error: no tickers in catalyst_snapshots — run Module 1 first",
                      file=sys.stderr)
                return 2

        print(f"[3_2_ingest_edgar_form4] {len(tickers)} ticker(s), "
              f"lookback={args.lookback_days}d, "
              f"{'full-refresh' if args.full_refresh else 'incremental'}")

        stats = ingest_form4_for_tickers(
            tickers, conn,
            lookback_days=args.lookback_days,
            full_refresh=args.full_refresh,
        )
    finally:
        conn.close()

    print()
    print(f"[3_2_ingest_edgar_form4] {stats.status}")
    print(f"  tickers_requested:    {stats.tickers_requested}")
    print(f"  tickers_unresolved:   {stats.tickers_unresolved}")
    print(f"  tickers_processed:    {stats.tickers_processed}")
    print(f"  filings_inserted:     {stats.filings_inserted}")
    print(f"  transactions_inserted:{stats.transactions_inserted}")
    if args.verbose:
        errs = [t for t in stats.per_ticker if t.error or t.filings_failed]
        if errs:
            print("  per-ticker issues:")
            for t in errs[:20]:
                msg = t.error or f"{t.filings_failed} filings failed"
                print(f"    {t.ticker:6s} {msg}")
            if len(errs) > 20:
                print(f"    ... and {len(errs) - 20} more")
    return 0 if stats.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
