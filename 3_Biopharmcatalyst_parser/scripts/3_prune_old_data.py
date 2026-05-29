"""D37 — DB pruning CLI.

Default behavior:
  * **Dry-run mode** — reports what would be deleted, writes nothing.
  * Manual invocation: just run the script. Always safe; never prunes
    without an explicit `--write`.

Modes:
  * `--write`      — actually delete the rows (otherwise dry-run).
  * `--auto`       — no-op unless we're past the next quarterly trigger
                     date AND we haven't pruned since that trigger.
                     Used by the orchestrator bat for hands-off operation.
  * `--force`      — bypass the auto-due check; always prune.

Schedule (per D37):
  Quarterly trigger dates are anchored at ~1.5 months after each 13F
  filing deadline so the 2_Funds_parser DB is settled before we
  reshape biotech.db:
    Jan 1 (post Nov-14 13F)
    Apr 1 (post Feb-14 13F)
    Jul 1 (post May-15 13F)
    Oct 1 (post Aug-14 13F)

Prune actions:
  1. catalyst_scores   — keep top-N snapshots per PK     (default keep_n=3)
  2. catalyst_timing   — same PK + N
  3. catalyst_snapshots — same PK + N (delete children first; FK)
  4. bpc_insider_supplement — keep top-N snapshots per PK + N
  5. claude_deep_dives.web_search_cache — TTL drop, default 90 days
  6. claude_deep_dives.deep_dive_errors  — TTL drop, default 90 days

`deep_dives`, `deep_dive_runs`, EDGAR tables, `ticker_cik_map`,
fundamentals.* are LEFT ALONE per the D37 design discussion.

Usage:
    PYTHONPATH=src python scripts/3_prune_old_data.py                    # dry-run
    PYTHONPATH=src python scripts/3_prune_old_data.py --write            # actually prune
    PYTHONPATH=src python scripts/3_prune_old_data.py --auto             # bat-driven
    PYTHONPATH=src python scripts/3_prune_old_data.py --write --keep-snapshots 1
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import DEFAULT_DB_PATH as BIOTECH_DB, get_connection      # noqa: E402
from database.prune import (                                               # noqa: E402
    execute_snapshot_prune,
    execute_ttl_prune,
    get_last_prune_date,
    is_prune_due,
    most_recent_trigger_on_or_before,
    plan_snapshot_prune,
    plan_ttl_prune,
)

DEEP_DIVES_DB = PROJECT_ROOT / "data" / "claude_deep_dives.db"

log = logging.getLogger(__name__)


SNAPSHOT_TABLES_BIOTECH = [
    # Order matters for delete: children before parents (FK = ON).
    # catalyst_scores + catalyst_timing both FK → catalyst_snapshots.
    ("catalyst_scores",         ("snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type")),
    ("catalyst_timing",         ("snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type")),
    ("catalyst_snapshots",      ("snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type")),
    # bpc_insider_supplement has no FK to catalyst_snapshots; order independent.
    ("bpc_insider_supplement",  ("snapshot_date", "ticker", "insider_name", "filing_date", "buy_sell")),
]

TTL_TABLES_DEEP_DIVES = [
    ("web_search_cache",   "cached_at"),
    ("deep_dive_errors",   "created_at"),
]


# ─── ingest_log helpers ──────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _open_prune_run(conn: sqlite3.Connection, input_ref: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO ingest_log(
            module, started_at, status, input_ref
        ) VALUES (?, ?, ?, ?)
        """,
        ("prune", _now_iso(), "running", input_ref),
    )
    return int(cur.lastrowid)


