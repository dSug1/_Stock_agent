"""One-shot pre-seed: copy 13F data from 1_Stock_Picker/stockpicker.db.

Rationale: 1_Stock_Picker has already ingested 13F-HR filings for many
of the same CIKs tracked here. Copying that data once saves ~25
filings * N overlapping funds of EDGAR/OpenFIGI round-trips on the
very first run.

What gets copied:
- filings_log rows for CIKs that match a fund in this DB (remapped
  from institution_id to fund_id).
- institution_holdings rows (renamed to `holdings`, remapped to
  fund_id).
- cusip_ticker_map rows (verbatim — CUSIP->ticker is fund-independent).

What does NOT get copied:
- Anything for CIKs not in this DB's `funds` table.
- Tier / multiplier / scores — not modelled here.

Idempotent via `INSERT OR IGNORE` on the UNIQUE constraints
(funds+accession, funds+filing_date+cusip, cusip PK). Re-running is
safe and a no-op after the first success.

Usage:
    python 2_Funds_parser/scripts/2_import_from_stockpicker.py
    python 2_Funds_parser/scripts/2_import_from_stockpicker.py \\
        --source ../1_Stock_Picker/stockpicker.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
REPO_ROOT = PROJECT_ROOT.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402

DEFAULT_SOURCE_DB = REPO_ROOT / "1_Stock_Picker" / "stockpicker.db"


def build_cik_map(
    source: sqlite3.Connection, target: sqlite3.Connection
) -> dict[int, int]:
    """Map source.institutions.id -> target.funds.id by matching CIK."""
    source.row_factory = sqlite3.Row
    target.row_factory = sqlite3.Row

    fund_cik_to_id = {
        row["cik"]: row["id"]
        for row in target.execute(
            "SELECT id, cik FROM funds WHERE cik IS NOT NULL"
        )
    }
    mapping: dict[int, int] = {}
    for row in source.execute(
        "SELECT id, cik FROM institutions WHERE cik IS NOT NULL"
    ):
        fund_id = fund_cik_to_id.get(row["cik"])
        if fund_id is not None:
            mapping[row["id"]] = fund_id
    return mapping


def copy_holdings(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    inst_to_fund: dict[int, int],
) -> int:
    if not inst_to_fund:
        return 0
    placeholders = ",".join("?" for _ in inst_to_fund)
    inst_ids = tuple(inst_to_fund.keys())
    rows = source.execute(
        "SELECT institution_id, filing_date, period_of_report, ticker, "
        "cusip, shares, market_value, created_at, updated_at "
        f"FROM institution_holdings WHERE institution_id IN ({placeholders})",
        inst_ids,
    ).fetchall()
    before = target.total_changes
    target.executemany(
        "INSERT OR IGNORE INTO holdings "
        "(fund_id, filing_date, period_of_report, ticker, cusip, "
        " shares, market_value, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                inst_to_fund[r["institution_id"]],
                r["filing_date"],
                r["period_of_report"],
                r["ticker"],
                r["cusip"],
                r["shares"],
                r["market_value"],
                r["created_at"],
                r["updated_at"],
            )
            for r in rows
        ],
    )
    target.commit()
    return target.total_changes - before


def copy_filings_log(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    inst_to_fund: dict[int, int],
) -> int:
    if not inst_to_fund:
        return 0
    placeholders = ",".join("?" for _ in inst_to_fund)
    inst_ids = tuple(inst_to_fund.keys())
    rows = source.execute(
        "SELECT institution_id, filing_date, period_of_report, "
        "accession_number, document_url, holdings_count, parse_status, "
        "created_at, updated_at "
        f"FROM filings_log WHERE institution_id IN ({placeholders})",
        inst_ids,
    ).fetchall()
    before = target.total_changes
    target.executemany(
        "INSERT OR IGNORE INTO filings_log "
        "(fund_id, filing_date, period_of_report, accession_number, "
        " document_url, holdings_count, parse_status, "
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                inst_to_fund[r["institution_id"]],
                r["filing_date"],
                r["period_of_report"],
                r["accession_number"],
                r["document_url"],
                r["holdings_count"],
                r["parse_status"],
                r["created_at"],
                r["updated_at"],
            )
            for r in rows
        ],
    )
    target.commit()
    return target.total_changes - before


def copy_cusip_map(
    source: sqlite3.Connection, target: sqlite3.Connection
) -> int:
    rows = source.execute(
        "SELECT cusip, ticker, exchange, security_type, resolved_date, "
        "created_at, updated_at FROM cusip_ticker_map"
    ).fetchall()
    before = target.total_changes
    target.executemany(
        "INSERT OR IGNORE INTO cusip_ticker_map "
        "(cusip, ticker, exchange, security_type, resolved_date, "
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                r["cusip"], r["ticker"], r["exchange"], r["security_type"],
                r["resolved_date"], r["created_at"], r["updated_at"],
            )
            for r in rows
        ],
    )
    target.commit()
    return target.total_changes - before


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE_DB),
        help=f"Path to stockpicker.db (default: {DEFAULT_SOURCE_DB}).",
    )
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.exists():
        print(f"[FATAL] source DB not found: {source_path}")
        return 1

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    target = get_connection()
    try:
        mapping = build_cik_map(source, target)
        print(
            f"Matched {len(mapping)} institutions in source DB to funds "
            f"in target DB (by CIK)."
        )
        if not mapping:
            print("Nothing to copy; target has no matching CIKs.")
            return 0

        filings_copied = copy_filings_log(source, target, mapping)
        holdings_copied = copy_holdings(source, target, mapping)
        cusips_copied = copy_cusip_map(source, target)
    finally:
        source.close()
        target.close()

    print(
        f"Pre-seed complete: "
        f"{filings_copied} filings_log rows, "
        f"{holdings_copied} holdings rows, "
        f"{cusips_copied} cusip_ticker_map rows."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
