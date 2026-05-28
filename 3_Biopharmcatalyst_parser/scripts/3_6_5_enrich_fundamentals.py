"""Module 6.5 — FDSC enrichment CLI.

Fetches authoritative shares-outstanding + PFW count + last price for the
M6 hard-pass feed and writes them to data/fundamentals.db.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_5_enrich_fundamentals.py [opts]

Default feed: distinct tickers from catalyst_scores WHERE hard_pass=1
on the most recent snapshot_date.

Options:
  --tickers TCRX,RCKT     comma-separated explicit list (skips hard_pass feed)
  --force-refresh         bypass TTL gates on all sources
  --raises-lookback-days  PFW lookback window (default 730)
  --dry-run               print the feed and exit; no HTTP, no writes

Spec: spec/module_7_spec.md §4.
Decisions: spec/decisions.md § D15.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import DEFAULT_DB_PATH as BIOTECH_DB              # noqa: E402
from module_6_5 import (                                            # noqa: E402
    DEFAULT_DILUTION_WARNING_PCT,
    DEFAULT_LOOKBACK_DAYS,
    FUNDAMENTALS_DB_PATH,
    run_enrichment,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    feed = parser.add_mutually_exclusive_group()
    feed.add_argument("--tickers", default=None,
                      help="Comma-separated tickers (skips the hard-pass feed)")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Bypass companyfacts/capital_raises TTLs")
    parser.add_argument("--raises-lookback-days", type=int,
                        default=DEFAULT_LOOKBACK_DAYS,
                        help=f"PFW lookback window (default {DEFAULT_LOOKBACK_DAYS})")
    parser.add_argument("--pfw-dilution-warning-pct", type=float,
                        default=DEFAULT_DILUTION_WARNING_PCT,
                        help=f"PFW/basic_shares pct that flips the dilution warning "
                             f"(default {DEFAULT_DILUTION_WARNING_PCT:g}%%)")
    parser.add_argument("--today", default=None,
                        help="Override today for replayability (ISO YYYY-MM-DD)")
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB,
                        help="Path to biotech.db (default: data/biotech.db)")
    parser.add_argument("--fundamentals-db", type=Path,
                        default=FUNDAMENTALS_DB_PATH,
                        help="Path to fundamentals.db (default: data/fundamentals.db)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print feed + exit; no HTTP, no writes")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    today = dt.date.fromisoformat(args.today) if args.today else None

    tickers: list[str] | None = None
    if args.tickers:
        tickers = [s.strip().upper() for s in args.tickers.split(",") if s.strip()]
        if not tickers:
            print("error: --tickers given but no valid tickers parsed", file=sys.stderr)
            return 2

    if args.dry_run:
        # Resolve the feed but skip enrichment.
        from module_6_5.enrich import _hard_pass_tickers              # type: ignore
        feed = tickers if tickers else _hard_pass_tickers(args.biotech_db)
        print(f"[3_6_5] dry-run: feed size = {len(feed)}")
        for t in feed:
            print(f"  {t}")
        return 0

    print(f"[3_6_5] biotech.db:      {args.biotech_db}")
    print(f"[3_6_5] fundamentals.db: {args.fundamentals_db}")
    if tickers:
        print(f"[3_6_5] explicit tickers: {','.join(tickers)}")
    else:
        print(f"[3_6_5] feed: hard_pass=1 on latest snapshot")
    if args.force_refresh:
        print(f"[3_6_5] --force-refresh: bypassing TTL gates")

    stats = run_enrichment(
        biotech_db_path=args.biotech_db,
        fundamentals_db_path=args.fundamentals_db,
        tickers=tickers,
        force_refresh=args.force_refresh,
        raises_lookback_days=args.raises_lookback_days,
        pfw_dilution_warning_pct=args.pfw_dilution_warning_pct,
        today=today,
    )

    wall = (stats.finished_at - stats.started_at).total_seconds() \
        if stats.finished_at else 0.0
    print()
    print("-" * 70)
    print(f"Module 6.5 enrichment complete:")
    print(f"  Tickers in feed:          {stats.n_tickers}")
    print(f"  Unresolved (no CIK):      {stats.n_unresolved}")
    print(f"  companyfacts ok:          {stats.n_companyfacts_ok}")
    print(f"  companyfacts skipped TTL: {stats.n_companyfacts_skipped_ttl}")
    print(f"  companyfacts failed:      {stats.n_companyfacts_failed}")
    print(f"  capital_raises written:   {stats.n_raises_written}")
    print(f"  capital_raises skipped:   {stats.n_raises_skipped_ttl}")
    print(f"  prices ok:                {stats.n_price_ok}")
    print(f"  prices failed:            {stats.n_price_failed}")
    print(f"  Wall time:                {wall:.1f}s")
    print("-" * 70)

    if stats.failed:
        print()
        print(f"  Failures ({len(stats.failed)}):")
        for ticker, source, error in stats.failed[:25]:
            print(f"    {ticker:8s} {source:18s} {error[:100]}")
        if len(stats.failed) > 25:
            print(f"    … and {len(stats.failed) - 25} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
