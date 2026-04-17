from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

CURRENT_SCHEMA_VERSION = 4

# Default DB location when callers pass no path. Resolved against cwd so
# running from 1_Stock_Picker/ puts the file at 1_Stock_Picker/stockpicker.db.
DEFAULT_DB_PATH = Path("stockpicker.db")


def _migration_v3_add_institution_cik_fields(
    conn: sqlite3.Connection,
) -> None:
    cols = {
        r["name"]
        for r in conn.execute(
            "PRAGMA table_info(institutions)"
        ).fetchall()
    }
    if "cik" not in cols:
        conn.execute("ALTER TABLE institutions ADD COLUMN cik TEXT")
    if "edgar_name" not in cols:
        conn.execute(
            "ALTER TABLE institutions ADD COLUMN edgar_name TEXT"
        )


# Migrations are keyed by target version. Each callable must be idempotent
# (safe to re-run on a fresh DB where schema.sql already created the target
# shape). apply_migrations() runs every entry whose key > current version.
def _migration_v4_add_filings_log(
    conn: sqlite3.Connection,
) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS filings_log ("
        " id INTEGER PRIMARY KEY,"
        " institution_id INTEGER NOT NULL"
        "   REFERENCES institutions(id),"
        " filing_date TEXT NOT NULL,"
        " period_of_report TEXT NOT NULL,"
        " accession_number TEXT NOT NULL,"
        " document_url TEXT,"
        " holdings_count INTEGER NOT NULL DEFAULT 0,"
        " parse_status TEXT NOT NULL DEFAULT 'success',"
        " created_at TEXT NOT NULL,"
        " updated_at TEXT NOT NULL,"
        " UNIQUE(institution_id, accession_number)"
        ")"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_filings_log_institution "
        "ON filings_log(institution_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_filings_log_filing_date "
        "ON filings_log(filing_date)"
    )


MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    3: _migration_v3_add_institution_cik_fields,
    4: _migration_v4_add_filings_log,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection(
    db_path: str | Path | None = None,
) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if not path.exists():
        # First-time open: populate schema + seed institutions so callers
        # never need a separate init_db step before use.
        init_db(path)
    else:
        # Existing DB: apply any migrations added since the file was
        # created so a schema bump in code never leaves callers on a
        # stale schema.
        probe = sqlite3.connect(str(path))
        probe.row_factory = sqlite3.Row
        try:
            stale = current_version(probe) < CURRENT_SCHEMA_VERSION
        finally:
            probe.close()
        if stale:
            apply_migrations(path)
    conn = sqlite3.connect(str(path))
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


def init_db(db_path: str | Path | None = None) -> None:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executescript(_load_schema_sql())
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
        if current_version(conn) < CURRENT_SCHEMA_VERSION:
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


def apply_migrations(db_path: str | Path | None = None) -> int:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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
