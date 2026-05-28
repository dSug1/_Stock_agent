"""Check + (conditionally) run 2_Funds_parser refresh based on the 13F
filing calendar.

Invoked at the top of ``run_3_Biopharmcatalyst_parser.bat``. Exits 0 on
"nothing to do" so the calling .bat keeps moving. Exits non-zero on a
fatal refresh error so the user notices.

Triggers (per ``funds_refresh.decision.decide``):
  1. Today within ±7 days of any 13F filing deadline (2/14, 5/15, 8/14, 11/14).
  2. Today past the most recent deadline AND 2_Funds_parser DB does not
     yet hold the matching quarter.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from funds_refresh.decision import decide  # noqa: E402
from funds_refresh.runner import latest_period_in_db, run_refresh  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--today", type=date.fromisoformat, default=None,
        help="override the current date (for testing). Default: today UTC.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report the decision but do not invoke 2_Funds_parser.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="ignore the calendar window and run anyway (target = most recent "
             "completed quarter as of today).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    log = logging.getLogger("auto_refresh_funds")

    today = args.today or date.today()
    latest_iso = latest_period_in_db()
    latest = date.fromisoformat(latest_iso) if latest_iso else None
    log.info("today=%s, 2_Funds_parser latest period=%s", today, latest)

    d = decide(today=today, latest_period_in_db=latest)
    log.info("decision: should_run=%s — %s", d.should_run, d.reason)

    if args.force and not d.should_run:
        log.warning("decision said skip but --force is set; will run anyway")
        # Synthesise a target if decide() didn't produce one (extremely early date).
        if d.target_quarter_end is None:
            log.error("--force passed but no target quarter could be derived; abort")
            return 2
    elif not d.should_run:
        print(f"[auto-refresh-funds] skip: {d.reason}")
        return 0

    print(f"[auto-refresh-funds] trigger: {d.reason}")
    if args.dry_run:
        print("[auto-refresh-funds] dry-run; not invoking 2_Funds_parser.")
        return 0

    target = d.target_quarter_end.isoformat()
    prev = d.previous_quarter_end.isoformat() if d.previous_quarter_end else None
    print(f"[auto-refresh-funds] running 2_Funds_parser M2..M5 for target quarter "
          f"{target} (prev {prev}) …")

    summary = run_refresh(target_quarter_end=target, previous_quarter_end=prev or target)

    # Pretty-print step outcomes
    for s in summary.steps:
        status = "OK" if not s.fatal else f"FAIL exit={s.returncode}"
        print(f"  [{status:14s}] {s.label:30s} ({s.script})")
        if s.fatal:
            print("    --- stderr tail ---")
            for line in s.stderr_tail.splitlines():
                print(f"    {line}")
            print("    --- stdout tail ---")
            for line in s.stdout_tail.splitlines():
                print(f"    {line}")

    if summary.aborted_at:
        print(f"[auto-refresh-funds] ABORTED at step: {summary.aborted_at}")
        return 1

    if summary.consensus_html_path:
        print(f"[auto-refresh-funds] consensus HTML: "
              f"{summary.consensus_html_path}")

    print("[auto-refresh-funds] complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
