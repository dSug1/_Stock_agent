"""Module 4c — fundamentals enrichment CLI (financials only, biotech).

Pre-fetches free SEC EDGAR data into `data/fundamentals.db` for
biotech-industry tickers in the M4b ranked feed. Idempotent (per-source
TTL gating). Clinical-trial data stays with M6's web_search per D54.

Usage:
  python scripts/4c_enrich_fundamentals.py
  python scripts/4c_enrich_fundamentals.py --quarter 2025Q4 -v
  python scripts/4c_enrich_fundamentals.py --ticker NTLA -v
  python scripts/4c_enrich_fundamentals.py --tickers NTLA,TCRX,BCYC -v
  python scripts/4c_enrich_fundamentals.py --source companyfacts -v
  python scripts/4c_enrich_fundamentals.py --force-refresh
  python scripts/4c_enrich_fundamentals.py --dry-run -v
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

from module_1 import load_config  # noqa: E402
from module_4c import run_enrichment_4c  # noqa: E402


def _parse_tickers(args: argparse.Namespace) -> list[str] | None:
    if args.ticker:
        return [args.ticker]
    if args.tickers:
        return [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    return None


def _parse_sources(args: argparse.Namespace) -> list[str] | None:
    if not args.source:
        return None
    return [s.strip() for s in args.source.split(",") if s.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quarter", default=None,
                        help="Quarter YYYYQn (default: pipeline.yaml -> auto-latest)")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--ticker", default=None, help="Single ticker")
    g.add_argument("--tickers", default=None, help="Comma-separated tickers")
    parser.add_argument("--source", default=None,
                        help="Restrict to one or more sources (comma-separated): "
                             "companyfacts | submissions | form4 | capital_raises")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Ignore TTLs; re-fetch every selected (ticker, source)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve work plan + log; no HTTP, no DB writes")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    explicit_tickers = _parse_tickers(args)
    sources = _parse_sources(args)

    result = run_enrichment_4c(
        config,
        quarter=args.quarter,
        explicit_tickers=explicit_tickers,
        sources=sources,
        force_refresh=args.force_refresh,
        dry_run=args.dry_run,
    )

    print()
    print(f"Module 4c {'DRY-RUN ' if args.dry_run else ''}complete "
          f"(quarter={result.quarter}):")
    print(f"  Tickers in feed:        {result.n_tickers_total}")
    print(f"  Biotech (enriched):     {result.n_biotech}")
    print(f"  Non-biotech (skipped):  {result.n_non_biotech}")
    if result.n_no_cik:
        print(f"  No SEC CIK (failed):    {result.n_no_cik}")
    if not args.dry_run:
        print("  Rows written by source:")
        for src, n in result.rows_written.items():
            print(f"    {src:18s} {n:>6d}")
        if result.skipped_ttl:
            print(f"  TTL-skipped: {len(result.skipped_ttl)} (ticker, source) pairs")
        if result.failed:
            print(f"  Failures: {len(result.failed)}")
            for t, src, err in result.failed[:20]:
                print(f"    {t:8s} {src:14s} {err[:80]}")
            if len(result.failed) > 20:
                print(f"    ... and {len(result.failed) - 20} more")
    print(f"  Wall: {result.wall_seconds:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