def _close_prune_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    rows_deleted: int,
    rows_kept: int,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ingest_log SET
            finished_at = ?, status = ?,
            rows_in = ?, rows_inserted = 0, rows_updated = 0,
            rows_rejected = ?, error_message = ?
        WHERE run_id = ?
        """,
        (_now_iso(), status,
         rows_kept + rows_deleted, rows_deleted, error_message, run_id),
    )


# ─── main ────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--write", action="store_true",
                        help="Actually delete rows (default is dry-run).")
    parser.add_argument("--auto", action="store_true",
                        help="No-op unless the quarterly schedule says we're due.")
    parser.add_argument("--force", action="store_true",
                        help="Bypass the --auto due-check.")
    parser.add_argument("--keep-snapshots", type=int, default=3, metavar="N",
                        help="Keep the N most-recent snapshots per catalyst PK. Default 3.")
    parser.add_argument("--web-search-ttl-days", type=int, default=90, metavar="D",
                        help="Drop web_search_cache rows older than D days. Default 90.")
    parser.add_argument("--error-ttl-days", type=int, default=90, metavar="D",
                        help="Drop deep_dive_errors rows older than D days. Default 90.")
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB)
    parser.add_argument("--deep-dives-db", type=Path, default=DEEP_DIVES_DB)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    today = date.today()
    dry_run = not args.write

    # ── schedule gate ───────────────────────────────────────────────
    if args.auto and not args.force:
        # Check the last-prune date against the schedule.
        bio_conn = get_connection(args.biotech_db)
        try:
            last = get_last_prune_date(bio_conn)
        finally:
            bio_conn.close()
        if not is_prune_due(today, last):
            next_trigger = _next_trigger_after(today)
            print(f"[3_prune_old_data] --auto: not due. "
                  f"Last prune={last or 'never'}; "
                  f"next trigger={next_trigger}; today={today}. Skipping.")
            return 0
        print(f"[3_prune_old_data] --auto: due. "
              f"Last prune={last or 'never'}; "
              f"most recent trigger={most_recent_trigger_on_or_before(today)}.")

    mode = "DRY-RUN (no rows will be deleted)" if dry_run else "WRITE (rows WILL be deleted)"
    print(f"[3_prune_old_data] mode: {mode}")
    print(f"[3_prune_old_data] keep_snapshots={args.keep_snapshots} "
          f"web_search_ttl={args.web_search_ttl_days}d "
          f"error_ttl={args.error_ttl_days}d")
    print()

    # ── biotech.db: snapshot-keyed prune ────────────────────────────
    print(f"=== biotech.db ({args.biotech_db}) ===")
    bio_conn = get_connection(args.biotech_db)
    try:
        snapshot_plans = []
        for table, pk in SNAPSHOT_TABLES_BIOTECH:
            plan = plan_snapshot_prune(
                bio_conn, table=table, pk_cols=pk, keep_n=args.keep_snapshots,
            )
            snapshot_plans.append(plan)
            print(f"  {table:30s}  kept={plan.rows_kept:>6,}  would_delete={plan.rows_pruned:>6,}")

        bio_total_kept = sum(p.rows_kept for p in snapshot_plans)
        bio_total_pruned = sum(p.rows_pruned for p in snapshot_plans)

        if not dry_run and bio_total_pruned > 0:
            run_id = _open_prune_run(bio_conn,
                                     f"keep_n={args.keep_snapshots}")
            try:
                bio_conn.execute("BEGIN")
                deleted_total = 0
                for plan in snapshot_plans:
                    n = execute_snapshot_prune(bio_conn, plan)
                    deleted_total += n
                    log.info("biotech.db: deleted %d from %s", n, plan.table)
                _close_prune_run(
                    bio_conn, run_id=run_id,
                    rows_deleted=deleted_total, rows_kept=bio_total_kept,
                )
                bio_conn.execute("COMMIT")
                print(f"  -> COMMITTED biotech.db: {deleted_total:,} rows deleted across "
                      f"{len(snapshot_plans)} tables.")
            except Exception as e:                                  # noqa: BLE001
                bio_conn.execute("ROLLBACK")
                _close_prune_run(
                    bio_conn, run_id=run_id, rows_deleted=0,
                    rows_kept=bio_total_kept,
                    status="failed", error_message=str(e),
                )
                raise
    finally:
        bio_conn.close()

    # ── claude_deep_dives.db: TTL prune ─────────────────────────────
    print()
    print(f"=== claude_deep_dives.db ({args.deep_dives_db}) ===")
    if not args.deep_dives_db.exists():
        print("  deep_dives DB not found; skipping.")
    else:
        dd_conn = sqlite3.connect(args.deep_dives_db)
        dd_conn.row_factory = sqlite3.Row
        try:
            ttl_plans = []
            for table, ts_col in TTL_TABLES_DEEP_DIVES:
                if table == "web_search_cache":
                    ttl = args.web_search_ttl_days
                else:
                    ttl = args.error_ttl_days
                plan = plan_ttl_prune(
                    dd_conn, table=table, timestamp_col=ts_col,
                    ttl_days=ttl, today=today,
                )
                ttl_plans.append(plan)
                print(f"  {table:30s}  kept={plan.rows_kept:>6,}  "
                      f"would_delete={plan.rows_to_delete:>6,}  "
                      f"(cutoff={plan.cutoff_iso})")

            if not dry_run and any(p.rows_to_delete for p in ttl_plans):
                dd_conn.execute("BEGIN")
                try:
                    deleted_total = 0
                    for plan in ttl_plans:
                        n = execute_ttl_prune(dd_conn, plan)
                        deleted_total += n
                        log.info("deep_dives.db: deleted %d from %s", n, plan.table)
                    dd_conn.execute("COMMIT")
                    print(f"  -> COMMITTED deep_dives.db: {deleted_total:,} rows deleted.")
                except Exception:
                    dd_conn.execute("ROLLBACK")
                    raise
        finally:
            dd_conn.close()

    # ── VACUUM (optional) ───────────────────────────────────────────
    if not dry_run and (bio_total_pruned > 0 or any(p.rows_to_delete for p in ttl_plans)):
        print()
        print("[3_prune_old_data] Running VACUUM on both DBs to reclaim disk space...")
        for path in (args.biotech_db, args.deep_dives_db):
            if not path.exists():
                continue
            try:
                vc = sqlite3.connect(path)
                vc.execute("VACUUM")
                vc.close()
                print(f"  VACUUM OK: {path}")
            except sqlite3.OperationalError as e:
                print(f"  VACUUM skipped on {path}: {e}")

    print()
    if dry_run:
        print("[3_prune_old_data] DRY-RUN complete. Re-run with --write to apply.")
    else:
        print("[3_prune_old_data] WRITE complete.")
    return 0


def _next_trigger_after(today: date) -> date:
    """Find the next quarterly trigger date strictly after today."""
    from database.prune import QUARTERLY_TRIGGER_MMDD
    candidates: list[date] = []
    for year in (today.year, today.year + 1):
        for mm, dd in QUARTERLY_TRIGGER_MMDD:
            candidates.append(date(year, mm, dd))
    return min(c for c in candidates if c > today)


if __name__ == "__main__":
    sys.exit(main())
