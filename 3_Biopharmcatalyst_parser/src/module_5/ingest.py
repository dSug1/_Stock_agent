"""Module 5 orchestrator — read catalyst_snapshots, compute timing,
upsert into catalyst_timing, write an ingest_log row.

Idempotent via ``INSERT OR REPLACE`` on the composite PK. Per spec
§7.11 the function returns counts so the CLI can print them.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .compute import compute_timing_for_row
from .timing_rules import RULES_VERSION

log = logging.getLogger(__name__)


@dataclass
class ComputeStats:
    snapshot_date: date
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    rows_in: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_rejected: int = 0    # M5 never rejects rows; always 0 (kept for log symmetry)
    status: str = "running"
    error_message: str | None = None
    tier_counts: dict[str, int] = field(default_factory=dict)
    lane_counts: dict[str, int] = field(default_factory=dict)


_INSERT_SQL = """
INSERT OR REPLACE INTO catalyst_timing
    (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
     date_min, date_max, precision_tier, source_lane, matched_phrase,
     computed_at, rules_version)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _write_ingest_log(conn: sqlite3.Connection, stats: ComputeStats) -> None:
    conn.execute(
        """
        INSERT INTO ingest_log
            (module, started_at, finished_at, status, input_ref,
             rows_in, rows_inserted, rows_updated, rows_rejected, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "compute_timing",
            stats.started_at.isoformat(timespec="seconds"),
            (stats.finished_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            stats.status,
            stats.snapshot_date.isoformat(),
            stats.rows_in,
            stats.rows_inserted,
            stats.rows_updated,
            stats.rows_rejected,
            stats.error_message,
        ),
    )


def compute_timing_for_snapshot(
    snapshot_date: date,
    conn: sqlite3.Connection,
    *,
    today: date | None = None,
    rules_version: str = RULES_VERSION,
) -> ComputeStats:
    """Compute (date_min, date_max, tier, lane) for every catalyst_snapshots
    row at ``snapshot_date`` and upsert into catalyst_timing.

    The ``today`` parameter anchors the "future" filter for Lane 3 text
    parsing (spec §7.6 step 3). Defaults to ``snapshot_date`` — see
    decisions.md D3 for why we anchor on snapshot date rather than
    actual current date.
    """
    stats = ComputeStats(snapshot_date=snapshot_date)
    today = today or snapshot_date

    rows = conn.execute(
        """
        SELECT snapshot_date, ticker, drug, nct_number, next_catalyst_type,
               conference, catalyst_date, catalyst_text
        FROM catalyst_snapshots
        WHERE snapshot_date = ?
        """,
        (snapshot_date.isoformat(),),
    ).fetchall()
    stats.rows_in = len(rows)

    existing_pks: set[tuple[str, str, str, str]] = {
        (r["ticker"], r["drug"], r["nct_number"], r["next_catalyst_type"])
        for r in conn.execute(
            "SELECT ticker, drug, nct_number, next_catalyst_type "
            "FROM catalyst_timing WHERE snapshot_date = ?",
            (snapshot_date.isoformat(),),
        ).fetchall()
    }

    tier_counts: dict[str, int] = defaultdict(int)
    lane_counts: dict[str, int] = defaultdict(int)

    try:
        with conn:
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for r in rows:
                cd = date.fromisoformat(r["catalyst_date"]) if r["catalyst_date"] else None
                result = compute_timing_for_row(
                    conference=r["conference"],
                    catalyst_date=cd,
                    catalyst_text=r["catalyst_text"],
                    today=today,
                )
                pk = (r["ticker"], r["drug"], r["nct_number"], r["next_catalyst_type"])
                is_update = pk in existing_pks
                conn.execute(
                    _INSERT_SQL,
                    (
                        r["snapshot_date"],
                        r["ticker"], r["drug"], r["nct_number"], r["next_catalyst_type"],
                        result.date_min.isoformat() if result.date_min else None,
                        result.date_max.isoformat() if result.date_max else None,
                        result.precision_tier,
                        result.source_lane,
                        result.matched_phrase,
                        now_iso,
                        rules_version,
                    ),
                )
                if is_update:
                    stats.rows_updated += 1
                else:
                    stats.rows_inserted += 1
                    existing_pks.add(pk)
                tier_counts[result.precision_tier] += 1
                lane_counts[result.source_lane] += 1
        stats.status = "success"
    except Exception as e:
        stats.status = "failed"
        stats.error_message = str(e)
        raise
    finally:
        stats.finished_at = datetime.now(timezone.utc)
        stats.tier_counts = dict(tier_counts)
        stats.lane_counts = dict(lane_counts)
        try:
            _write_ingest_log(conn, stats)
            conn.commit()
        except Exception:
            log.exception("failed to write ingest_log row for compute_timing")

    return stats


def latest_snapshot_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute(
        "SELECT MAX(snapshot_date) FROM catalyst_snapshots"
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return date.fromisoformat(row[0])


def all_snapshot_dates(conn: sqlite3.Connection) -> list[date]:
    return [
        date.fromisoformat(r[0])
        for r in conn.execute(
            "SELECT DISTINCT snapshot_date FROM catalyst_snapshots "
            "ORDER BY snapshot_date"
        ).fetchall()
    ]
