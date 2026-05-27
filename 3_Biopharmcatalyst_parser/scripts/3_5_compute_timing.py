"""Module 5 — derive (date_min, date_max, precision_tier) for every
catalyst_snapshots row at a given snapshot_date and upsert into
catalyst_timing.

Default: process the most recent snapshot_date present in
catalyst_snapshots. Use --all-snapshots after a RULES_VERSION bump
(spec §7.12).

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_5_compute_timing.py [opts]
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
from module_5.ingest import (  # noqa: E402
    all_snapshot_dates,
    compute_timing_for_snapshot,
    latest_snapshot_date,
)
from module_5.timing_rules import RULES_VERSION  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-date", type=date.fromisoformat, default=None,
        help="ISO snapshot date to process (default: most recent in catalyst_snapshots)",
    )
    parser.add_argument(
        "--all-snapshots", action="store_true",
        help="recompute timing for every snapshot ever loaded (use after a RULES_VERSION bump)",
    )
    parser.add_argument(
        "--rules-version-override", default=None,
        help=f"persist this version label instead of the default ({RULES_VERSION})",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if args.snapshot_date and args.all_snapshots:
        print("error: --snapshot-date and --all-snapshots are mutually exclusive",
              file=sys.stderr)
        return 2

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    rules_version = args.rules_version_override or RULES_VERSION

    conn = get_connection()
    try:
        if args.all_snapshots:
            targets = all_snapshot_dates(conn)
        elif args.snapshot_date:
            targets = [args.snapshot_date]
        else:
            latest = latest_snapshot_date(conn)
            if latest is None:
                print("error: catalyst_snapshots is empty — run Module 1 first",
                      file=sys.stderr)
                return 2
            targets = [latest]

        if not targets:
            print("[3_5_compute_timing] no snapshots to process")
            return 0

        any_failed = False
        for snap in targets:
            print(f"[3_5_compute_timing] processing snapshot {snap.isoformat()}…")
            stats = compute_timing_for_snapshot(
                snap, conn, rules_version=rules_version,
            )
            print(f"  status:        {stats.status}")
            print(f"  rows_in:       {stats.rows_in}")
            print(f"  rows_inserted: {stats.rows_inserted}")
            print(f"  rows_updated:  {stats.rows_updated}")
            print(f"  precision_tier breakdown:")
            for tier in ("specific", "conference", "month", "quarter",
                         "half", "year", "unknown"):
                n = stats.tier_counts.get(tier, 0)
                if n:
                    print(f"    {tier:14s} {n:>4d}")
            print(f"  source_lane breakdown:")
            for lane in ("conference", "catalyst_date_specific", "text_parse",
                         "catalyst_date_bucket", "unknown"):
                n = stats.lane_counts.get(lane, 0)
                if n:
                    print(f"    {lane:24s} {n:>4d}")
            if stats.status != "success":
                any_failed = True
        return 1 if any_failed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
