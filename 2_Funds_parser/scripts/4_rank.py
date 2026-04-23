"""Module 4b — fetch price history, compute ratios, match archetypes, rank.

Reads:
  _intermediate_outputs/survivors_{quarter}.parquet
  data/prices.db
  config/archetypes.yaml
  config/ranking.yaml

Writes:
  _intermediate_outputs/ranked_candidates_{quarter}.parquet
  _intermediate_outputs/young_ticker_excluded_{quarter}.parquet (if any)
  Outputs/ranking_report_{quarter}.html
  Outputs/ranking_report_{quarter}.xlsx

Usage:
  python scripts/4_rank.py
  python scripts/4_rank.py --quarter 2025Q4 -v
  python scripts/4_rank.py --quarter 2025Q4 --rerank-only
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
from module_4 import rank_universe  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", default=None,
                        help="Quarter YYYYQn (default: pipeline.yaml -> auto-latest).")
    parser.add_argument("--rerank-only", action="store_true",
                        help="Skip yfinance fetch; recompute ratios + ranking from cached prices.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    ranked = rank_universe(
        config, quarter=args.quarter, rerank_only=args.rerank_only,
    )

    quarter = (
        ranked["quarter"].iloc[0] if len(ranked) and "quarter" in ranked
        else (args.quarter or "auto-latest")
    )
    print(
        f"Module 4b complete (quarter={quarter}): "
        f"{len(ranked)} ranked candidates."
    )
    if len(ranked):
        by_arch = ranked["archetype"].value_counts().head(8)
        for name, n in by_arch.items():
            print(f"  {name:24s} {n:>5d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
