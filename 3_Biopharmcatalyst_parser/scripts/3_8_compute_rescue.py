"""Module 8 — compute rescue eligibility + UPDATE catalyst_scores.

Reads every hard_pass=0 row in the rolling-view (latest snapshot per
catalyst), classifies it via module_8.rescue_filter, and writes the
result back to:
    catalyst_scores.rescued       (0 / 1)
    catalyst_scores.rescue_class  ('A' / 'B' / 'C' / 'AB' / ... / NULL)

Idempotent: re-running rewrites the same values. Designed to run as a
post-M6 step in the orchestrator bat (after M6 emits fail_reasons but
before M8's dispatch script runs).

Usage (from 3_Biopharmcatalyst_parser/):
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_compute_rescue.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_compute_rescue.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import DEFAULT_DB_PATH as BIOTECH_DB, get_connection  # noqa: E402
from module_6_5.fundamentals_db import DEFAULT_DB_PATH as FUNDAMENTALS_DB  # noqa: E402
from module_8 import classify_rows  # noqa: E402


def _load_mcap_map(fundamentals_db: Path, biotech_db: Path) -> dict[str, Optional[float]]:
    """Best-available mcap per ticker.

    Priority: fundamentals.db.financials.market_cap_fdsc_usd (M6.5 FDSC,
    most recent period) > catalyst_snapshots.market_cap_usd (BPC docx).
    """
    mcap: dict[str, Optional[float]] = {}
    if fundamentals_db.exists():
        with sqlite3.connect(fundamentals_db) as cx:
            cx.row_factory = sqlite3.Row
            for r in cx.execute(
                "SELECT ticker, market_cap_fdsc_usd FROM financials "
                "WHERE market_cap_fdsc_usd IS NOT NULL "
                "ORDER BY period_end_date DESC"
            ):
                if r["ticker"] not in mcap:
                    mcap[r["ticker"]] = float(r["market_cap_fdsc_usd"])
    # Fallback: BPC's static mcap (some rows have it even when M6.5 doesn't).
    conn = get_connection(biotech_db)
    try:
        for r in conn.execute(
            "SELECT DISTINCT ticker, market_cap_usd FROM catalyst_snapshots "
            "WHERE market_cap_usd IS NOT NULL"
        ):
            if r["ticker"] not in mcap:
                mcap[r["ticker"]] = float(r["market_cap_usd"])
    finally:
        conn.close()
    return mcap


def _fetch_excluded_rows(biotech_db: Path) -> list[dict]:
    """Rolling-view excluded rows (hard_pass=0, latest snapshot per catalyst)."""
    conn = get_connection(biotech_db)
    try:
        rows = conn.execute(
            """
            WITH latest AS (
              SELECT ticker, drug, nct_number, next_catalyst_type,
                     MAX(snapshot_date) AS max_snap
              FROM catalyst_scores
              GROUP BY ticker, drug, nct_number, next_catalyst_type
            )
            SELECT cs.snapshot_date, cs.ticker, cs.drug, cs.nct_number,
                   cs.next_catalyst_type, cs.fail_reasons,
                   cs.rescued AS prev_rescued,
                   cs.rescue_class AS prev_rescue_class
            FROM catalyst_scores cs
            JOIN latest l USING (ticker, drug, nct_number, next_catalyst_type)
            WHERE cs.snapshot_date = l.max_snap
              AND cs.hard_pass = 0
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _write_back(
    biotech_db: Path,
    updates: list[tuple[str, Optional[str], str, str, str, str, str]],
) -> int:
    """UPDATE catalyst_scores SET rescued=?, rescue_class=? for each row.

    Each update tuple: (rescued, rescue_class, snapshot_date, ticker, drug,
    nct_number, next_catalyst_type) — values come first because that's the
    SQL parameter order.
    """
    if not updates:
        return 0
    conn = get_connection(biotech_db)
    try:
        cur = conn.executemany(
            """
            UPDATE catalyst_scores
            SET rescued = ?, rescue_class = ?
            WHERE snapshot_date = ?
              AND ticker = ?
              AND drug = ?
              AND nct_number = ?
              AND next_catalyst_type = ?
            """,
            updates,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB)
    parser.add_argument("--fundamentals-db", type=Path, default=FUNDAMENTALS_DB)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing.")
    args = parser.parse_args()

    print(f"[3_8_compute_rescue] biotech-db: {args.biotech_db}")
    print(f"[3_8_compute_rescue] fundamentals-db: {args.fundamentals_db}")

    rows = _fetch_excluded_rows(args.biotech_db)
    print(f"[3_8_compute_rescue] rolling-view excluded rows: {len(rows)}")

    mcap_map = _load_mcap_map(args.fundamentals_db, args.biotech_db)
    print(f"[3_8_compute_rescue] mcap lookup populated for {len(mcap_map)} tickers")

    # Also need next_catalyst_type for each row to drive Class C scope.
    # It's already in catalyst_scores so it's in `rows` above.
    decisions = classify_rows(rows, mcap_lookup=mcap_map)

    # Bucket counts + per-class breakdown
    n_rescued = sum(1 for _, d in decisions if d.rescued)
    by_class: dict[str, int] = {}
    for _, d in decisions:
        if d.rescued:
            by_class[d.rescue_class_str or "?"] = by_class.get(d.rescue_class_str or "?", 0) + 1

    print()
    print(f"  → rescued: {n_rescued} catalysts, stays excluded: {len(decisions) - n_rescued}")
    for cls in sorted(by_class):
        print(f"     class={cls}: {by_class[cls]}")
    print()

    # Build the UPDATE batch — only write rows whose rescued/rescue_class
    # actually CHANGED, so the script is idempotent and quiet on re-runs.
    updates: list[tuple] = []
    n_changed = 0
    n_unchanged = 0
    for row, decision in decisions:
        new_rescued = 1 if decision.rescued else 0
        new_class = decision.rescue_class_str
        if new_rescued == (row.get("prev_rescued") or 0) \
                and new_class == row.get("prev_rescue_class"):
            n_unchanged += 1
            continue
        updates.append((
            new_rescued, new_class,
            row["snapshot_date"], row["ticker"], row["drug"],
            row["nct_number"], row["next_catalyst_type"],
        ))
        n_changed += 1

    # Also need to reset previously-rescued rows back to 0 if they're now
    # hard_pass=1 (because M6 was re-run and they passed all gates). The
    # query above doesn't include them — let's clear them in a separate pass.
    conn = get_connection(args.biotech_db)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM catalyst_scores "
            "WHERE hard_pass = 1 AND (rescued = 1 OR rescue_class IS NOT NULL)"
        )
        n_to_clear = cur.fetchone()[0]
    finally:
        conn.close()

    print(f"  → changes to write: {n_changed}  (unchanged: {n_unchanged})")
    if n_to_clear:
        print(f"  → ALSO clearing {n_to_clear} hard_pass=1 rows that had stale rescue flags")

    if args.dry_run:
        print()
        print("[3_8_compute_rescue] --dry-run: no writes performed.")
        return 0

    written = _write_back(args.biotech_db, updates)
    print(f"[3_8_compute_rescue] UPDATE catalyst_scores: {written} row(s)")

    if n_to_clear:
        conn = get_connection(args.biotech_db)
        try:
            conn.execute(
                "UPDATE catalyst_scores SET rescued = 0, rescue_class = NULL "
                "WHERE hard_pass = 1 AND (rescued = 1 OR rescue_class IS NOT NULL)"
            )
            conn.commit()
            print(f"[3_8_compute_rescue] cleared {n_to_clear} stale rescue flag(s) on hard-pass rows")
        finally:
            conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
