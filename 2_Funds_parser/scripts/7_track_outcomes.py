"""Module 7 — outcome tracking CLI.

Phase α (this build): forward-price collection + (optional) backfill of
prediction snapshots from `llm_scores.db`. NO outcome classification yet
(M7-β) and NO calibration reports yet (M7-β/γ).

Usage:
  # Fill forward prices for everything that's elapsed:
  python scripts/7_track_outcomes.py
  python scripts/7_track_outcomes.py --asof 2026-04-26 -v
  python scripts/7_track_outcomes.py --quarter 2025Q4

  # Backfill snapshots for historical runs in llm_scores.db:
  python scripts/7_track_outcomes.py --backfill-runs all -v
  python scripts/7_track_outcomes.py --backfill-runs 2,4,7,8,9 -v

  # Combine: backfill first, then collect:
  python scripts/7_track_outcomes.py --backfill-runs all --collect -v
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import yaml  # noqa: E402

from module_1 import load_config  # noqa: E402
from module_7 import (  # noqa: E402
    collect_forward_prices,
    snapshot_run,
)


DEFAULT_WINDOWS_WEEKS = (1, 4, 12, 26, 52)


def _load_module_7_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def _parse_runs_arg(arg: str, scores_db: Path) -> list[int]:
    """Parse the --backfill-runs argument.
    'all' → every run_id present in llm_runs.
    'N,M,K' → explicit ids.
    """
    if arg == "all":
        with sqlite3.connect(scores_db) as conn:
            return [int(r[0]) for r in conn.execute(
                "SELECT run_id FROM llm_runs ORDER BY run_id"
            ).fetchall()]
    out: list[int] = []
    for tok in arg.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            out.append(int(tok))
        except ValueError:
            raise SystemExit(f"--backfill-runs: bad token {tok!r}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--asof", default=None,
                        help="Treat this ISO date as 'today' for window-elapsed checks. "
                             "Default: today UTC.")
    parser.add_argument("--quarter", default=None,
                        help="Restrict forward-price collection to predictions in this quarter")
    parser.add_argument("--backfill-runs", default=None,
                        help="'all' or 'N,M,...': snapshot existing runs from llm_scores.db")
    parser.add_argument("--collect", action="store_true",
                        help="Run forward-price collection. Default if --backfill-runs is unset.")
    parser.add_argument("--no-collect", action="store_true",
                        help="Skip forward-price collection (use with --backfill-runs).")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = load_config()
    m7cfg_path = PROJECT_ROOT / "config" / "module_7.yaml"
    m7cfg = _load_module_7_config(m7cfg_path)

    outcomes_db = PROJECT_ROOT / str(m7cfg.get("db_path", "data/outcomes.db"))
    prices_db = config.paths.prices_db
    scores_db = PROJECT_ROOT / "llm_scores.db"
    packs_db = PROJECT_ROOT / "context_packs.db"

    windows_weeks = tuple(int(w) for w in (m7cfg.get("forward_windows_weeks") or DEFAULT_WINDOWS_WEEKS))
    max_forward_days = int(m7cfg.get("price_lookup_max_forward_days") or 5)
    delist_grace_days = int(m7cfg.get("delist_grace_days") or 90)

    asof_date = None
    if args.asof:
        try:
            asof_date = dt.date.fromisoformat(args.asof)
        except ValueError:
            raise SystemExit(f"--asof: bad ISO date {args.asof!r}")

    do_collect = (not args.backfill_runs) or args.collect
    if args.no_collect:
        do_collect = False

    # ---- Backfill snapshots ----
    snapshots_written: list[tuple[int, int]] = []
    if args.backfill_runs:
        if not scores_db.exists():
            raise SystemExit(f"llm_scores.db missing at {scores_db}")
        run_ids = _parse_runs_arg(args.backfill_runs, scores_db)
        with sqlite3.connect(scores_db) as sconn:
            sconn.row_factory = sqlite3.Row
            for run_id in run_ids:
                if args.dry_run:
                    print(f"  DRY-RUN: would snapshot run {run_id}")
                    continue
                n = snapshot_run(
                    sconn,
                    run_id=run_id,
                    outcomes_db_path=outcomes_db,
                    packs_db_path=packs_db if packs_db.exists() else None,
                )
                snapshots_written.append((run_id, n))
                print(f"  snapshot run_id={run_id}: {n} rows")

    # ---- Forward-price collection ----
    collect_result = None
    if do_collect:
        collect_result = collect_forward_prices(
            outcomes_db,
            prices_db,
            asof_date=asof_date,
            windows_weeks=windows_weeks,
            max_forward_days=max_forward_days,
            delist_grace_days=delist_grace_days,
            quarter_filter=args.quarter,
            dry_run=args.dry_run,
        )

    print()
    print("Module 7 (M7-alpha)" + (" DRY-RUN" if args.dry_run else "") + " complete:")
    if snapshots_written:
        total = sum(n for _, n in snapshots_written)
        print(f"  Snapshots backfilled:  {len(snapshots_written)} runs / {total} rows")
    if collect_result:
        cr = collect_result
        print(f"  Forward-price collection (asof={cr.asof_date}):")
        print(f"    predictions seen:     {cr.n_predictions}")
        print(f"    cells eligible:       {cr.cells_eligible}")
        print(f"    cells filled:         {cr.cells_filled}")
        print(f"    cells already filled: {cr.cells_already_present}")
        print(f"    cells not yet:        {cr.cells_not_yet}")
        print(f"    cells missing prices: {cr.cells_missing_in_prices}")
        print(f"    cells delisted:       {cr.cells_delisted}")
    print(f"  outcomes.db: {outcomes_db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
