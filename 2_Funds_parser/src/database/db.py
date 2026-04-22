"""Minimal SQLite helper for 2_Funds_parser.

Opens 2_fundparser.db at the project root (resolved relative to
this file) and applies schema.sql idempotently on every connect.
No migration framework yet — schema evolves by edit-in-place until
we outgrow it.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "2_fundparser.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# SEC Form 13F amendment (Release 34-93978) effective 2023-01-03 changed
# the unit of the <value> field from thousands of USD to whole USD. Any
# filing dated strictly before this cutoff is multiplied by 1000 so
# downstream code can treat market_value uniformly as raw USD.
MARKET_VALUE_RAW_USD_CUTOFF = "2023-01-03"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    """Bring an existing DB up to the current schema without data loss.

    SQLite has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so we
    probe `PRAGMA table_info` and only add columns that are missing.
    Schema edits are otherwise expected to be additive; any column
    rename or drop needs a proper migration script.
    """
    holdings_cols = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(holdings)"
        ).fetchall()
    }
    if holdings_cols and "name_of_issuer" not in holdings_cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN name_of_issuer TEXT")
    if holdings_cols and "ticker_source" not in holdings_cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN ticker_source TEXT")
        # Existing tickers came from the OpenFIGI path.
        conn.execute(
            "UPDATE holdings SET ticker_source = 'openfigi' "
            "WHERE ticker IS NOT NULL AND ticker_source IS NULL"
        )
    if holdings_cols and "title_of_class" not in holdings_cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN title_of_class TEXT")
    if holdings_cols and "put_call" not in holdings_cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN put_call TEXT")

    cusip_map_cols = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(cusip_ticker_map)"
        ).fetchall()
    }
    if cusip_map_cols and "ticker_source" not in cusip_map_cols:
        conn.execute("ALTER TABLE cusip_ticker_map ADD COLUMN ticker_source TEXT")
        # Existing rows were all written by the OpenFIGI resolver.
        conn.execute(
            "UPDATE cusip_ticker_map SET ticker_source = 'openfigi' "
            "WHERE ticker_source IS NULL"
        )
    conn.commit()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _apply_additive_migrations(conn)
    return conn
