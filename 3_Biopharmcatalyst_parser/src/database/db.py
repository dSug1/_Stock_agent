"""SQLite helper for 3_Biopharmcatalyst_parser.

Opens `data/biotech.db` (resolved relative to this file's project root)
and applies schema.sql idempotently on every connect. Mirrors the
2_Funds_parser/src/database/db.py pattern — no migration framework
yet, schema edits expected to be additive.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "biotech.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


# Additive migrations applied on every get_connection() call. Each tuple is
# (table, column, decl). CREATE TABLE IF NOT EXISTS in schema.sql covers
# fresh DBs; this list handles existing DBs that pre-date the column.
_ADDITIVE_MIGRATIONS: list[tuple[str, str, str]] = [
    # D35 — M8 rescue. `rescued=1` flips catalysts back into the eligible
    # pool after being H1/H3/H5-excluded. `rescue_class` records which
    # rescue class(es) applied (concatenated letters: 'A','B','C','BC',...).
    ("catalyst_scores", "rescued",      "INTEGER DEFAULT 0"),
    ("catalyst_scores", "rescue_class", "TEXT"),
]


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    for table, col, decl in _ADDITIVE_MIGRATIONS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.OperationalError:
            continue
        if col not in cols:
            log.info("biotech.db: ADD COLUMN %s.%s %s", table, col, decl)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    # D35 — indexes that depend on migrated columns.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scores_rescued "
        "ON catalyst_scores (snapshot_date, rescued)"
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _apply_additive_migrations(conn)
    return conn
