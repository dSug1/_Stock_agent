"""D37 — DB pruning logic (pure-compute, testable).

Two prune actions, used by `scripts/3_prune_old_data.py`:

  * `plan_snapshot_prune` — for each (ticker, drug, nct, type) PK in a
    snapshot-keyed table, identify rows beyond the N most recent
    snapshot_dates as prune candidates.

  * `plan_ttl_prune` — for any table with a single timestamp column,
    identify rows older than TODAY − ttl_days.

Both functions are *planners* — they return the snapshot_date / row-id
sets the caller should delete, but they do NOT execute any SQL. The
CLI in `3_prune_old_data.py` is the only thing that actually deletes,
inside a single transaction so failures don't leave the FK chain in an
inconsistent state.

Quarterly-schedule logic also lives here: `is_prune_due(today, last)`
returns True when the most-recent quarterly trigger has passed AND we
haven't pruned since. Triggers are anchored at ~1.5 months after each
13F deadline (per user spec) so the funds DB is settled when we prune.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Sequence


# ─── quarterly schedule ──────────────────────────────────────────────


# Trigger dates = ~1.5 months after each 13F filing deadline.
# 13F deadlines: Feb 14, May 15, Aug 14, Nov 14.
# Triggers:        Apr 1,  Jul 1,  Oct 1,  Jan 1.
QUARTERLY_TRIGGER_MMDD: tuple[tuple[int, int], ...] = (
    (1, 1),   # Jan 1 (post-Nov-14 13F deadline + 1.5 mo)
    (4, 1),   # Apr 1 (post-Feb-14)
    (7, 1),   # Jul 1 (post-May-15)
    (10, 1),  # Oct 1 (post-Aug-14)
)


def most_recent_trigger_on_or_before(today: date) -> date:
    """Return the last quarterly trigger date that has occurred."""
    candidates: list[date] = []
    for year in (today.year - 1, today.year):
        for mm, dd in QUARTERLY_TRIGGER_MMDD:
            candidates.append(date(year, mm, dd))
    return max(c for c in candidates if c <= today)


def is_prune_due(today: date, last_prune: date | None) -> bool:
    """Should we prune today?

    True if (a) we've never pruned, or (b) the most-recent quarterly
    trigger date has passed AND it's later than our last prune.
    """
    if last_prune is None:
        return True
    return last_prune < most_recent_trigger_on_or_before(today)


# ─── snapshot prune planner ──────────────────────────────────────────


@dataclass(frozen=True)
class SnapshotPrunePlan:
    """A list of snapshot_date values to DELETE from a snapshot-keyed table.

    Caller uses `snapshot_dates_to_delete` plus the PK columns (excluding
    snapshot_date) to construct the DELETE statement(s).
    """
    table: str
    pk_cols_excluding_snapshot_date: tuple[str, ...]
    rows_to_delete: list[dict]    # each dict has snapshot_date + the PK cols
    rows_kept: int
    rows_pruned: int


def plan_snapshot_prune(
    conn: sqlite3.Connection,
    *,
    table: str,
    pk_cols: Sequence[str],
    keep_n: int = 3,
) -> SnapshotPrunePlan:
    """For each PK (excluding snapshot_date), identify rows beyond the
    `keep_n` most recent `snapshot_date` values as deletion candidates.

    Args:
        table: e.g. 'catalyst_snapshots' or 'bpc_insider_supplement'.
        pk_cols: the table's full PK column list. The first element MUST
                 be 'snapshot_date'.
        keep_n: how many most-recent snapshots per (PK − snapshot_date) to keep.
    """
    if not pk_cols or pk_cols[0] != "snapshot_date":
        raise ValueError("pk_cols must start with 'snapshot_date'")
    if keep_n < 1:
        raise ValueError("keep_n must be ≥ 1")

    pk_tail = tuple(pk_cols[1:])
    pk_tail_csv = ", ".join(pk_tail)
    # Use a window function to RANK snapshot_dates per (PK − snapshot_date)
    # in descending order; keep rank ≤ keep_n, prune the rest.
    sql = f"""
        WITH ranked AS (
            SELECT
                snapshot_date, {pk_tail_csv},
                ROW_NUMBER() OVER (
                    PARTITION BY {pk_tail_csv}
                    ORDER BY snapshot_date DESC
                ) AS rk
            FROM {table}
        )
        SELECT snapshot_date, {pk_tail_csv}
        FROM ranked
        WHERE rk > ?
    """
    rows_to_delete = [dict(r) for r in conn.execute(sql, (keep_n,))]
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return SnapshotPrunePlan(
        table=table,
        pk_cols_excluding_snapshot_date=pk_tail,
        rows_to_delete=rows_to_delete,
        rows_kept=total - len(rows_to_delete),
        rows_pruned=len(rows_to_delete),
    )


def execute_snapshot_prune(
    conn: sqlite3.Connection, plan: SnapshotPrunePlan,
) -> int:
    """Apply a SnapshotPrunePlan. Returns rows actually deleted.

    Caller is responsible for the surrounding transaction.
    """
    if not plan.rows_to_delete:
        return 0
    pk_cols = ("snapshot_date",) + plan.pk_cols_excluding_snapshot_date
    where = " AND ".join(f"{c} = ?" for c in pk_cols)
    params = [
        tuple(row[c] for c in pk_cols)
        for row in plan.rows_to_delete
    ]
    cur = conn.executemany(f"DELETE FROM {plan.table} WHERE {where}", params)
    return cur.rowcount


# ─── TTL prune planner ───────────────────────────────────────────────


@dataclass(frozen=True)
class TTLPrunePlan:
    table: str
    timestamp_col: str
    cutoff_iso: str           # rows with timestamp_col < cutoff_iso are deleted
    rows_to_delete: int
    rows_kept: int


def plan_ttl_prune(
    conn: sqlite3.Connection,
    *,
    table: str,
    timestamp_col: str,
    ttl_days: int,
    today: date,
) -> TTLPrunePlan:
    """Identify rows older than today − ttl_days as deletion candidates."""
    if ttl_days < 1:
        raise ValueError("ttl_days must be ≥ 1")
    from datetime import timedelta
    cutoff = today - timedelta(days=ttl_days)
    cutoff_iso = cutoff.isoformat()
    n_to_delete = conn.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {timestamp_col} < ?",
        (cutoff_iso,),
    ).fetchone()[0]
    total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return TTLPrunePlan(
        table=table,
        timestamp_col=timestamp_col,
        cutoff_iso=cutoff_iso,
        rows_to_delete=n_to_delete,
        rows_kept=total - n_to_delete,
    )


def execute_ttl_prune(conn: sqlite3.Connection, plan: TTLPrunePlan) -> int:
    """Apply a TTLPrunePlan. Returns rows actually deleted."""
    cur = conn.execute(
        f"DELETE FROM {plan.table} WHERE {plan.timestamp_col} < ?",
        (plan.cutoff_iso,),
    )
    return cur.rowcount


# ─── last-prune tracking via ingest_log ──────────────────────────────


def get_last_prune_date(conn: sqlite3.Connection) -> date | None:
    """Read the most-recent `ingest_log.module = 'prune'` row's
    `finished_at` and return its date. None if never pruned."""
    try:
        row = conn.execute(
            "SELECT finished_at FROM ingest_log "
            "WHERE module = 'prune' AND status = 'success' "
            "ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or not row[0]:
        return None
    raw = row[0]
    # finished_at is an ISO timestamp; take the date prefix.
    return date.fromisoformat(raw[:10])
