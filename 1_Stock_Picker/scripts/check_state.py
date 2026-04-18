"""Post-ingest diagnostic — inspect filings_log, holdings, CUSIPs, TWOS.

Run from 1_Stock_Picker/ with the venv active:

    python scripts/check_state.py

Reads stockpicker.db in the current working directory.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def main(db_path: str = "stockpicker.db") -> int:
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # --- Ingestion outcomes (filings_log) ---
    print("=== filings_log per institution ===")
    for r in conn.execute(
        """
        SELECT i.tier, i.name,
               SUM(fl.parse_status='success')     AS ok,
               SUM(fl.parse_status='empty')       AS empty,
               SUM(fl.parse_status='parse_error') AS perr,
               COALESCE(SUM(fl.holdings_count),0) AS holdings
        FROM institutions i
        LEFT JOIN filings_log fl ON fl.institution_id = i.id
        GROUP BY i.id
        HAVING ok + empty + perr > 0
        ORDER BY i.tier, i.name
        """
    ):
        print(
            f"  {r['tier']:3} {r['name']:42} "
            f"ok:{r['ok']:2} empty:{r['empty']:2} "
            f"err:{r['perr']:2} holdings:{r['holdings']:5}"
        )

    missing_rows = conn.execute(
        """
        SELECT i.tier, i.name, i.cik
        FROM institutions i
        WHERE i.cik IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM filings_log fl
            WHERE fl.institution_id = i.id
          )
        ORDER BY i.tier, i.name
        """
    ).fetchall()
    print(
        f"  (institutions with CIK but zero filings_log rows: "
        f"{len(missing_rows)})"
    )
    for r in missing_rows:
        print(f"    {r['tier']:3} {r['name']:42} CIK={r['cik']}")

    # --- CUSIP resolution ---
    tot = conn.execute(
        "SELECT COUNT(*) c FROM cusip_ticker_map"
    ).fetchone()["c"]
    res = conn.execute(
        "SELECT COUNT(*) c FROM cusip_ticker_map WHERE ticker IS NOT NULL"
    ).fetchone()["c"]
    pct = 100 * res // max(tot, 1)
    print(f"\n=== CUSIP cache: {tot} cached, {res} resolved ({pct}% hit) ===")

    # --- TWOS latest run ---
    last = conn.execute(
        "SELECT MAX(run_date) d FROM twos_scores"
    ).fetchone()["d"]
    if last is None:
        print("\n=== twos_scores: empty ===")
    else:
        print(f"\n=== twos_scores (run_date={last}) ===")
        for r in conn.execute(
            """
            SELECT processing_tier, COUNT(*) n
            FROM twos_scores WHERE run_date = ?
            GROUP BY processing_tier ORDER BY processing_tier
            """,
            (last,),
        ):
            print(f"  {r['processing_tier']:12} {r['n']:5}")

        print("\n  Top 10 by TWOS:")
        for r in conn.execute(
            """
            SELECT ticker, twos_score, institution_count,
                   processing_tier, qoq_change_signal, crowding_flag
            FROM twos_scores WHERE run_date = ?
            ORDER BY twos_score DESC LIMIT 10
            """,
            (last,),
        ):
            c = " [crowded]" if r["crowding_flag"] else ""
            print(
                f"    {r['ticker']:8} TWOS:{r['twos_score']:7.3f} "
                f"inst:{r['institution_count']:2} "
                f"{r['processing_tier']:10} "
                f"{r['qoq_change_signal']}{c}"
            )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "stockpicker.db"))
