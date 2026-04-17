from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from database.db import (
    CURRENT_SCHEMA_VERSION,
    current_version,
    get_connection,
    init_db,
    now_iso,
)


REQUIRED_TABLES = [
    "schema_version",
    "companies",
    "open_catalysts",
    "probability_history",
    "active_contradictions",
    "institutional_accumulation",
    "outcome_records",
    "action_records",
    "parameters",
    "document_queue",
    "alert_queue",
    "filtered_out_log",
]

TABLES_WITH_TIMESTAMPS = [
    t for t in REQUIRED_TABLES if t != "schema_version"
]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "picker.db"
    init_db(p)
    return p


def _insert_company(
    conn: sqlite3.Connection,
    ticker: str = "ACME",
    tier: str = "active",
) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT INTO companies (ticker, processing_tier, "
        "created_at, updated_at) VALUES (?, ?, ?, ?)",
        (ticker, tier, ts, ts),
    )
    conn.commit()


def _insert_catalyst(
    conn: sqlite3.Connection,
    catalyst_id: str,
    ticker: str,
    bull: float,
    base: float,
    bear: float,
    fingerprint: str,
) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT INTO open_catalysts "
        "(catalyst_id, ticker, current_bull_probability, "
        " current_base_probability, current_bear_probability, "
        " deduplication_fingerprint, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (catalyst_id, ticker, bull, base, bear, fingerprint, ts, ts),
    )
    conn.commit()


def test_init_db_creates_fresh_file(tmp_path: Path) -> None:
    p = tmp_path / "new.db"
    assert not p.exists()
    init_db(p)
    assert p.exists() and p.stat().st_size > 0


def test_all_tables_created(db_path: Path) -> None:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    names = {r["name"] for r in rows}
    missing = [t for t in REQUIRED_TABLES if t not in names]
    assert not missing, f"missing tables: {missing}"


def test_schema_version_recorded(db_path: Path) -> None:
    conn = get_connection(db_path)
    assert current_version(conn) == CURRENT_SCHEMA_VERSION


def test_init_db_is_idempotent(db_path: Path) -> None:
    init_db(db_path)
    init_db(db_path)
    conn = get_connection(db_path)
    count = conn.execute(
        "SELECT COUNT(*) AS c FROM schema_version"
    ).fetchone()["c"]
    assert count == 1


def test_created_updated_columns_present(db_path: Path) -> None:
    conn = get_connection(db_path)
    for table in TABLES_WITH_TIMESTAMPS:
        cols = {
            r["name"]
            for r in conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()
        }
        assert "created_at" in cols, f"{table} missing created_at"
        assert "updated_at" in cols, f"{table} missing updated_at"


def test_foreign_keys_enabled_on_connection(db_path: Path) -> None:
    conn = get_connection(db_path)
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_probability_sum_check_accepts_valid_triple(
    db_path: Path,
) -> None:
    conn = get_connection(db_path)
    _insert_company(conn)
    _insert_catalyst(conn, "c1", "ACME", 0.2, 0.5, 0.3, "fp-ok")
    row = conn.execute(
        "SELECT catalyst_id FROM open_catalysts WHERE catalyst_id = ?",
        ("c1",),
    ).fetchone()
    assert row is not None


def test_probability_sum_check_rejects_sum_gt_1(db_path: Path) -> None:
    conn = get_connection(db_path)
    _insert_company(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_catalyst(conn, "c-bad", "ACME", 0.5, 0.5, 0.5, "fp-bad")


def test_probability_sum_check_rejects_sum_lt_1(db_path: Path) -> None:
    conn = get_connection(db_path)
    _insert_company(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_catalyst(conn, "c-lo", "ACME", 0.1, 0.1, 0.1, "fp-lo")


def test_dedup_fingerprint_unique_within_ticker(db_path: Path) -> None:
    conn = get_connection(db_path)
    _insert_company(conn)
    _insert_catalyst(conn, "c1", "ACME", 0.2, 0.5, 0.3, "dup")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_catalyst(conn, "c2", "ACME", 0.2, 0.5, 0.3, "dup")


def test_dedup_fingerprint_shared_across_tickers(db_path: Path) -> None:
    conn = get_connection(db_path)
    _insert_company(conn, "AAA")
    _insert_company(conn, "BBB")
    _insert_catalyst(conn, "c1", "AAA", 0.2, 0.5, 0.3, "shared")
    _insert_catalyst(conn, "c2", "BBB", 0.2, 0.5, 0.3, "shared")
    rows = conn.execute(
        "SELECT COUNT(*) AS c FROM open_catalysts"
    ).fetchone()
    assert rows["c"] == 2


def test_foreign_key_blocks_orphan_catalyst(db_path: Path) -> None:
    conn = get_connection(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_catalyst(
            conn, "orphan", "NO_SUCH_TICKER", 0.2, 0.5, 0.3, "fp-x"
        )


def test_foreign_key_blocks_orphan_probability_history(
    db_path: Path,
) -> None:
    conn = get_connection(db_path)
    ts = now_iso()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO probability_history "
            "(catalyst_id, timestamp, bull, base, bear, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("no-such-catalyst", ts, 0.2, 0.5, 0.3, ts, ts),
        )
        conn.commit()


def test_processing_tier_check_rejects_bad_value(db_path: Path) -> None:
    conn = get_connection(db_path)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_company(conn, "BAD", tier="not_a_tier")
