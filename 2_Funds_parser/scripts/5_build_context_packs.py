"""Module 5 — build per-ticker context packs from Module 4b ranked output.

Reads:
  _intermediate_outputs/ranked_candidates_{quarter}.parquet
  data/prices.db
  config/enrichment.yaml
  config/enrichment_narratives.yaml

Writes:
  context_packs.db                                  (primary store + cache)
  _intermediate_outputs/pack_exclusions_{quarter}.parquet
  Outputs/enrichment_report_{quarter}.html

Usage:
  python scripts/5_build_context_packs.py
  python scripts/5_build_context_packs.py --quarter 2025Q4 -v
  python scripts/5_build_context_packs.py --force-refresh
  python scripts/5_build_context_packs.py --no-cache
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
from module_5 import run_enrichment  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quarter", default=None,
                        help="Quarter YYYYQn (default: pipeline.yaml -> auto-latest).")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Rebuild every pack; skip cache-hit probe but still upsert.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Rebuild every pack AND skip the DB upsert. For debug runs.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    summary = run_enrichment(
        config,
        quarter=args.quarter,
        force_refresh=args.force_refresh or args.no_cache,
        use_cache=not args.no_cache,
    )

    quarter = args.quarter or config.quarter
    print(
        f"Module 5 complete (quarter={quarter}): "
        f"{len(summary)} packs."
    )
    if len(summary):
        by_status = summary["cache_status"].value_counts()
        for name, n in by_status.items():
            print(f"  {name:12s} {n:>5d}")
        top_arch = summary["archetype"].value_counts().head(6)
        print("  top archetypes:")
        for name, n in top_arch.items():
            print(f"    {name:24s} {n:>5d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
