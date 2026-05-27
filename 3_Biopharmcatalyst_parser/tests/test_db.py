"""Module 0 acceptance — schema bootstrap + idempotency.

Verifies every table in spec §2 exists with the correct columns and PK.
Uses a fresh temp DB per test so we never touch data/biotech.db.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe -m pytest tests/test_db.py -v
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


# (table → expected columns in declaration order, expected PK columns in order)
EXPECTED = {
    "catalyst_snapshots": (
        [
            "snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type",
            "name", "price", "price_history_30d", "indication", "stage", "status",
            "catalyst_date", "catalyst_text", "conference", "historical_loa",
            "historical_pop", "sentiment", "market_cap_usd", "no_of_shares",
            "bpc_last_updated",
        ],
        ["snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type"],
    ),
    "edgar_form4_filings": (
        [
            "accession_number", "cik_issuer", "ticker", "issuer_name",
            "reporting_owner_cik", "reporting_owner_name", "is_director",
            "is_officer", "is_ten_percent_owner", "officer_title", "filed_date",
            "fetched_at",
        ],
        ["accession_number"],
    ),
    "edgar_form4_transactions": (
        [
            "transaction_id", "accession_number", "transaction_date",
            "transaction_code", "transaction_code_meaning", "acquired_disposed",
            "shares", "price_per_share", "shares_owned_following",
            "is_open_market", "direct_or_indirect",
        ],
        ["transaction_id"],
    ),
    "edgar_ownership_filings": (
        [
            "accession_number", "cik_issuer", "ticker", "issuer_name", "form_type",
            "filed_date", "filer_name", "filing_url", "percent_of_class",
            "fetched_at",
        ],
        ["accession_number"],
    ),
    "bpc_insider_supplement": (
        [
            "snapshot_date", "ticker", "name", "insider_name", "insider_position",
            "filing_date", "buy_sell", "stock_or_option", "shares",
            "shares_change_pct", "trade_price", "cost", "final_shares",
            "no_of_shares",
        ],
        # PK widened per D4 (final_shares added) to preserve legitimate
        # same-day partial-fill rows.
        ["snapshot_date", "ticker", "insider_name", "filing_date", "buy_sell",
         "stock_or_option", "shares", "final_shares"],
    ),
    "ingest_log": (
        [
            "run_id", "module", "started_at", "finished_at", "status", "input_ref",
            "rows_in", "rows_inserted", "rows_updated", "rows_rejected",
            "error_message",
        ],
        ["run_id"],
    ),
    "ticker_cik_map": (
        ["ticker", "cik", "name", "last_refreshed"],
        ["ticker"],
    ),
    "catalyst_timing": (
        [
            "snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type",
            "date_min", "date_max", "precision_tier", "source_lane",
            "matched_phrase", "computed_at", "rules_version",
        ],
        ["snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type"],
    ),
}


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "test_biotech.db"
    c = get_connection(db)
    yield c
    c.close()


def test_all_tables_present(conn):
    actual = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    for table in EXPECTED:
        assert table in actual, f"missing table: {table}"


@pytest.mark.parametrize("table", list(EXPECTED))
def test_columns_in_order(conn, table):
    expected_cols, _ = EXPECTED[table]
    actual_cols = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    assert actual_cols == expected_cols, (
        f"{table}: expected {expected_cols}, got {actual_cols}"
    )


@pytest.mark.parametrize("table", list(EXPECTED))
def test_primary_key(conn, table):
    _, expected_pk = EXPECTED[table]
    pk_rows = [
        (row["name"], row["pk"])
        for row in conn.execute(f"PRAGMA table_info({table})")
        if row["pk"] > 0
    ]
    pk_rows.sort(key=lambda x: x[1])
    actual_pk = [name for name, _ in pk_rows]
    assert actual_pk == expected_pk, (
        f"{table}: expected PK {expected_pk}, got {actual_pk}"
    )


def test_idempotent_reconnect(tmp_path):
    db = tmp_path / "idemp.db"
    get_connection(db).close()
    # Second open must not error; tables already exist.
    c2 = get_connection(db)
    actual = {
        row[0]
        for row in c2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    c2.close()
    for table in EXPECTED:
        assert table in actual


def test_foreign_keys_enabled(conn):
    # PRAGMA foreign_keys returns 1 when enabled.
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


EXPECTED_VIEWS = ("v_latest_catalysts", "v_insider_signal_combined")


def test_views_present(conn):
    actual = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()
    }
    for v in EXPECTED_VIEWS:
        assert v in actual, f"missing view: {v}"


@pytest.mark.parametrize("view", EXPECTED_VIEWS)
def test_views_queryable(conn, view):
    # SELECT against an empty DB — view should parse cleanly and return 0 rows.
    rows = conn.execute(f"SELECT * FROM {view} LIMIT 1").fetchall()
    assert rows == []


def test_insider_signal_view_unions_both_sources(conn):
    # Seed one row in each underlying table; the view should return both,
    # tagged with the right source.
    conn.execute(
        "INSERT INTO bpc_insider_supplement "
        "(snapshot_date, ticker, insider_name, filing_date, buy_sell, "
        " stock_or_option, shares, final_shares) "
        "VALUES ('2026-05-27', 'AAA', 'Alice', '2026-05-20', 'Buy', 'Stock', 1000, 5000)"
    )
    conn.execute(
        "INSERT INTO edgar_form4_filings "
        "(accession_number, cik_issuer, ticker, reporting_owner_name, "
        " filed_date, fetched_at) "
        "VALUES ('001', '0000001', 'BBB', 'Bob', '2026-05-21', '2026-05-21T00:00:00')"
    )
    conn.execute(
        "INSERT INTO edgar_form4_transactions "
        "(accession_number, transaction_date, transaction_code, "
        " acquired_disposed, shares, is_open_market) "
        "VALUES ('001', '2026-05-20', 'P', 'A', 500, 1)"
    )
    conn.commit()

    rows = list(conn.execute(
        "SELECT source, ticker, insider_name, buy_sell, shares "
        "FROM v_insider_signal_combined ORDER BY source"
    ))
    sources = [r["source"] for r in rows]
    assert sources == ["bpc", "edgar"]
    by_source = {r["source"]: r for r in rows}
    assert by_source["bpc"]["ticker"] == "AAA"
    assert by_source["bpc"]["shares"] == 1000
    assert by_source["edgar"]["ticker"] == "BBB"
    assert by_source["edgar"]["buy_sell"] == "Buy"  # 'A' → 'Buy'
    assert by_source["edgar"]["shares"] == 500
