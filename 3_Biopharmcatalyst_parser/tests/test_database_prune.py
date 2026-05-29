"""D37 — prune logic unit tests."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.prune import (  # noqa: E402
    execute_snapshot_prune,
    execute_ttl_prune,
    is_prune_due,
    most_recent_trigger_on_or_before,
    plan_snapshot_prune,
    plan_ttl_prune,
)


# ─── quarterly schedule ──────────────────────────────────────────────


def test_most_recent_trigger_january_returns_jan_1():
    # Jan 10 → most recent trigger is Jan 1 of the same year.
    assert most_recent_trigger_on_or_before(date(2026, 1, 10)) == date(2026, 1, 1)


def test_most_recent_trigger_mid_year():
    # Aug 5 → most recent trigger is Jul 1.
    assert most_recent_trigger_on_or_before(date(2026, 8, 5)) == date(2026, 7, 1)


def test_most_recent_trigger_on_trigger_date():
    # Oct 1 itself → that day's trigger qualifies.
    assert most_recent_trigger_on_or_before(date(2026, 10, 1)) == date(2026, 10, 1)


def test_most_recent_trigger_pre_first_trigger():
    # Dec 25 prior year → wraps to Oct 1 of prior year.
    assert most_recent_trigger_on_or_before(date(2026, 12, 25)) == date(2026, 10, 1)


def test_is_due_when_never_pruned():
    assert is_prune_due(date(2026, 5, 29), last_prune=None) is True


def test_is_due_when_last_prune_was_before_recent_trigger():
    # Today=2026-05-29 → recent trigger=2026-04-01. Last prune=2026-01-15
    # is BEFORE that → due.
    assert is_prune_due(date(2026, 5, 29), last_prune=date(2026, 1, 15)) is True


def test_is_not_due_when_last_prune_was_after_recent_trigger():
    # Today=2026-05-29 → recent trigger=2026-04-01. Last prune=2026-04-15
    # is AFTER that → NOT due.
    assert is_prune_due(date(2026, 5, 29), last_prune=date(2026, 4, 15)) is False


def test_is_due_on_trigger_date_itself_when_no_prior():
    # Today=2026-04-01 (trigger day) and no prior prune → due.
    assert is_prune_due(date(2026, 4, 1), last_prune=None) is True


def test_is_due_just_after_new_trigger():
    # Today=2026-04-02. Last prune=2026-01-15 (after Jan trigger).
    # Apr trigger has just passed → due.
    assert is_prune_due(date(2026, 4, 2), last_prune=date(2026, 1, 15)) is True


# ─── snapshot prune planner ──────────────────────────────────────────


@pytest.fixture
def snap_conn(tmp_path):
    db = tmp_path / "fx.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE catalyst_snapshots (
            snapshot_date TEXT NOT NULL,
            ticker        TEXT NOT NULL,
            drug          TEXT NOT NULL,
            nct_number    TEXT NOT NULL,
            next_catalyst_type TEXT NOT NULL,
            extra         TEXT,
            PRIMARY KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        );
    """)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


_SNAP_PK = ("snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type")


def test_plan_snapshot_prune_keeps_latest_n(snap_conn):
    """5 snapshots for one catalyst PK → keep_n=3 prunes 2 oldest."""
    for snap in ("2026-01-01", "2026-02-01", "2026-03-01",
                 "2026-04-01", "2026-05-01"):
        snap_conn.execute(
            "INSERT INTO catalyst_snapshots VALUES (?, 'AAA', 'drugX', 'NCT1', 'Interim Data', NULL)",
            (snap,),
        )
    plan = plan_snapshot_prune(
        snap_conn, table="catalyst_snapshots", pk_cols=_SNAP_PK, keep_n=3,
    )
    assert plan.rows_pruned == 2
    assert plan.rows_kept == 3
    pruned_dates = sorted(r["snapshot_date"] for r in plan.rows_to_delete)
    assert pruned_dates == ["2026-01-01", "2026-02-01"]


def test_plan_snapshot_prune_keep_1_keeps_only_latest(snap_conn):
    for snap in ("2026-01-01", "2026-02-01", "2026-03-01"):
        snap_conn.execute(
            "INSERT INTO catalyst_snapshots VALUES (?, 'AAA', 'drugX', 'NCT1', 'Interim Data', NULL)",
            (snap,),
        )
    plan = plan_snapshot_prune(
        snap_conn, table="catalyst_snapshots", pk_cols=_SNAP_PK, keep_n=1,
    )
    assert plan.rows_kept == 1
    assert plan.rows_pruned == 2
    pruned = sorted(r["snapshot_date"] for r in plan.rows_to_delete)
    assert pruned == ["2026-01-01", "2026-02-01"]


