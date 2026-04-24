"""Daily driver for the Stock Picker pipeline.

Cadence (Step 1.2 scope):
  * Every day         heartbeat log entry.
  * Every Saturday    ingest 13F-HR filings + recompute TWOS.
  * Staleness guard   if the PC was off the previous Saturday
                      (or longer) and the last ingest is more
                      than INGEST_MAX_AGE_DAYS old, ingest on
                      the first available run regardless of
                      weekday — prevents 14+ day gaps when a
                      Saturday slot is missed.

Filing-deadline windows (SEC 45-day rule) mean new 13F-HR
content only arrives in the ~6 weeks after each quarter end
(Jan, Apr, Jul, Oct). Weekly Saturday ingestion gives a
worst-case 6-day detection lag, which is acceptable for a
quarterly-cadence dataset. Saturday was chosen so the work
runs when markets are closed and no interactive session is
likely to be competing for the DB file.

Invocation assumes cwd = 1_not_used/ and PYTHONPATH=src,
set by the parent run_1_Stock_Picker.bat.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path("logs")
INGEST_WEEKDAY = 5         # Saturday (Mon=0 .. Sun=6)
INGEST_MAX_AGE_DAYS = 7    # Force ingest if last run is older than this


def _configure_logging(today: date) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"{today.isoformat()}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def _last_ingest_date(conn: sqlite3.Connection) -> Optional[date]:
    row = conn.execute(
        "SELECT MAX(created_at) AS ts FROM filings_log"
    ).fetchone()
    if not row or not row["ts"]:
        return None
    try:
        return datetime.fromisoformat(row["ts"]).date()
    except ValueError:
        return None


def _should_ingest(today: date, conn: sqlite3.Connection) -> bool:
    # Extend here once Step 1.3 (Form 4) adds a daily cadence
    # or tighter windows near the 45-day filing deadlines.
    if today.weekday() == INGEST_WEEKDAY:
        return True
    last = _last_ingest_date(conn)
    if last is None:
        # First-ever run or empty filings_log: ingest now.
        return True
    return (today - last).days >= INGEST_MAX_AGE_DAYS


def main() -> int:
    today = date.today()
    _configure_logging(today)
    log = logging.getLogger("stockpicker.daily")

    log.info("=== daily run start %s ===", datetime.now().isoformat())

    from database.db import get_connection
    from layer_minus1.edgar_13f_parser import ingest_all_institutions
    from layer_minus1.twos_calculator import run_quarterly_update

    conn = get_connection()
    try:
        last = _last_ingest_date(conn)
        if not _should_ingest(today, conn):
            age_days = (today - last).days if last else -1
            log.info(
                "heartbeat only; last ingest %s (age %dd), "
                "next Saturday ingest on %s",
                last, age_days, _next_saturday(today),
            )
            return 0

        if today.weekday() != INGEST_WEEKDAY:
            log.info(
                "staleness catch-up: last ingest was %s "
                "(>= %d days ago); ingesting today (%s)",
                last, INGEST_MAX_AGE_DAYS, today.strftime("%A"),
            )

        log.info("ingesting 13F-HR filings from EDGAR")
        summary = ingest_all_institutions(
            conn=conn,
            from_date="2025-01-01",
            to_date=today.isoformat(),
        )
        total_new = sum(
            s["holdings_inserted"] for s in summary.values()
        )
        total_skipped = sum(
            s["filings_skipped"] for s in summary.values()
        )
        log.info(
            "ingest done: %d new holdings, %d filings skipped (dedup)",
            total_new, total_skipped,
        )

        log.info("recomputing TWOS for run_date=%s", today.isoformat())
        run_quarterly_update(run_date=today.isoformat(), conn=conn)

        counts = conn.execute(
            "SELECT processing_tier, COUNT(*) AS n "
            "FROM twos_scores WHERE run_date = ? "
            "GROUP BY processing_tier",
            (today.isoformat(),),
        ).fetchall()
        for row in counts:
            log.info(
                "tier %s: %d tickers",
                row["processing_tier"], row["n"],
            )
    except Exception:
        log.exception("daily run failed")
        return 1
    finally:
        conn.close()

    log.info("=== daily run end ===")
    return 0


def _next_saturday(today: date) -> date:
    from datetime import timedelta
    days_ahead = (INGEST_WEEKDAY - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


if __name__ == "__main__":
    raise SystemExit(main())
