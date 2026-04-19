"""Top-N tickers by TWOS score for the latest run.

Run from 1_Stock_Picker/ with PYTHONPATH=src:

    python scripts/top_twos.py          # default: top 30
    python scripts/top_twos.py 50       # top 50
"""
from __future__ import annotations

import sys

from database.db import get_connection


def main(limit: int = 30) -> int:
    conn = get_connection()
    try:
        run_date = conn.execute(
            "SELECT MAX(run_date) FROM twos_scores"
        ).fetchone()[0]
        if run_date is None:
            print("no twos_scores rows yet")
            return 1
        print(f"=== Top {limit} tickers by TWOS — run_date={run_date} ===")
        rows = conn.execute(
            "SELECT ticker, twos_score, processing_tier, "
            "       institution_count, crowding_flag, qoq_change_signal "
            "FROM twos_scores "
            "WHERE run_date = ? "
            "ORDER BY twos_score DESC "
            "LIMIT ?",
            (run_date, limit),
        ).fetchall()
        for r in rows:
            flag = "CROWDED" if r["crowding_flag"] else ""
            ticker = r["ticker"]
            score = r["twos_score"]
            tier = r["processing_tier"]
            inst = r["institution_count"]
            signal = r["qoq_change_signal"]
            print(
                f"{ticker:8} TWOS:{score:6.3f}  "
                f"tier:{tier:10}  "
                f"institutions:{inst:2}  "
                f"{signal:25} {flag}"
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    raise SystemExit(main(n))
