"""Module 2 — per-ticker incremental-floor logic (D5 optimization).

Verifies that on the second run of M2 for the same ticker, we floor the
``since_date_iso`` filter at ``MAX(filed_date)`` instead of paying the
full 365-day window again. Tests the resolver function directly against
a tmp DB seeded with synthetic prior filings.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402
from module_2.ingest import _per_ticker_floor  # noqa: E402


FULL_WINDOW_FLOOR = "2025-05-27"   # what `today - 365 days` would yield


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "edgar_floor.db"
    c = get_connection(db)
    yield c
    c.close()


def _seed_filing(conn, *, cik: str, accession: str, filed_date: str) -> None:
    conn.execute(
        "INSERT INTO edgar_form4_filings "
        "(accession_number, cik_issuer, ticker, filed_date, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (accession, cik, "TEST", filed_date, "2026-05-27T00:00:00"),
    )
    conn.commit()


def test_new_ticker_returns_full_window_floor(conn):
    floor, mode = _per_ticker_floor(conn, "0000123456", FULL_WINDOW_FLOOR)
    assert mode == "new"
    assert floor == FULL_WINDOW_FLOOR


def test_existing_ticker_floors_at_max_filed_date(conn):
    _seed_filing(conn, cik="0000123456", accession="A1", filed_date="2026-04-10")
    _seed_filing(conn, cik="0000123456", accession="A2", filed_date="2026-05-20")
    _seed_filing(conn, cik="0000123456", accession="A3", filed_date="2026-03-15")
    floor, mode = _per_ticker_floor(conn, "0000123456", FULL_WINDOW_FLOOR)
    assert mode == "incremental"
    # Most recent filed_date dominates
    assert floor == "2026-05-20"


def test_max_filed_date_older_than_window_uses_window(conn):
    # If a ticker hasn't been touched in years and the user narrows
    # --lookback-days, the window floor takes precedence — we never
    # look further back than the user asked for.
    _seed_filing(conn, cik="0000999999", accession="OLD", filed_date="2023-01-15")
    floor, mode = _per_ticker_floor(conn, "0000999999", FULL_WINDOW_FLOOR)
    assert mode == "incremental"
    # max(2025-05-27, 2023-01-15) = 2025-05-27
    assert floor == FULL_WINDOW_FLOOR


def test_unrelated_cik_does_not_influence_floor(conn):
    # Two CIKs in the table; only the one we're querying matters.
    _seed_filing(conn, cik="0000111111", accession="A1", filed_date="2026-05-20")
    floor, mode = _per_ticker_floor(conn, "0000222222", FULL_WINDOW_FLOOR)
    assert mode == "new"
    assert floor == FULL_WINDOW_FLOOR
