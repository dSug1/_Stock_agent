"""Module 1 — ingest a BPC FDA-calendar CSV into catalyst_snapshots.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_1_ingest_catalysts.py [csv] [--snapshot-date YYYY-MM-DD]

If `csv` is omitted, picks the most recent *.csv at the top of _csv_source/
(archived copies under _csv_source/archive/ are ignored).
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402
from module_1.ingest import SchemaValidationError, ingest_catalyst_csv  # noqa: E402


def _default_csv() -> Path | None:
    """Most recent *catalyst*.csv at the top of _csv_source/.

    Narrower than `*.csv` on purpose: the same folder also holds
    *insider*.csv files for Module 4, and picking those would fail the
    Module 1 header check.
    """
    src = PROJECT_ROOT / "_csv_source"
    if not src.exists():
        return None
    candidates = [p for p in src.glob("*.csv")
                  if p.is_file() and "catalyst" in p.name.lower()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv_path", nargs="?", type=Path, default=None,
        help="path to BPC catalyst CSV (default: most recent *.csv in _csv_source/)",
    )
    parser.add_argument(
        "--snapshot-date", type=date.fromisoformat,
        default=datetime.now(timezone.utc).date(),
        help="ISO date tagging this snapshot (default: today UTC)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate + count but don't write to DB or archive",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    csv_path = args.csv_path or _default_csv()
    if csv_path is None:
        print("error: no csv_path supplied and no *.csv found in _csv_source/",
              file=sys.stderr)
        return 2
    if not csv_path.exists():
        print(f"error: csv not found: {csv_path}", file=sys.stderr)
        return 2

    conn = get_connection()
    try:
        stats = ingest_catalyst_csv(
            csv_path, args.snapshot_date, conn, dry_run=args.dry_run,
        )
    except SchemaValidationError as e:
        print(f"[3_1_ingest_catalysts] FAILED — {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"[3_1_ingest_catalysts] {stats.status} — {csv_path.name} @ {args.snapshot_date}")
    print(f"  rows_in:       {stats.rows_in}")
    print(f"  rows_inserted: {stats.rows_inserted}")
    print(f"  rows_updated:  {stats.rows_updated}")
    print(f"  rows_rejected: {stats.rows_rejected}")
    if stats.rejections and args.verbose:
        print("  first rejections:")
        for idx, tkr, reason in stats.rejections[:20]:
            print(f"    row {idx} {tkr}: {reason}")
        if len(stats.rejections) > 20:
            print(f"    ... and {len(stats.rejections) - 20} more")
    return 0 if stats.status in ("success", "partial", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())
