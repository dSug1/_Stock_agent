"""Module 3 — ingest SEC EDGAR Schedule 13D/13G filings (metadata only)
for the catalyst-snapshot ticker universe.

v1 records accession, form, filed_date, and a working filing URL per
spec §5.3. `filer_name` and `percent_of_class` are left NULL — both
require fetching + parsing the filing body, deferred to a later
milestone per spec §5.5.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_3_ingest_edgar_13dg.py \\
        [--lookback-days 365] [--tickers AAPL,BMY,...] [--full-refresh] [-v]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402
from module_3.ingest import ingest_13dg_for_tickers  # noqa: E402


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
        help="how far back to look for 13D/G filings (default: 365)",
    )
    parser.add_argument(
        "--tickers", type=str, default=None,
        help="comma-separated subset (default: all tickers in the latest catalyst snapshot)",
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

        print(f"[3_3_ingest_edgar_13dg] {len(tickers)} ticker(s), "
              f"lookback={args.lookback_days}d, "
              f"{'full-refresh' if args.full_refresh else 'incremental'}")

        stats = ingest_13dg_for_tickers(
            tickers, conn,
            lookback_days=args.lookback_days,
            full_refresh=args.full_refresh,
        )
    finally:
        conn.close()

    print()
    print(f"[3_3_ingest_edgar_13dg] {stats.status}")
    print(f"  tickers_requested:    {stats.tickers_requested}")
    print(f"  tickers_unresolved:   {stats.tickers_unresolved}")
    print(f"  tickers_processed:    {stats.tickers_processed}")
    print(f"    new (full lookback): {stats.tickers_new}")
    print(f"    incremental:         {stats.tickers_incremental}")
    print(f"  filings_inserted:     {stats.filings_inserted}")
    if args.verbose:
        errs = [t for t in stats.per_ticker if t.error]
        if errs:
            print("  per-ticker errors:")
            for t in errs[:20]:
                print(f"    {t.ticker:6s} {t.error}")
            if len(errs) > 20:
                print(f"    ... and {len(errs) - 20} more")
    return 0 if stats.status == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
