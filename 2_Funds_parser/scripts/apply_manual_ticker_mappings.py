"""Apply a user-edited TSV/CSV of (cusip, ticker) pairs to the DB.

Companion to list_unresolved_cusips.py. Writes to cusip_ticker_map and
holdings with ticker_source='manual'.

Policy:
  - UPSERT into cusip_ticker_map:
      * New row → insert with ticker_source='manual', resolved_date=today.
      * Existing row with ticker=NULL → update to the new ticker + 'manual'.
      * Existing row with a non-null ticker → conflict; skip and log.
  - UPDATE holdings SET ticker=?, ticker_source='manual', updated_at=now
    WHERE cusip=? AND ticker IS NULL (never overwrites an OpenFIGI hit).
  - Blank ticker cells, comment lines (#), and blank lines are skipped.

Input columns: `cusip` and `ticker`. Extra columns are ignored.
Detects delimiter from the first line (tab → TSV, else CSV).

Usage:
    python 2_Funds_parser/scripts/apply_manual_ticker_mappings.py --input manual_map.tsv
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection, now_iso  # noqa: E402


def _read_mapping(path: Path) -> list[tuple[str, str]]:
    """Parse input file into a list of (cusip, ticker) pairs."""
    text = path.read_text(encoding="utf-8")
    # Sniff delimiter from first non-comment line.
    delim = "\t"
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        delim = "\t" if "\t" in line else ","
        break

    reader = csv.DictReader(
        (ln for ln in text.splitlines() if not ln.strip().startswith("#")),
        delimiter=delim,
    )
    if reader.fieldnames is None:
        raise ValueError(f"{path}: no header row found")
    cols = {c.lower().strip(): c for c in reader.fieldnames}
    if "cusip" not in cols or "ticker" not in cols:
        raise ValueError(
            f"{path}: header must include 'cusip' and 'ticker'; got {reader.fieldnames}"
        )

    pairs: list[tuple[str, str]] = []
    for row in reader:
        cusip = (row.get(cols["cusip"]) or "").strip()
        ticker = (row.get(cols["ticker"]) or "").strip()
        if not cusip or not ticker:
            continue
        pairs.append((cusip, ticker.upper()))
    return pairs


def _apply_one(
    conn: sqlite3.Connection,
    cusip: str,
    ticker: str,
    ts: str,
) -> str:
    """Apply one mapping. Returns 'inserted' | 'updated' | 'conflict' | 'noop'."""
    row = conn.execute(
        "SELECT ticker, ticker_source FROM cusip_ticker_map WHERE cusip = ?",
        (cusip,),
    ).fetchone()

    if row is None:
        conn.execute(
            "INSERT INTO cusip_ticker_map "
            "(cusip, ticker, exchange, security_type, ticker_source, "
            " resolved_date, created_at, updated_at) "
            "VALUES (?, ?, NULL, NULL, 'manual', ?, ?, ?)",
            (cusip, ticker, ts, ts, ts),
        )
        status = "inserted"
    elif row[0] is None:
        conn.execute(
            "UPDATE cusip_ticker_map "
            "SET ticker = ?, ticker_source = 'manual', "
            "    resolved_date = ?, updated_at = ? "
            "WHERE cusip = ?",
            (ticker, ts, ts, cusip),
        )
        status = "updated"
    elif row[0] == ticker:
        # Same ticker already present — make sure ticker_source is not
        # lost if it was NULL (legacy rows).
        if row[1] is None:
            conn.execute(
                "UPDATE cusip_ticker_map SET ticker_source = 'manual', "
                "updated_at = ? WHERE cusip = ?",
                (ts, cusip),
            )
        return "noop"
    else:
        return "conflict"

    # Backfill holdings.
    res = conn.execute(
        "UPDATE holdings SET ticker = ?, ticker_source = 'manual', "
        "updated_at = ? WHERE cusip = ? AND ticker IS NULL",
        (ticker, ts, cusip),
    )
    # res.rowcount is returned via conn.total_changes in sqlite3; we
    # rely on caller-level counting for holdings updates.
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", required=True,
        help="Path to a TSV or CSV with 'cusip' and 'ticker' columns.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input file not found: {input_path}", file=sys.stderr)
        return 1

    pairs = _read_mapping(input_path)
    if not pairs:
        print("No valid (cusip, ticker) rows found in input.")
        return 0

    conn = get_connection()
    try:
        ts = now_iso()
        stats = {"inserted": 0, "updated": 0, "conflict": 0, "noop": 0}
        holdings_updated = 0
        conflicts: list[tuple[str, str, str]] = []

        for cusip, ticker in pairs:
            before = conn.total_changes
            status = _apply_one(conn, cusip, ticker, ts)
            stats[status] += 1
            if status == "conflict":
                existing = conn.execute(
                    "SELECT ticker FROM cusip_ticker_map WHERE cusip = ?",
                    (cusip,),
                ).fetchone()
                conflicts.append((cusip, existing[0] if existing else "?", ticker))
            after = conn.total_changes
            # Crude but correct: holdings UPDATE happens after the map
            # write, so (after - before - {0 or 1}) = holdings rows touched.
            if status in ("inserted", "updated"):
                holdings_updated += max(0, (after - before) - 1)

        conn.commit()
    finally:
        conn.close()

    print(
        f"cusip_ticker_map: {stats['inserted']} inserted, "
        f"{stats['updated']} updated, "
        f"{stats['noop']} no-op, "
        f"{stats['conflict']} conflicts skipped."
    )
    print(f"holdings: {holdings_updated} rows tickerised (ticker_source='manual').")
    if conflicts:
        print()
        print("Conflicts (existing cusip_ticker_map ticker != proposed):")
        for cusip, existing, proposed in conflicts:
            print(f"  {cusip}: existing='{existing}', proposed='{proposed}' (skipped)")
        print(
            "Edit the DB manually or drop the conflicting row from the input "
            "if the proposed ticker is actually correct."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
