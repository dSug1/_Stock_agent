"""Build the per-ticker holdings universe for one quarter.

Reads Module 2's per-filing holdings out of 2_fundparser.db, classifies
share types from config/share_types.yaml, aggregates per-fund then
across-funds, and writes:

    Outputs/universe_{quarter}.parquet
    Outputs/dropped_rows_{quarter}.parquet
    Outputs/unresolved_positions_{quarter}.parquet

Default quarter is 'auto-latest' (newest period_of_report in holdings).
Pass --quarter YYYYQn to build for a historical quarter (required for
the feedback-loop replays of later modules).

Usage (from repo root):
    python 2_Funds_parser/scripts/3_build_universe.py
    python 2_Funds_parser/scripts/3_build_universe.py --quarter 2025Q4 -v
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
from module_3 import build_universe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quarter",
        default=None,
        help=(
            "Quarter in YYYYQn format (e.g. 2025Q4). "
            "Default: resolve via pipeline.yaml (usually 'auto-latest')."
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

    config = load_config()
    universe = build_universe(config, quarter=args.quarter)

    print(
        f"Universe built: {len(universe)} tickers, "
        f"quarter={universe['quarter'].iloc[0] if len(universe) else args.quarter}."
    )
    if len(universe):
        verified = int(universe["ticker_is_verified"].sum())
        unknown = int(universe["has_unknown_class"].sum())
        print(
            f"  {verified} tickers ticker_is_verified, "
            f"{unknown} flagged has_unknown_class."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
