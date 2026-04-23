"""Module 4a — apply hard filters to the per-quarter universe.

Reads:
  _intermediate_outputs/universe_{quarter}.parquet
  config/filters.yaml
  data/prices.db (snapshot cache; auto-created)

Writes:
  _intermediate_outputs/survivors_{quarter}.parquet
  _intermediate_outputs/hard_filter_rejections_{quarter}.parquet
  Outputs/filter_summary_{quarter}.html

Usage:
  python scripts/4_run_hard_filters.py
  python scripts/4_run_hard_filters.py --quarter 2025Q4 -v
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
from module_4 import run_hard_filters  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", default=None,
                        help="Quarter YYYYQn (default: pipeline.yaml -> auto-latest).")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--market-cap-max-usd", type=float, default=None,
        help=("Maximum market cap (USD) for survivors. If omitted, prompts "
              "interactively (TTY) or falls back to filters.yaml's "
              "market_cap_max_usd_default. Accepts e.g. 3700000000 or 3.7e9."),
    )
    parser.add_argument(
        "--no-prompt", action="store_true",
        help=("Skip the interactive cap prompt; use filters.yaml's "
              "market_cap_max_usd_default. Implied for non-TTY runs."),
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    survivors, rejections = run_hard_filters(
        config,
        quarter=args.quarter,
        user_market_cap_max_usd=int(args.market_cap_max_usd) if args.market_cap_max_usd else None,
        interactive=not args.no_prompt,
    )

    quarter = (
        survivors["quarter"].iloc[0] if len(survivors) and "quarter" in survivors
        else (args.quarter or "auto-latest")
    )
    print(
        f"Module 4a complete (quarter={quarter}): "
        f"{len(survivors)} survivors, {len(rejections)} rejected."
    )
    if len(rejections):
        top = rejections["rejection_reason"].value_counts().head(5)
        for reason, n in top.items():
            print(f"  {reason:30s} {n:>5d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
