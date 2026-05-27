"""Module 1 acceptance — BPC catalyst CSV ingest.

Synthetic CSV fixtures for edge cases; the real biotech_catalysts_v3.csv
covers the spec §3.6 "exactly 600 rows" contract.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_ingest_catalysts.py -v
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402
from module_1.ingest import SchemaValidationError, ingest_catalyst_csv  # noqa: E402


REAL_CSV = PROJECT_ROOT / "_csv_source" / "biotech_catalysts_v3.csv"

# Spec §3.3 header, exact column order is irrelevant — only the set is checked.
HEADER_COLS = [
    "Ticker", "Name", "Price", "30 Day Price Change", "Drug", "NCT Number",
    "Indication", "Stage", "Status", "Next Catalyst", "Catalyst Date",
    "Catalyst", "Conference", "Historical LOA", "Historical POP",
    "Bullish or Bearish", "Market Cap", "Last Updated", "No Of Shares",
]
HEADER = ",".join(HEADER_COLS)


def _valid_row(**overrides: str) -> str:
    base = {
        "Ticker": "TEST",
        "Name": "Test Inc.",
        "Price": "10.50",
        "30 Day Price Change": "10;11;12",
        "Drug": "TestDrug",
        "NCT Number": "NCT12345678",
        "Indication": "indication",
        "Stage": "phase2",
        "Status": "ongoing",
        "Next Catalyst": "Interim Data",
        "Catalyst Date": "24/05/2026",
        "Catalyst": "Interim readout expected Q2 2026",
        "Conference": "",
        "Historical LOA": "50.0",
        "Historical POP": "30.0",
        "Bullish or Bearish": "Bullish",
        "Market Cap": "1.5E+09",
        "Last Updated": "11/05/2026 08:36",
        "No Of Shares": "1000000",
    }
    base.update(overrides)
    return ",".join(base[c] for c in HEADER_COLS)


def _write_csv(tmp_path: Path, name: str, rows: list[str], header: str = HEADER) -> Path:
    # Inputs live in <tmp_path>/_csv_source/ to mirror real layout.
    src = tmp_path / "_csv_source"
    src.mkdir(parents=True, exist_ok=True)
    p = src / name
    p.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "ingest.db"
    c = get_connection(db)
    yield c
    c.close()


# ----- happy paths --------------------------------------------------------


def test_valid_single_row_inserts(conn, tmp_path):
    csv = _write_csv(tmp_path, "one.csv", [_valid_row()])
    stats = ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                                project_root=tmp_path, archive=False)
    assert stats.status == "success"
    assert stats.rows_in == 1
    assert stats.rows_inserted == 1
    assert stats.rows_updated == 0
    assert stats.rows_rejected == 0
    assert conn.execute("SELECT COUNT(*) FROM catalyst_snapshots").fetchone()[0] == 1


def test_idempotent_rerun_updates_no_inserts(conn, tmp_path):
    csv = _write_csv(tmp_path, "idemp.csv", [_valid_row()])
    ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                        project_root=tmp_path, archive=False)
    stats = ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                                project_root=tmp_path, archive=False)
    assert stats.rows_inserted == 0
    assert stats.rows_updated == 1
    assert stats.rows_rejected == 0
    assert conn.execute("SELECT COUNT(*) FROM catalyst_snapshots").fetchone()[0] == 1


def test_two_distinct_pks_both_inserted(conn, tmp_path):
    rows = [
        _valid_row(Ticker="AAA", Drug="DrugA"),
        _valid_row(Ticker="BBB", Drug="DrugB"),
    ]
    csv = _write_csv(tmp_path, "two.csv", rows)
    stats = ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                                project_root=tmp_path, archive=False)
    assert stats.rows_inserted == 2
    assert stats.rows_rejected == 0


# ----- header validation --------------------------------------------------


def test_missing_column_hard_fails_no_writes(conn, tmp_path):
    bad_header_cols = [c for c in HEADER_COLS if c != "Status"]
    bad_header = ",".join(bad_header_cols)
    # Build a value row by dropping the Status field
    fields = _valid_row().split(",")
    fields.pop(HEADER_COLS.index("Status"))
    csv = _write_csv(tmp_path, "bad_header.csv", [",".join(fields)], header=bad_header)

    with pytest.raises(SchemaValidationError) as ei:
        ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                            project_root=tmp_path, archive=False)
    assert "Status" in str(ei.value)
    assert conn.execute("SELECT COUNT(*) FROM catalyst_snapshots").fetchone()[0] == 0
    # ingest_log should record the failure
    log = conn.execute("SELECT module, status, error_message FROM ingest_log").fetchone()
    assert log["module"] == "catalysts"
    assert log["status"] == "failed"
    assert "Status" in (log["error_message"] or "")


def test_extra_column_hard_fails(conn, tmp_path):
    bad_header = HEADER + ",ExtraCol"
    bad_row = _valid_row() + ",something"
    csv = _write_csv(tmp_path, "extra.csv", [bad_row], header=bad_header)
    with pytest.raises(SchemaValidationError) as ei:
        ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                            project_root=tmp_path, archive=False)
    assert "ExtraCol" in str(ei.value)
    assert conn.execute("SELECT COUNT(*) FROM catalyst_snapshots").fetchone()[0] == 0


# ----- row-level validation ----------------------------------------------


def test_bad_stage_rejects_row_keeps_others(conn, tmp_path):
    rows = [
        _valid_row(Ticker="GOOD"),
        _valid_row(Ticker="BAD", Stage="phase99"),
    ]
    csv = _write_csv(tmp_path, "mixed.csv", rows)
    stats = ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                                project_root=tmp_path, archive=False)
    assert stats.rows_in == 2
    assert stats.rows_inserted == 1
    assert stats.rows_rejected == 1
    assert stats.status == "partial"
    tickers = {r["ticker"] for r in conn.execute("SELECT ticker FROM catalyst_snapshots")}
    assert tickers == {"GOOD"}


def test_blank_nct_normalized_to_empty_string(conn, tmp_path):
    csv = _write_csv(tmp_path, "blank_nct.csv",
                     [_valid_row(**{"NCT Number": ""})])
    ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                        project_root=tmp_path, archive=False)
    nct = conn.execute(
        "SELECT nct_number FROM catalyst_snapshots"
    ).fetchone()["nct_number"]
    assert nct == ""


def test_blank_next_catalyst_normalized_to_empty_string(conn, tmp_path):
    csv = _write_csv(tmp_path, "blank_nc.csv",
                     [_valid_row(**{"Next Catalyst": ""})])
    stats = ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                                project_root=tmp_path, archive=False)
    assert stats.rows_inserted == 1
    assert stats.rows_rejected == 0
    nc = conn.execute(
        "SELECT next_catalyst_type FROM catalyst_snapshots"
    ).fetchone()["next_catalyst_type"]
    assert nc == ""


def test_em_dash_in_historical_loa_becomes_null(conn, tmp_path):
    # BPC's sentinel for "not applicable" — U+2014.
    csv = _write_csv(tmp_path, "emdash.csv",
                     [_valid_row(**{"Historical LOA": "—", "Historical POP": "—"})])
    stats = ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                                project_root=tmp_path, archive=False)
    assert stats.rows_inserted == 1
    row = conn.execute(
        "SELECT historical_loa, historical_pop FROM catalyst_snapshots"
    ).fetchone()
    assert row["historical_loa"] is None
    assert row["historical_pop"] is None


def test_unparseable_catalyst_date_becomes_null(conn, tmp_path):
    csv = _write_csv(tmp_path, "bad_date.csv",
                     [_valid_row(**{"Catalyst Date": "not-a-date"})])
    stats = ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                                project_root=tmp_path, archive=False)
    assert stats.rows_inserted == 1  # NOT row-rejecting
    cd = conn.execute(
        "SELECT catalyst_date FROM catalyst_snapshots"
    ).fetchone()["catalyst_date"]
    assert cd is None


def test_scientific_notation_market_cap(conn, tmp_path):
    csv = _write_csv(tmp_path, "sci.csv",
                     [_valid_row(**{"Market Cap": "5.82485E+11"})])
    ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                       project_root=tmp_path, archive=False)
    mc = conn.execute(
        "SELECT market_cap_usd FROM catalyst_snapshots"
    ).fetchone()["market_cap_usd"]
    assert mc == pytest.approx(5.82485e11)


# ----- ingest_log + archive -----------------------------------------------


def test_ingest_log_written_with_counts(conn, tmp_path):
    rows = [_valid_row(), _valid_row(Ticker="BAD", Stage="phase99")]
    csv = _write_csv(tmp_path, "log.csv", rows)
    ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                        project_root=tmp_path, archive=False)
    log = conn.execute(
        "SELECT module, status, rows_in, rows_inserted, rows_rejected, input_ref "
        "FROM ingest_log"
    ).fetchone()
    assert log["module"] == "catalysts"
    assert log["status"] == "partial"
    assert log["rows_in"] == 2
    assert log["rows_inserted"] == 1
    assert log["rows_rejected"] == 1
    assert log["input_ref"] == "log.csv"


def test_archive_copy_made_on_success(conn, tmp_path):
    csv = _write_csv(tmp_path, "src.csv", [_valid_row()])
    ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                        project_root=tmp_path, archive=True)
    archived = tmp_path / "_csv_source" / "archive" / "2026-05-27_src.csv"
    assert archived.exists()


def test_no_archive_on_dry_run(conn, tmp_path):
    csv = _write_csv(tmp_path, "src.csv", [_valid_row()])
    ingest_catalyst_csv(csv, date(2026, 5, 27), conn,
                        project_root=tmp_path, archive=True, dry_run=True)
    archived = tmp_path / "_csv_source" / "archive" / "2026-05-27_src.csv"
    assert not archived.exists()


# ----- real-data acceptance (spec §3.6) -----------------------------------


@pytest.mark.skipif(not REAL_CSV.exists(),
                    reason="real biotech_catalysts_v3.csv not in _csv_source/")
def test_real_csv_loads_with_within_csv_dedup(tmp_path):
    """Spec §3.6 acceptance — the canonical contract for M1.

    600 CSV rows; 28 are bit-for-bit within-CSV duplicates on the PK
    (BPC source quirk). DB ends with 572 distinct rows.
    """
    db = tmp_path / "real.db"
    c = get_connection(db)
    try:
        stats = ingest_catalyst_csv(REAL_CSV, date(2026, 5, 27), c,
                                    project_root=tmp_path, archive=False)
        assert stats.status == "success", \
            f"expected status=success, got {stats.status} ({stats.rows_rejected} rejected)"
        assert stats.rows_in == 600
        assert stats.rows_inserted == 572  # distinct PKs
        assert stats.rows_updated == 28    # within-CSV duplicates
        assert stats.rows_rejected == 0
        assert c.execute(
            "SELECT COUNT(*) FROM catalyst_snapshots"
        ).fetchone()[0] == 572
    finally:
        c.close()


@pytest.mark.skipif(not REAL_CSV.exists(), reason="real CSV not present")
def test_real_csv_rerun_is_idempotent(tmp_path):
    db = tmp_path / "real_idemp.db"
    c = get_connection(db)
    try:
        ingest_catalyst_csv(REAL_CSV, date(2026, 5, 27), c,
                            project_root=tmp_path, archive=False)
        stats = ingest_catalyst_csv(REAL_CSV, date(2026, 5, 27), c,
                                    project_root=tmp_path, archive=False)
        assert stats.rows_inserted == 0
        assert stats.rows_updated == 600  # every CSV row hits an existing PK
        assert stats.rows_rejected == 0
        assert c.execute(
            "SELECT COUNT(*) FROM catalyst_snapshots"
        ).fetchone()[0] == 572  # DB count unchanged
    finally:
        c.close()
