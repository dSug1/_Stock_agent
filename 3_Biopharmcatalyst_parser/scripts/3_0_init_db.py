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
EXPECTED_VIEWS = [
    "v_latest_catalysts",
    "v_insider_signal_combined",
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

    actual_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    actual_views = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()
    }
    missing_tables = [t for t in EXPECTED_TABLES if t not in actual_tables]
    missing_views = [v for v in EXPECTED_VIEWS if v not in actual_views]

    print(f"[3_0_init_db] DB: {db_path}")
    print(f"[3_0_init_db] {'created' if is_new else 'opened existing'}; "
          f"{len(actual_tables)} table(s), {len(actual_views)} view(s) present")
    for t in EXPECTED_TABLES:
        marker = "OK " if t in actual_tables else "MISSING"
        print(f"  [{marker}] table   {t}")
    for v in EXPECTED_VIEWS:
        marker = "OK " if v in actual_views else "MISSING"
        print(f"  [{marker}] view    {v}")
    extra_t = sorted(actual_tables - set(EXPECTED_TABLES))
    extra_v = sorted(actual_views - set(EXPECTED_VIEWS))
    if extra_t:
        print(f"  unexpected tables present: {extra_t}")
    if extra_v:
        print(f"  unexpected views present:  {extra_v}")

    conn.close()
    return 0 if not (missing_tables or missing_views) else 1


if __name__ == "__main__":
    sys.exit(main())
