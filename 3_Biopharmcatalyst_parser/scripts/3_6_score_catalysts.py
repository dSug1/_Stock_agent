"""Module 6 — Score every catalyst at a snapshot_date and write
catalyst_scores rows.

Default: process the most recent snapshot. Use --all-snapshots to
re-score historical snapshots (e.g., after a scoring.yaml edit bumps
rules_version).

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_6_score_catalysts.py [opts]
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
from module_6.config import default_config_path, load_scoring_config  # noqa: E402
from module_6.ingest import (  # noqa: E402
    all_snapshot_dates,
    latest_snapshot_date,
    score_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-date", type=date.fromisoformat, default=None,
        help="ISO snapshot date to score (default: most recent in catalyst_snapshots)",
    )
    parser.add_argument(
        "--all-snapshots", action="store_true",
        help="re-score every snapshot ever loaded (use after a scoring.yaml edit)",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="path to scoring.yaml (default: config/scoring.yaml)",
    )
    parser.add_argument(
        "--skip-funds", action="store_true",
        help="bypass cross-DB read of 2_Funds_parser/2_fundparser.db; renormalises remaining weights",
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

    cfg_path = args.config or default_config_path()
    if not cfg_path.exists():
        print(f"error: scoring config not found at {cfg_path}", file=sys.stderr)
        return 2
    cfg = load_scoring_config(cfg_path)
    print(f"[3_6_score_catalysts] config: {cfg_path}")
    print(f"[3_6_score_catalysts] rules_version: {cfg.rules_version}")

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
            print("[3_6_score_catalysts] no snapshots to process")
            return 0

        any_failed = False
        for snap in targets:
            print(f"[3_6_score_catalysts] scoring snapshot {snap.isoformat()}…")
            stats = score_snapshot(
                snap, conn, cfg,
                project_root=PROJECT_ROOT,
                skip_funds=args.skip_funds,
            )
            print(f"  status:               {stats.status}")
            print(f"  rows_in:              {stats.rows_in}")
            print(f"  rows_inserted:        {stats.rows_inserted}")
            print(f"  rows_updated:         {stats.rows_updated}")
            print(f"  hard_pass count:      {stats.hard_pass_count}")
            if stats.bucket_counts:
                print(f"  bucket breakdown:")
                for b in ("catalyst_date_defined", "catalyst_date_undefined"):
                    n = stats.bucket_counts.get(b, 0)
                    if n:
                        print(f"    {b:26s} {n:>4d}")
            if stats.fail_reason_counts:
                print(f"  fail_reasons (per rule, can overlap):")
                for code in ("H1", "H2", "H3", "H4", "H5"):
                    n = stats.fail_reason_counts.get(code, 0)
                    if n:
                        print(f"    {code:4s} {n:>4d}")
            if stats.funds_skipped:
                print(f"  funds:                SKIPPED (composite uses 2-signal renormalisation)")
            else:
                if stats.funds_quarter_latest:
                    flag = " (STALE)" if stats.funds_stale else ""
                    print(f"  funds quarter_latest: {stats.funds_quarter_latest}{flag}")
                    print(f"  funds quarter_prev:   {stats.funds_quarter_previous}")
            if stats.status not in ("success", "partial"):
                any_failed = True
        return 1 if any_failed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
