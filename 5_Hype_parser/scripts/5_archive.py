#!/usr/bin/env python
"""Hype Parser forward archive CLI (OD-2).

Weekly snapshot of the forward_only sources whose history is otherwise unrecoverable.
Schedule it (Windows Task Scheduler / run_5_archive.bat) so panel-era history accrues.

    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_archive.py --dry-run   # show what WOULD be fetched (no network)
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_archive.py --run        # fetch + snapshot all forward_only sources
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_archive.py --source mit_tr_10_breakthrough --run
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_archive.py --stats       # per-source archive coverage
"""

import argparse
import logging
import sys
from datetime import datetime, timezone

from hype_parser import archive, db

DEFAULT_DB = "data/hype.db"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hype Parser forward archive (OD-2).")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--run", action="store_true", help="fetch + snapshot forward_only sources")
    p.add_argument("--dry-run", action="store_true",
                   help="list the forward_only sources that WOULD be fetched (no network)")
    p.add_argument("--source", action="append", metavar="SOURCE_ID",
                   help="limit to specific source_id(s); repeatable")
    p.add_argument("--limit", type=int, help="cap the number of sources fetched")
    p.add_argument("--if-stale-days", type=int, metavar="N",
                   help="with --run, skip entirely if the last snapshot is younger than N days "
                        "(lets the main pipeline call it cheaply without crawling every run)")
    p.add_argument("--stats", action="store_true", help="show per-source archive coverage")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    conn = db.connect(args.db)
    try:
        if args.dry_run:
            rows = archive.forward_only_sources(conn, source_ids=args.source)
            if args.limit:
                rows = rows[: args.limit]
            with_url = [r for r in rows if r["url"]]
            print(f"would fetch {len(with_url)} / {len(rows)} forward_only sources "
                  f"({len(rows) - len(with_url)} have no URL yet):")
            for r in rows:
                tag = r["url"] if r["url"] else "(no url)"
                print(f"  {r['source_id']:28} {tag}")
            return 0

        if args.run and args.if_stale_days is not None:
            last = archive.most_recent_snapshot(conn)
            if last:
                try:
                    age_days = (datetime.now(timezone.utc)
                                - datetime.fromisoformat(last)).days
                except ValueError:
                    age_days = None
                if age_days is not None and age_days < args.if_stale_days:
                    print(f"archive fresh (last run {age_days}d ago < "
                          f"{args.if_stale_days}d); skipping")
                    return 0

        if args.run:
            summary = archive.archive_forward_only(
                conn, limit=args.limit, source_ids=args.source)
            print(f"archive: attempted={summary['attempted']} ok={summary['ok']} "
                  f"changed={summary['changed']} errors={summary['errors']} "
                  f"no_url={summary['skipped_no_url']}")
            if args.verbose:
                for r in summary["results"]:
                    print(f"  {r['source_id']:28} {r['status']}"
                          + (" *changed*" if r.get("changed") else ""))

        if args.stats or not (args.run or args.dry_run):
            rows = archive.snapshot_stats(conn)
            if not rows:
                print("(no snapshots yet — run with --run)")
            else:
                print(f"{'source_id':28} {'snaps':>6} {'revs':>5} {'errs':>5}  last_fetch")
                for r in rows:
                    print(f"{r['source_id']:28} {r['n_snapshots']:>6} "
                          f"{r['n_revisions'] or 0:>5} {r['n_errors'] or 0:>5}  "
                          f"{r['last_fetch']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