def test_plan_snapshot_prune_per_pk_independent(snap_conn):
    """Different catalysts each keep their own top-N independently."""
    # PK A: 4 snapshots, keep_n=2 → 2 pruned
    for snap in ("2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01"):
        snap_conn.execute(
            "INSERT INTO catalyst_snapshots VALUES (?, 'AAA', 'drugX', 'NCT1', 'Interim Data', NULL)",
            (snap,),
        )
    # PK B: 1 snapshot → 0 pruned even though older
    snap_conn.execute(
        "INSERT INTO catalyst_snapshots VALUES ('2025-12-01', 'BBB', 'drugY', 'NCT2', 'Topline Data', NULL)"
    )
    plan = plan_snapshot_prune(
        snap_conn, table="catalyst_snapshots", pk_cols=_SNAP_PK, keep_n=2,
    )
    assert plan.rows_pruned == 2
    assert all(r["ticker"] == "AAA" for r in plan.rows_to_delete)


def test_execute_snapshot_prune_actually_deletes(snap_conn):
    for snap in ("2026-01-01", "2026-02-01", "2026-03-01"):
        snap_conn.execute(
            "INSERT INTO catalyst_snapshots VALUES (?, 'AAA', 'drugX', 'NCT1', 'Interim Data', NULL)",
            (snap,),
        )
    plan = plan_snapshot_prune(
        snap_conn, table="catalyst_snapshots", pk_cols=_SNAP_PK, keep_n=1,
    )
    n = execute_snapshot_prune(snap_conn, plan)
    assert n == 2
    remaining = snap_conn.execute("SELECT COUNT(*) FROM catalyst_snapshots").fetchone()[0]
    assert remaining == 1
    latest = snap_conn.execute("SELECT snapshot_date FROM catalyst_snapshots").fetchone()[0]
    assert latest == "2026-03-01"


def test_plan_snapshot_prune_rejects_invalid_keep_n(snap_conn):
    with pytest.raises(ValueError):
        plan_snapshot_prune(
            snap_conn, table="catalyst_snapshots", pk_cols=_SNAP_PK, keep_n=0,
        )


def test_plan_snapshot_prune_rejects_bad_pk_order(snap_conn):
    with pytest.raises(ValueError):
        # pk_cols MUST start with snapshot_date
        plan_snapshot_prune(
            snap_conn, table="catalyst_snapshots",
            pk_cols=("ticker", "snapshot_date", "drug", "nct_number", "next_catalyst_type"),
            keep_n=3,
        )


# ─── TTL prune planner ──────────────────────────────────────────────


@pytest.fixture
def ttl_conn(tmp_path):
    db = tmp_path / "ttl.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE web_search_cache (
            url TEXT PRIMARY KEY,
            cached_at TEXT NOT NULL
        );
    """)
    try:
        yield conn
    finally:
        conn.close()


def test_plan_ttl_prune_counts_old_rows(ttl_conn):
    rows = [
        ("https://a.test", "2026-01-01T00:00:00"),   # 5 months ago
        ("https://b.test", "2026-04-15T00:00:00"),   # 6 weeks ago
        ("https://c.test", "2026-05-20T00:00:00"),   # 9 days ago
    ]
    ttl_conn.executemany("INSERT INTO web_search_cache VALUES (?, ?)", rows)
    plan = plan_ttl_prune(
        ttl_conn, table="web_search_cache", timestamp_col="cached_at",
        ttl_days=90, today=date(2026, 5, 29),
    )
    # cutoff = 2026-02-28; only the 2026-01-01 row is older.
    assert plan.rows_to_delete == 1
    assert plan.rows_kept == 2


def test_execute_ttl_prune_actually_deletes(ttl_conn):
    ttl_conn.executemany(
        "INSERT INTO web_search_cache VALUES (?, ?)",
        [("https://old.test", "2025-12-01T00:00:00"),
         ("https://new.test", "2026-05-25T00:00:00")],
    )
    plan = plan_ttl_prune(
        ttl_conn, table="web_search_cache", timestamp_col="cached_at",
        ttl_days=90, today=date(2026, 5, 29),
    )
    n = execute_ttl_prune(ttl_conn, plan)
    assert n == 1
    remaining = ttl_conn.execute("SELECT url FROM web_search_cache").fetchall()
    assert len(remaining) == 1
    assert remaining[0]["url"] == "https://new.test"


def test_plan_ttl_prune_rejects_invalid_ttl(ttl_conn):
    with pytest.raises(ValueError):
        plan_ttl_prune(
            ttl_conn, table="web_search_cache", timestamp_col="cached_at",
            ttl_days=0, today=date(2026, 5, 29),
        )
