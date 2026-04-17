from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

CURRENT_SCHEMA_VERSION = 2

# Migrations are keyed by target version. To introduce schema v2, add
# entry 2: <callable(conn)> that mutates the DB into its v2 shape.
# apply_migrations() runs every entry whose key > current version, in order.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _load_schema_sql() -> str:
    return SCHEMA_FILE.read_text(encoding="utf-8")


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    row = conn.execute(
        "SELECT MAX(version) AS v FROM schema_version"
    ).fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def init_db(db_path: str | Path) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(_load_schema_sql())
        version = current_version(conn)
        if version < CURRENT_SCHEMA_VERSION:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version(version, applied_at) "
                "VALUES (?, ?)",
                (CURRENT_SCHEMA_VERSION, now_iso()),
            )
            conn.commit()
        # Late import avoids a circular dependency at module-load time.
        from layer_minus1.institution_registry import seed_institutions
        seed_institutions(conn)
    finally:
        conn.close()


def apply_migrations(db_path: str | Path) -> int:
    conn = get_connection(db_path)
    try:
        version = current_version(conn)
        for target in sorted(MIGRATIONS):
            if target <= version:
                continue
            with conn:
                MIGRATIONS[target](conn)
                conn.execute(
                    "INSERT INTO schema_version(version, applied_at) "
                    "VALUES (?, ?)",
                    (target, now_iso()),
                )
            version = target
        return version
    finally:
        conn.close()
