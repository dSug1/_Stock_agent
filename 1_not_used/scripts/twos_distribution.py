"""TWOS score distribution and threshold sensitivity.

Answers: how many tickers sit at each score range, how many survive
each active-threshold candidate, and what kinds of tickers slip into
active monitoring with no Tier 1A/1B holder.

Run from 1_not_used/ with PYTHONPATH=src:

    python scripts/twos_distribution.py
"""
from __future__ import annotations

from database.db import get_connection


def main() -> int:
    conn = get_connection()
    try:
        run_date = conn.execute(
            "SELECT MAX(run_date) FROM twos_scores"
        ).fetchone()[0]
        if run_date is None:
            print("no twos_scores rows yet")
            return 1
        print(f"Run date: {run_date}\n")

        print("=== TWOS score distribution ===")
        buckets = [
            (0.001,  0.1,  "0.001-0.1"),
            (0.1,    0.5,  "0.1-0.5  "),
            (0.5,    1.0,  "0.5-1.0  "),
            (1.0,    2.0,  "1.0-2.0  "),
            (2.0,    5.0,  "2.0-5.0  "),
            (5.0,   10.0,  "5.0-10.0 "),
            (10.0, 1e12,   "10.0+    "),
        ]
        for lo, hi, label in buckets:
            count = conn.execute(
                "SELECT COUNT(*) FROM twos_scores "
                "WHERE run_date = ? AND twos_score >= ? AND twos_score < ?",
                (run_date, lo, hi),
            ).fetchone()[0]
            bar = "#" * (count // 50)
            print(f"  {label}  {count:5}  {bar}")
        print()

        print("=== Active count at different TWOS thresholds ===")
        for t in [0.5, 1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0]:
            count = conn.execute(
                "SELECT COUNT(*) FROM twos_scores "
                "WHERE run_date = ? AND twos_score >= ?",
                (run_date, t),
            ).fetchone()[0]
            marker = " <- TARGET" if 150 <= count <= 250 else ""
            print(f"  TWOS >= {t:5.1f}  ->  {count:5} tickers{marker}")
        print()

        print("=== Tickers in active monitoring with TWOS = 0.0 ===")
        zero_active = conn.execute(
            "SELECT COUNT(*) FROM twos_scores "
            "WHERE run_date = ? AND processing_tier = 'active' "
            "AND twos_score = 0.0",
            (run_date,),
        ).fetchone()[0]
        print(
            f"  Tickers with TWOS = 0.0 but tier='active' "
            f"(signal-driven): {zero_active}\n"
        )

        print("=== Tickers with Tier 1A/1B holder (filings >= 2025-01-01) ===")
        tier1_count = conn.execute(
            "SELECT COUNT(DISTINCT ih.ticker) "
            "FROM institution_holdings ih "
            "JOIN institutions i ON ih.institution_id = i.id "
            "WHERE i.tier IN ('1A', '1B') "
            "AND ih.ticker IS NOT NULL "
            "AND ih.filing_date >= '2025-01-01'"
        ).fetchone()[0]
        print(f"  Tickers held by any Tier 1A/1B fund: {tier1_count}\n")

        print(
            "=== Samples: active tickers w/ TWOS 0.5-2.0 "
            "and no Tier 1A/1B holder ==="
        )
        samples = conn.execute(
            "SELECT ts.ticker, ts.twos_score, ts.institution_count "
            "FROM twos_scores ts "
            "WHERE ts.run_date = ? "
            "AND ts.twos_score BETWEEN 0.5 AND 2.0 "
            "AND ts.processing_tier = 'active' "
            "AND ts.ticker NOT IN ( "
            "  SELECT DISTINCT ih.ticker "
            "  FROM institution_holdings ih "
            "  JOIN institutions i ON ih.institution_id = i.id "
            "  WHERE i.tier IN ('1A', '1B') "
            "  AND ih.ticker IS NOT NULL "
            ") "
            "ORDER BY ts.twos_score DESC LIMIT 5",
            (run_date,),
        ).fetchall()
        for s in samples:
            print(
                f"  {s['ticker']:8}  TWOS:{s['twos_score']:6.3f}  "
                f"institutions:{s['institution_count']}"
            )
            holders = conn.execute(
                "SELECT i.name, i.tier, i.multiplier, "
                "       MAX(ih.filing_date) AS latest_filing "
                "FROM institution_holdings ih "
                "JOIN institutions i ON ih.institution_id = i.id "
                "WHERE ih.ticker = ? "
                "AND ih.filing_date >= '2025-01-01' "
                "GROUP BY i.id "
                "ORDER BY i.multiplier DESC",
                (s["ticker"],),
            ).fetchall()
            for h in holders:
                print(
                    f"    Tier {h['tier']} ({h['multiplier']}x)  {h['name']}"
                )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
