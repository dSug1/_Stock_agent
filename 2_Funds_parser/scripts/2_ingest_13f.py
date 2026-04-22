"""Ingest 13F-HR filings for every fund in the registry.

Consults EDGAR for each fund's submissions index, downloads filings
filed in [--from-date, --to-date], parses them, resolves CUSIPs via
OpenFIGI (cached), and stores holdings + filings_log rows.

Idempotent: filings already present in filings_log (by fund_id +
accession_number) are skipped without re-downloading. Running the
script a second time after a successful run is a no-op until new
13F-HR filings appear at the SEC.

Usage (from repo root):
    python 2_Funds_parser/scripts/2_ingest_13f.py
    python 2_Funds_parser/scripts/2_ingest_13f.py --from-date 2024-01-01
    python 2_Funds_parser/scripts/2_ingest_13f.py --to-date 2026-04-01
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
from layer_1.edgar_13f import (  # noqa: E402
    DEFAULT_FROM_DATE,
    ingest_all_funds,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-date",
        default=DEFAULT_FROM_DATE,
        help=(
            "Ingest filings with filing_date >= this ISO date "
            f"(default {DEFAULT_FROM_DATE})."
        ),
    )
    parser.add_argument(
        "--to-date",
        default=None,
        help=(
            "Ingest filings with filing_date <= this ISO date "
            "(default: today)."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable INFO-level logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    conn = get_connection()
    try:
        summary = ingest_all_funds(
            conn,
            from_date=args.from_date,
            to_date=args.to_date,
        )
    finally:
        conn.close()

    backfill = summary.pop("_backfill", None)
    if backfill and backfill["filings_scanned"]:
        print(
            f"Backfill (name_of_issuer): "
            f"{backfill['filings_scanned']} filings rescanned, "
            f"{backfill['rows_updated']} rows updated, "
            f"{backfill['errors']} errors."
        )

    share_type_bf = summary.pop("_share_type_backfill", None)
    if share_type_bf and share_type_bf["filings_scanned"]:
        print(
            f"Backfill (title_of_class + put_call): "
            f"{share_type_bf['filings_scanned']} filings rescanned, "
            f"{share_type_bf['rows_updated']} rows updated, "
            f"{share_type_bf['errors']} errors."
        )

    ticker_bf = summary.pop("_ticker_backfill", None)
    if ticker_bf and ticker_bf["distinct_names"]:
        print(
            f"Backfill (ticker via SEC name match): "
            f"{ticker_bf['distinct_names']} distinct names, "
            f"{ticker_bf['names_matched']} matched, "
            f"{ticker_bf['rows_updated']} rows updated."
        )

    total_filings = 0
    total_holdings = 0
    total_skipped = 0
    for fund_name, stats in summary.items():
        total_filings += stats["filings_processed"]
        total_holdings += stats["holdings_inserted"]
        total_skipped += stats["filings_skipped"]
        if any(stats.values()):
            print(
                f"{fund_name}: "
                f"{stats['filings_processed']} filings ingested, "
                f"{stats['holdings_inserted']} holdings, "
                f"{stats['filings_skipped']} skipped, "
                f"{stats['cusips_unresolved']} cusips unresolved, "
                f"{stats['filings_empty']} empty, "
                f"{stats['filings_error']} error"
            )

    print(
        f"Total: {total_filings} filings ingested, "
        f"{total_holdings} holdings inserted, "
        f"{total_skipped} filings skipped (already in DB)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
