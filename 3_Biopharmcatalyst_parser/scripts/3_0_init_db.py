"""Module 0 — initialize data/biotech.db with the full schema.

Idempotent: re-running is a safe no-op (uses CREATE TABLE IF NOT EXISTS).

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_init_db.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import DEFAULT_DB_PATH, get_connection  # noqa: E402


EXPECTED_TABLES = [
    "catalyst_snapshots",
    "edgar_form4_filings",
    "edgar_form4_transactions",
    "edgar_ownership_filings",
    "bpc_insider_supplement",
    "ingest_log",
    "ticker_cik_map",
    "catalyst_timing",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help=f"Override DB path (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    db_path = args.db_path or DEFAULT_DB_PATH
    is_new = not db_path.exists()
    conn = get_connection(db_path)

    actual = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    missing = [t for t in EXPECTED_TABLES if t not in actual]
    extra = sorted(actual - set(EXPECTED_TABLES))

    print(f"[3_0_init_db] DB: {db_path}")
    print(f"[3_0_init_db] {'created' if is_new else 'opened existing'}; {len(actual)} table(s) present")
    for t in EXPECTED_TABLES:
        marker = "OK " if t in actual else "MISSING"
        print(f"  [{marker}] {t}")
    if extra:
        print(f"  unexpected tables present: {extra}")

    conn.close()
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
