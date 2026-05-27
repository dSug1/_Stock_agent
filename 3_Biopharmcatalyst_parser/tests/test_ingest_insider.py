"""Module 4 acceptance — BPC insider supplement CSV ingest.

Synthetic fixtures for edge cases; the real insider_data3.csv covers
the spec §6.6 "exactly 1,608 rows" contract.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_ingest_insider.py -v
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
from module_4.ingest import SchemaValidationError, ingest_bpc_insider_csv  # noqa: E402


REAL_CSV = PROJECT_ROOT / "_csv_source" / "insider_data3.csv"

HEADER_COLS = [
    "Ticker", "Name", "Insider Name", "Insider Position", "Filing Date",
    "Buy/Sell", "Stock/Option", "Shares", "Shares Change", "Trade Price",
    "Cost", "Final Share", "No Of Shares",
]
HEADER = ",".join(HEADER_COLS)


def _valid_row(**overrides: str) -> str:
    base = {
        "Ticker": "TEST",
        "Name": "Test Inc.",
        "Insider Name": "Alice Anderson",
        "Insider Position": "CEO",
        "Filing Date": "2026-05-20",
        "Buy/Sell": "Buy",
        "Stock/Option": "Stock",
        "Shares": "1000",
        "Shares Change": "1.5",
        "Trade Price": "10.50",
        "Cost": "10500.00",
        "Final Share": "100000",
        "No Of Shares": "5000000",
    }
    base.update(overrides)
    return ",".join(base[c] for c in HEADER_COLS)


def _write_csv(tmp_path: Path, name: str, rows: list[str], header: str = HEADER) -> Path:
    src = tmp_path / "_csv_source"
    src.mkdir(parents=True, exist_ok=True)
    p = src / name
    p.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return p


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "insider.db"
    c = get_connection(db)
    yield c
    c.close()


# ----- happy paths --------------------------------------------------------


def test_valid_single_row_inserts(conn, tmp_path):
    csv = _write_csv(tmp_path, "one.csv", [_valid_row()])
    stats = ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                                   project_root=tmp_path, archive=False)
    assert stats.status == "success"
    assert stats.rows_in == 1
    assert stats.rows_inserted == 1
    assert stats.rows_rejected == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM bpc_insider_supplement"
    ).fetchone()[0] == 1


def test_idempotent_rerun_updates_no_inserts(conn, tmp_path):
    csv = _write_csv(tmp_path, "idemp.csv", [_valid_row()])
    ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                           project_root=tmp_path, archive=False)
    stats = ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                                   project_root=tmp_path, archive=False)
    assert stats.rows_inserted == 0
    assert stats.rows_updated == 1


def test_two_distinct_pks_both_inserted(conn, tmp_path):
    # Same ticker + same insider + same date but different share amounts
    # → distinct PKs (shares is in the PK).
    rows = [
        _valid_row(Shares="1000"),
        _valid_row(Shares="2000"),
    ]
    csv = _write_csv(tmp_path, "two.csv", rows)
    stats = ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                                   project_root=tmp_path, archive=False)
    assert stats.rows_inserted == 2
    assert stats.rows_rejected == 0


def test_option_grant_zero_trade_price_accepted(conn, tmp_path):
    # Spec §6.4: "Trade Price: float; 0 allowed (option exercises)"
    csv = _write_csv(tmp_path, "opt.csv", [_valid_row(
        **{"Stock/Option": "Option", "Trade Price": "0", "Cost": "0"})])
    stats = ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                                   project_root=tmp_path, archive=False)
    assert stats.rows_inserted == 1
    row = conn.execute(
        "SELECT trade_price, cost, stock_or_option FROM bpc_insider_supplement"
    ).fetchone()
    assert row["trade_price"] == 0
    assert row["cost"] == 0
    assert row["stock_or_option"] == "Option"


def test_blank_insider_position_stored_as_null(conn, tmp_path):
    csv = _write_csv(tmp_path, "blank_pos.csv",
                     [_valid_row(**{"Insider Position": ""})])
    ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                           project_root=tmp_path, archive=False)
    pos = conn.execute(
        "SELECT insider_position FROM bpc_insider_supplement"
    ).fetchone()["insider_position"]
    assert pos is None


# ----- header validation --------------------------------------------------


def test_missing_column_hard_fails_no_writes(conn, tmp_path):
    bad_header_cols = [c for c in HEADER_COLS if c != "Cost"]
    bad_header = ",".join(bad_header_cols)
    fields = _valid_row().split(",")
    fields.pop(HEADER_COLS.index("Cost"))
    csv = _write_csv(tmp_path, "bad_header.csv", [",".join(fields)],
                     header=bad_header)

    with pytest.raises(SchemaValidationError) as ei:
        ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                               project_root=tmp_path, archive=False)
    assert "Cost" in str(ei.value)
    assert conn.execute(
        "SELECT COUNT(*) FROM bpc_insider_supplement"
    ).fetchone()[0] == 0
    log = conn.execute(
        "SELECT module, status FROM ingest_log"
    ).fetchone()
    assert log["module"] == "bpc_insider"
    assert log["status"] == "failed"


def test_extra_column_hard_fails(conn, tmp_path):
    bad_header = HEADER + ",ExtraCol"
    bad_row = _valid_row() + ",something"
    csv = _write_csv(tmp_path, "extra.csv", [bad_row], header=bad_header)
    with pytest.raises(SchemaValidationError) as ei:
        ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                               project_root=tmp_path, archive=False)
    assert "ExtraCol" in str(ei.value)


# ----- row-level validation ----------------------------------------------


def test_invalid_buy_sell_rejects_row(conn, tmp_path):
    rows = [
        _valid_row(Ticker="GOOD"),
        _valid_row(Ticker="BAD", **{"Buy/Sell": "Hold"}),
    ]
    csv = _write_csv(tmp_path, "mixed.csv", rows)
    stats = ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                                   project_root=tmp_path, archive=False)
    assert stats.rows_in == 2
    assert stats.rows_inserted == 1
    assert stats.rows_rejected == 1
    assert stats.status == "partial"
    tickers = {r["ticker"] for r in conn.execute(
        "SELECT ticker FROM bpc_insider_supplement"
    )}
    assert tickers == {"GOOD"}


def test_invalid_stock_or_option_rejects_row(conn, tmp_path):
    csv = _write_csv(tmp_path, "stk.csv",
                     [_valid_row(**{"Stock/Option": "Bond"})])
    stats = ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                                   project_root=tmp_path, archive=False)
    assert stats.rows_rejected == 1
    assert stats.rows_inserted == 0


def test_blank_insider_name_rejects_row(conn, tmp_path):
    csv = _write_csv(tmp_path, "blank_in.csv",
                     [_valid_row(**{"Insider Name": ""})])
    stats = ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                                   project_root=tmp_path, archive=False)
    assert stats.rows_rejected == 1


def test_unparseable_filing_date_rejects_row(conn, tmp_path):
    csv = _write_csv(tmp_path, "bad_date.csv",
                     [_valid_row(**{"Filing Date": "20/05/2026"})])
    stats = ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                                   project_root=tmp_path, archive=False)
    # Filing Date is required + ISO; unparseable rejects the row
    # (unlike M1's catalyst_date which soft-NULLs).
    assert stats.rows_rejected == 1


# ----- ingest_log + archive ---------------------------------------------


def test_ingest_log_written_with_counts(conn, tmp_path):
    rows = [_valid_row(), _valid_row(Ticker="BAD", **{"Buy/Sell": "Hold"})]
    csv = _write_csv(tmp_path, "log.csv", rows)
    ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                           project_root=tmp_path, archive=False)
    log = conn.execute(
        "SELECT module, status, rows_in, rows_inserted, rows_rejected, input_ref "
        "FROM ingest_log"
    ).fetchone()
    assert log["module"] == "bpc_insider"
    assert log["status"] == "partial"
    assert log["rows_in"] == 2
    assert log["rows_inserted"] == 1
    assert log["rows_rejected"] == 1
    assert log["input_ref"] == "log.csv"


def test_archive_copy_made_on_success(conn, tmp_path):
    csv = _write_csv(tmp_path, "src.csv", [_valid_row()])
    ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                           project_root=tmp_path, archive=True)
    archived = tmp_path / "_csv_source" / "archive" / "2026-05-27_src.csv"
    assert archived.exists()


# ----- v_insider_signal_combined view --------------------------------


def test_view_filters_bpc_options_out(conn, tmp_path):
    # The view restricts BPC rows to stock_or_option='Stock'. An option
    # row should be present in the table but NOT in the view.
    csv = _write_csv(tmp_path, "view.csv", [
        _valid_row(Ticker="STK"),
        _valid_row(Ticker="OPT", **{"Stock/Option": "Option",
                                    "Trade Price": "0", "Cost": "0"}),
    ])
    ingest_bpc_insider_csv(csv, date(2026, 5, 27), conn,
                          project_root=tmp_path, archive=False)
    raw_count = conn.execute(
        "SELECT COUNT(*) FROM bpc_insider_supplement"
    ).fetchone()[0]
    assert raw_count == 2

    view_tickers = {r[0] for r in conn.execute(
        "SELECT ticker FROM v_insider_signal_combined WHERE source = 'bpc'"
    )}
    assert view_tickers == {"STK"}


# ----- real-data acceptance (spec §6.6) -----------------------------------


@pytest.mark.skipif(not REAL_CSV.exists(),
                    reason="real insider_data3.csv not in _csv_source/")
def test_real_csv_loads_with_within_csv_dedup(tmp_path):
    """Spec §6.6 acceptance — the canonical contract for M4.

    1608 CSV rows; 52 are bit-for-bit within-CSV duplicates on the 8-col
    PK (BPC source quirk). The 7 buckets that previously collided on
    the narrower 7-col PK but had distinct final_shares are now preserved
    as separate rows. DB ends with 1556 distinct rows.
    """
    db = tmp_path / "real.db"
    c = get_connection(db)
    try:
        stats = ingest_bpc_insider_csv(REAL_CSV, date(2026, 5, 27), c,
                                       project_root=tmp_path, archive=False)
        assert stats.status == "success", \
            f"expected success, got {stats.status} ({stats.rows_rejected} rejected)"
        assert stats.rows_in == 1608
        assert stats.rows_inserted == 1556  # distinct PKs
        assert stats.rows_updated == 52     # bit-for-bit dupes
        assert stats.rows_rejected == 0
        assert c.execute(
            "SELECT COUNT(*) FROM bpc_insider_supplement"
        ).fetchone()[0] == 1556
    finally:
        c.close()


@pytest.mark.skipif(not REAL_CSV.exists(), reason="real CSV not present")
def test_real_csv_rerun_is_idempotent(tmp_path):
    db = tmp_path / "real_idemp.db"
    c = get_connection(db)
    try:
        ingest_bpc_insider_csv(REAL_CSV, date(2026, 5, 27), c,
                              project_root=tmp_path, archive=False)
        stats = ingest_bpc_insider_csv(REAL_CSV, date(2026, 5, 27), c,
                                       project_root=tmp_path, archive=False)
        assert stats.rows_inserted == 0
        assert stats.rows_updated == 1608  # every CSV row hits an existing PK
        assert stats.rows_rejected == 0
        assert c.execute(
            "SELECT COUNT(*) FROM bpc_insider_supplement"
        ).fetchone()[0] == 1556  # DB count unchanged
    finally:
        c.close()
