"""Maintain the `delisted_tickers` table in biotech.db (D34).

Tickers on this list fail H6 in M6's hard-pass gates and are excluded
from M7 dispatch + the live-price server's allowlist.

Usage:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_flag_delisted_tickers.py --list
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_flag_delisted_tickers.py \
        --add DVAX --reason "yfinance 404; reverse-split delisting"
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_flag_delisted_tickers.py --remove DVAX
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_flag_delisted_tickers.py --add DVAX --rescore

`--rescore` (default ON when `--add` is given) walks existing
catalyst_scores rows for the flagged ticker and flips them in-place:
hard_pass -> 0, fail_reasons gets "H6" appended (or set if NULL),
timing_bucket -> NULL. This avoids needing a full M6 re-run after flagging
a single ticker — the next M6 run will re-derive everything from scratch
anyway.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
DEFAULT_BIOTECH_DB = PROJECT_ROOT / "data" / "biotech.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_table(cx: sqlite3.Connection) -> None:
    cx.execute(
        """
        CREATE TABLE IF NOT EXISTS delisted_tickers (
            ticker       TEXT PRIMARY KEY,
            flagged_at   TIMESTAMP NOT NULL,
            reason       TEXT,
            source       TEXT
        )
        """
    )


def _list(cx: sqlite3.Connection) -> int:
    cx.row_factory = sqlite3.Row
    rows = cx.execute(
        "SELECT ticker, flagged_at, reason, source FROM delisted_tickers ORDER BY ticker"
    ).fetchall()
    if not rows:
        print("(delisted_tickers is empty)")
        return 0
    print(f"{'ticker':8s} {'flagged_at':21s} {'source':15s} reason")
    print("-" * 80)
    for r in rows:
        print(f"  {r['ticker']:6s} {r['flagged_at'][:19]:21s} {(r['source'] or ''):15s} {r['reason'] or ''}")
    return 0


def _rescore_in_place(cx: sqlite3.Connection, ticker: str) -> int:
    """Flip every catalyst_scores row for `ticker` to hard_pass=0 with H6."""
    cx.row_factory = sqlite3.Row
    rows = cx.execute(
        "SELECT snapshot_date, ticker, drug, nct_number, next_catalyst_type, "
        "       hard_pass, fail_reasons "
        "FROM catalyst_scores WHERE ticker = ?",
        (ticker,),
    ).fetchall()
    if not rows:
        print(f"  no catalyst_scores rows for {ticker}; nothing to rescore")
        return 0
    n_flipped = 0
    for r in rows:
        existing = (r["fail_reasons"] or "").split(",") if r["fail_reasons"] else []
        existing = [c for c in existing if c]              # drop empties
        if "H6" not in existing:
            existing.append("H6")
        new_reasons = ",".join(existing) if existing else "H6"
        cx.execute(
            """
            UPDATE catalyst_scores
            SET hard_pass = 0, fail_reasons = ?, timing_bucket = NULL
            WHERE snapshot_date = ? AND ticker = ? AND drug = ?
              AND nct_number = ? AND next_catalyst_type = ?
            """,
            (new_reasons, r["snapshot_date"], r["ticker"], r["drug"],
             r["nct_number"], r["next_catalyst_type"]),
        )
        n_flipped += 1
    return n_flipped


def _add(cx: sqlite3.Connection, ticker: str, reason: str,
         source: str, rescore: bool) -> int:
    cx.execute(
        """
        INSERT INTO delisted_tickers(ticker, flagged_at, reason, source)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            flagged_at = excluded.flagged_at,
            reason     = excluded.reason,
            source     = excluded.source
        """,
        (ticker, _now_iso(), reason, source),
    )
    print(f"  flagged {ticker} as delisted (reason={reason!r})")
    if rescore:
        n = _rescore_in_place(cx, ticker)
        print(f"  rescored {n} catalyst_scores row(s) for {ticker} -> hard_pass=0 with H6")
    return 0


def _remove(cx: sqlite3.Connection, ticker: str) -> int:
    cx.execute("DELETE FROM delisted_tickers WHERE ticker = ?", (ticker,))
    print(f"  unflagged {ticker}")
    print(f"  NOTE: catalyst_scores rows are NOT auto-restored — run M6 to re-score.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--biotech-db", type=Path, default=DEFAULT_BIOTECH_DB)
    parser.add_argument("--list", action="store_true",
                        help="Print the current delisted set and exit")
    parser.add_argument("--add", metavar="TICKER",
                        help="Add (or update) a ticker; also rescores by default")
    parser.add_argument("--remove", metavar="TICKER",
                        help="Remove a ticker from the delisted set")
    parser.add_argument("--reason", default="",
                        help="Free-text reason; recommended when --add'ing")
    parser.add_argument("--source", default="manual",
                        help="manual / yfinance-probe / edgar-suspension / ...")
    parser.add_argument("--no-rescore", action="store_true",
                        help="Skip the in-place catalyst_scores rewrite (just touch the table)")
    args = parser.parse_args()

    cx = sqlite3.connect(args.biotech_db, timeout=10)
    try:
        cx.execute("PRAGMA foreign_keys = ON")
        _ensure_table(cx)

        if args.list:
            return _list(cx)
        if args.add:
            ticker = args.add.upper().strip()
            with cx:
                _add(cx, ticker,
                     reason=args.reason or "(none)",
                     source=args.source,
                     rescore=not args.no_rescore)
            return 0
        if args.remove:
            ticker = args.remove.upper().strip()
            with cx:
                _remove(cx, ticker)
            return 0

        parser.print_help()
        return 1
    finally:
        cx.close()


if __name__ == "__main__":
    raise SystemExit(main())
