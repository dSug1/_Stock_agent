"""Module 3 — offline tests for the 13D/G ingest helpers.

The live HTTP smoke test (against SEC) is run separately via the CLI;
these tests pin the pure-logic pieces: URL construction, per-ticker
floor (same shape as M2), and the form-type allow-list.
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
from module_3.ingest import (  # noqa: E402
    OWNERSHIP_FORMS,
    _per_ticker_floor,
    build_filing_url,
)


FULL_WINDOW_FLOOR = "2025-05-27"


# ===== form-type allow-list (spec §5.3) ===================================

def test_ownership_forms_match_spec_53():
    # Calibration (D6 update 2026-05-27): SEC publishes ownership filings
    # under both "SC" and "SCHEDULE" prefixes; both must be in the filter.
    assert set(OWNERSHIP_FORMS) == {
        "SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A",
        "SCHEDULE 13D", "SCHEDULE 13G", "SCHEDULE 13D/A", "SCHEDULE 13G/A",
    }


# ===== URL construction ===================================================

def test_filing_url_with_primary_doc():
    url = build_filing_url(
        cik="0000320193",
        accession_number="0001234567-26-000123",
        primary_doc="sc13g.htm",
    )
    # int(cik) strips leading zeros; that's SEC's canonical Archive path.
    assert url == "https://www.sec.gov/Archives/edgar/data/320193/000123456726000123/sc13g.htm"


def test_filing_url_strips_xsl_prefix():
    # Modern filings put the rendered HTML at xslSCHEDULE_13G_X01/<name>.
    # The raw filing is at <name> — strip the xsl prefix.
    url = build_filing_url(
        cik="0000320193",
        accession_number="0001234567-26-000123",
        primary_doc="xslSCHEDULE_13G_X01/sc13g.htm",
    )
    assert url.endswith("/sc13g.htm")
    assert "xslSCHEDULE" not in url


def test_filing_url_falls_back_to_directory_when_no_primary_doc():
    url = build_filing_url(
        cik="0000320193",
        accession_number="0001234567-26-000123",
        primary_doc=None,
    )
    assert url == "https://www.sec.gov/Archives/edgar/data/320193/000123456726000123/"


def test_filing_url_always_non_empty():
    # The schema declares filing_url NOT NULL; the function must always
    # return a non-empty string for the INSERT to succeed.
    for pd in (None, "", "primary.xml", "xslF345X05/primary.xml"):
        url = build_filing_url("0000000001", "0000001-00-000001", pd)
        assert url
        assert "https://www.sec.gov/Archives/edgar/data/1/" in url


# ===== per-ticker floor (mirrors M2's pattern) ============================

@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "m3_floor.db"
    c = get_connection(db)
    yield c
    c.close()


def _seed_ownership(conn, *, cik: str, accession: str, filed_date: str,
                    form_type: str = "SC 13G") -> None:
    conn.execute(
        "INSERT INTO edgar_ownership_filings "
        "(accession_number, cik_issuer, form_type, filed_date, filing_url, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (accession, cik, form_type, filed_date, "https://example/", "2026-05-27T00:00:00"),
    )
    conn.commit()


def test_floor_new_ticker_returns_full_window(conn):
    floor, mode = _per_ticker_floor(conn, "0000111111", FULL_WINDOW_FLOOR)
    assert mode == "new"
    assert floor == FULL_WINDOW_FLOOR


def test_floor_existing_ticker_uses_max_filed_date(conn):
    _seed_ownership(conn, cik="0000222222", accession="A1", filed_date="2026-04-10")
    _seed_ownership(conn, cik="0000222222", accession="A2", filed_date="2026-05-20")
    floor, mode = _per_ticker_floor(conn, "0000222222", FULL_WINDOW_FLOOR)
    assert mode == "incremental"
    assert floor == "2026-05-20"


def test_floor_max_older_than_window_uses_window(conn):
    _seed_ownership(conn, cik="0000333333", accession="OLD", filed_date="2023-01-15")
    floor, mode = _per_ticker_floor(conn, "0000333333", FULL_WINDOW_FLOOR)
    assert mode == "incremental"
    assert floor == FULL_WINDOW_FLOOR


def test_m2_and_m3_floors_are_independent(conn):
    """M2 floors at MAX(filed_date) from edgar_form4_filings; M3 from
    edgar_ownership_filings. The two should not contaminate each other."""
    from module_2.ingest import _per_ticker_floor as m2_floor
    # Seed M3 only
    _seed_ownership(conn, cik="0000444444", accession="OWN1", filed_date="2026-05-20")
    m3_floor, m3_mode = _per_ticker_floor(conn, "0000444444", FULL_WINDOW_FLOOR)
    m2_floor_v, m2_mode = m2_floor(conn, "0000444444", FULL_WINDOW_FLOOR)
    assert m3_mode == "incremental" and m3_floor == "2026-05-20"
    assert m2_mode == "new" and m2_floor_v == FULL_WINDOW_FLOOR
