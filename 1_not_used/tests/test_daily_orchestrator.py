"""Tests for scripts/daily_orchestrator.py cadence logic.

The orchestrator file lives under scripts/ (not on PYTHONPATH), so
we load it by path via importlib. We exercise only the pure cadence
helpers — _last_ingest_date, _should_ingest, _next_saturday —
which are cheap to test and carry the logic that matters for the
multi-day-offline case.
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from database.db import get_connection


_ORCHESTRATOR_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts" / "daily_orchestrator.py"
)


def _load_orchestrator():
    spec = importlib.util.spec_from_file_location(
        "daily_orchestrator", _ORCHESTRATOR_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def orch():
    return _load_orchestrator()


@pytest.fixture
def conn(tmp_path):
    # get_connection() seeds institutions via seed_institutions(),
    # so any id from the seed roster is a valid FK target.
    c = get_connection(tmp_path / "test.db")
    yield c
    c.close()


def _insert_filing(conn, created_at_iso: str) -> None:
    inst_id = conn.execute(
        "SELECT id FROM institutions ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO filings_log "
        "(institution_id, filing_date, period_of_report, "
        " accession_number, document_url, holdings_count, "
        " parse_status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, NULL, 0, 'success', ?, ?)",
        (
            inst_id,
            created_at_iso[:10], created_at_iso[:10],
            f"acc-{created_at_iso}",
            created_at_iso, created_at_iso,
        ),
    )
    conn.commit()


def _iso_at(d: date) -> str:
    return datetime(
        d.year, d.month, d.day, 12, 0, 0, tzinfo=timezone.utc
    ).isoformat(timespec="seconds")


def test_last_ingest_date_empty_returns_none(orch, conn):
    assert orch._last_ingest_date(conn) is None


def test_last_ingest_date_returns_most_recent(orch, conn):
    _insert_filing(conn, _iso_at(date(2026, 4, 1)))
    _insert_filing(conn, _iso_at(date(2026, 4, 11)))
    _insert_filing(conn, _iso_at(date(2026, 4, 5)))
    assert orch._last_ingest_date(conn) == date(2026, 4, 11)


def test_should_ingest_on_saturday(orch, conn):
    _insert_filing(conn, _iso_at(date(2026, 4, 17)))  # Friday
    saturday = date(2026, 4, 18)
    assert saturday.weekday() == 5
    assert orch._should_ingest(saturday, conn) is True


def test_should_ingest_skips_midweek_when_fresh(orch, conn):
    # Last ingest Saturday 2026-04-11, today Tuesday 2026-04-14 (3d ago)
    _insert_filing(conn, _iso_at(date(2026, 4, 11)))
    tuesday = date(2026, 4, 14)
    assert tuesday.weekday() == 1
    assert orch._should_ingest(tuesday, conn) is False


def test_should_ingest_catches_up_when_stale(orch, conn):
    # Last ingest 8 days ago (PC was off the intervening Saturday)
    _insert_filing(conn, _iso_at(date(2026, 4, 6)))
    tuesday = date(2026, 4, 14)
    assert (tuesday - date(2026, 4, 6)).days == 8
    assert orch._should_ingest(tuesday, conn) is True


def test_should_ingest_with_empty_filings_log(orch, conn):
    # First-ever run: no filings_log rows yet, ingest regardless of day.
    wednesday = date(2026, 4, 15)
    assert wednesday.weekday() == 2
    assert orch._should_ingest(wednesday, conn) is True


def test_should_ingest_boundary_exactly_seven_days(orch, conn):
    # Exactly INGEST_MAX_AGE_DAYS old — >= triggers ingest.
    _insert_filing(conn, _iso_at(date(2026, 4, 7)))
    tuesday = date(2026, 4, 14)
    assert (tuesday - date(2026, 4, 7)).days == orch.INGEST_MAX_AGE_DAYS
    assert orch._should_ingest(tuesday, conn) is True


def test_should_ingest_boundary_six_days_skips(orch, conn):
    # Six days ago — below threshold, stays heartbeat.
    _insert_filing(conn, _iso_at(date(2026, 4, 8)))
    tuesday = date(2026, 4, 14)
    assert (tuesday - date(2026, 4, 8)).days == 6
    assert orch._should_ingest(tuesday, conn) is False


@pytest.mark.parametrize("today_iso,expected_iso", [
    ("2026-04-13", "2026-04-18"),  # Mon -> Sat
    ("2026-04-14", "2026-04-18"),  # Tue -> Sat
    ("2026-04-17", "2026-04-18"),  # Fri -> Sat (next day)
    ("2026-04-18", "2026-04-25"),  # Sat -> following Sat (not today)
    ("2026-04-19", "2026-04-25"),  # Sun -> Sat
])
def test_next_saturday(orch, today_iso, expected_iso):
    assert orch._next_saturday(
        date.fromisoformat(today_iso)
    ) == date.fromisoformat(expected_iso)
