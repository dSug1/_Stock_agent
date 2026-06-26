#!/usr/bin/env python
"""PIT fundamentals (SEC EDGAR companyfacts) CLI — Stage A of the rigorous-panel escalation (D16/D17).

    # fetch companyfacts for every panel ticker (or named tickers) into data/hype.db
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_fundamentals.py --fetch -v
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_fundamentals.py --fetch --ticker CRSP --ticker NTAP

    # inspect a point-in-time snapshot (first-print revenue + shares as known at a date)
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_fundamentals.py --as-of CRSP 2020-01-02

Free, survivorship-free, point-in-time, clean-licensing (D16). SEC etiquette: <=10 req/s.
"""

import argparse
import logging
import sys
import time

from hype_parser import db, fundamentals

DEFAULT_DB = "data/hype.db"
RATE_LIMIT_SLEEP = 0.12  # SEC <= 10 req/s

log = logging.getLogger("5_fundamentals")


def _panel_tickers(conn):
    return [r["ticker"] for r in conn.execute(
        "SELECT DISTINCT ticker FROM panel ORDER BY ticker")]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hype Parser PIT fundamentals (SEC EDGAR companyfacts).")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--fetch", action="store_true", help="fetch companyfacts for panel/named tickers")
    p.add_argument("--ticker", action="append", help="limit to ticker(s); default = all panel names")
    p.add_argument("--as-of", nargs=2, metavar=("TICKER", "DATE"),
                   help="print the PIT fundamentals snapshot for TICKER as-of DATE (YYYY-MM-DD)")
    p.add_argument("--list", action="store_true", help="show fetch log")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    conn = db.connect(args.db)
    try:
        if args.fetch:
            tickers = [t.upper() for t in args.ticker] if args.ticker else _panel_tickers(conn)
            if not tickers:
                print("no tickers (build the panel first, or pass --ticker)")
                return 0
            cik_map = fundamentals.load_ticker_cik_map()
            ok = miss = fail = 0
            for tk in tickers:
                cik = cik_map.get(tk)
                if not cik:
                    fundamentals.log_fetch(conn, tk, None, "no_cik", "ticker not in SEC map", 0)
                    miss += 1
                    log.info("%-6s no CIK in SEC map (skipped)", tk)
                    continue
                status, rows, err = fundamentals.fetch_company_facts(tk, cik)
                if status != "failed":
                    fundamentals.upsert_facts(conn, tk, cik, rows)
                fundamentals.log_fetch(conn, tk, cik, status, err, len(rows))
                nrev = sum(1 for r in rows if r["concept"] == "revenue")
                nsh = sum(1 for r in rows if r["concept"] == "shares")
                print(f"{tk:6} {status:8} revenue_rows={nrev:4} shares_rows={nsh:4} {err or ''}")
                ok += status == "ok"
                fail += status == "failed"
                time.sleep(RATE_LIMIT_SLEEP)
            print(f"\nfetched: {ok} ok, {miss} no-CIK, {fail} failed (of {len(tickers)})")

        if args.as_of:
            tk, d = args.as_of[0].upper(), args.as_of[1]
            snap = fundamentals.fundamentals_as_of(conn, tk, d)
            print(f"{tk} as-of {d}:")
            for k in ("revenue_ttm", "shares", "pre_revenue"):
                print(f"  {k}: {snap[k]}")
            if snap["revenue_ttm"] and snap["shares"]:
                print(f"  revenue_per_share: {snap['revenue_ttm']/snap['shares']:.4f}")

        if args.list or not (args.fetch or args.as_of):
            print(f"{'ticker':6} {'status':8} {'rows':5} fetched_at")
            for r in conn.execute("SELECT * FROM company_facts_log ORDER BY ticker"):
                print(f"{r['ticker']:6} {r['status'] or '':8} {r['n_rows'] or 0:5} {r['fetched_at']}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
