"""One-shot D24 migration: backfill drug_signature + bump prompt_version
on legacy deep_dives rows so they cache-hit on the next M7 run.

When D23 was introduced, the `drug_signature` column was added but existing
rows kept it NULL — making them cache-misses by definition. Combined with
the post-D22/D23 config edits that changed the YAML SHA-7, the existing
10 rows would all re-dispatch unnecessarily.

This script:

  1. For every successful deep_dives row (p_clinical IS NOT NULL):
     a. Query biotech.db for the FULL list of catalyst rows that share
        this row's (snapshot_date, ticker, drug) — these define the
        drug-group's catalyst set.
     b. Compute drug_signature over (drug, stage, sorted catalyst tuples).
     c. UPDATE deep_dives SET drug_signature, prompt_version.

  2. Skip rows already populated (idempotent).

  3. Print summary; nothing destructive (no DELETEs).

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_backfill_cache_fields.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_backfill_cache_fields.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import DEFAULT_DB_PATH as BIOTECH_DB                       # noqa: E402
from module_6_5.fundamentals_db import DEFAULT_DB_PATH as FUNDAMENTALS_DB  # noqa: E402
from module_7.cache import compute_drug_signature                          # noqa: E402
from module_7.config import default_config_path, load_module_7_config       # noqa: E402
from module_7.deep_dives_db import DEFAULT_DB_PATH as DD_DB, init_deep_dives_db  # noqa: E402


def _fundamentals_price(fundamentals_db: Path, ticker: str):
    """Best-effort lookup of last_price_usd from fundamentals.db (M6.5
    snapshot). Returns None if unavailable."""
    if not fundamentals_db.exists():
        return None
    try:
        with sqlite3.connect(fundamentals_db) as cx:
            cx.row_factory = sqlite3.Row
            row = cx.execute(
                """
                SELECT last_price_usd FROM financials
                WHERE ticker = ?
                ORDER BY period_end_date DESC LIMIT 1
                """, (ticker,)).fetchone()
        return row["last_price_usd"] if row else None
    except Exception:
        return None


def _siblings_for(biotech_db: Path, snapshot_date: str, ticker: str, drug: str):
    """Return list of (next_catalyst_type, catalyst_date_iso) for every
    catalyst sharing (snapshot_date, ticker, drug) in biotech.db, plus the
    drug's stage (taken from the first matching row)."""
    con = sqlite3.connect(biotech_db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            SELECT s.next_catalyst_type,
                   s.stage,
                   COALESCE(t.date_min, t.date_max, s.catalyst_date) AS catalyst_date_iso
            FROM catalyst_snapshots s
            LEFT JOIN catalyst_timing t USING
                (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
            WHERE s.snapshot_date = ? AND s.ticker = ? AND s.drug = ?
            """,
            (snapshot_date, ticker, drug),
        ).fetchall()
    finally:
        con.close()
    catalysts = [(r["next_catalyst_type"], r["catalyst_date_iso"]) for r in rows]
    stage = rows[0]["stage"] if rows else None
    return stage, catalysts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB)
    parser.add_argument("--dd-db", type=Path, default=DD_DB)
    parser.add_argument("--fundamentals-db", type=Path, default=FUNDAMENTALS_DB,
                        help="Source for fallback price_at_api_time_usd")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change; don't write")
    args = parser.parse_args()

    cfg = load_module_7_config(default_config_path())
    current_pv = cfg.prompt_version
    print(f"current prompt_version: {current_pv}")
    print(f"biotech.db: {args.biotech_db}")
    print(f"deep_dives.db: {args.dd_db}")
    print(f"dry-run: {args.dry_run}")
    print()

    init_deep_dives_db(args.dd_db)
    cx = sqlite3.connect(args.dd_db)
    cx.row_factory = sqlite3.Row
    try:
        rows = cx.execute(
            """
            SELECT snapshot_date, ticker, drug, nct_number, next_catalyst_type,
                   run_id, drug_signature, prompt_version, p_clinical,
                   expected_move_on_hit_pct, expected_move_on_miss_pct,
                   price_at_api_time_usd,
                   target_price_on_hit_usd, target_price_on_miss_usd,
                   e_move_pct, expectancy_per_week_pct,
                   weeks_to_catalyst_mid
            FROM deep_dives
            WHERE p_clinical IS NOT NULL
            ORDER BY run_id, ticker
            """
        ).fetchall()

        n_total = len(rows)
        n_updated = 0
        n_skipped_current = 0

        for r in rows:
            stage, sibling_catalysts = _siblings_for(
                args.biotech_db, r["snapshot_date"], r["ticker"], r["drug"],
            )
            new_sig = compute_drug_signature(
                drug=r["drug"], stage=stage, catalysts=sibling_catalysts,
            )

            # D25 — backfill price anchor + $ targets if missing.
            new_price = r["price_at_api_time_usd"]
            new_tgt_h = r["target_price_on_hit_usd"]
            new_tgt_m = r["target_price_on_miss_usd"]
            if new_price is None:
                new_price = _fundamentals_price(args.fundamentals_db, r["ticker"])
            if new_price is not None and new_tgt_h is None and r["expected_move_on_hit_pct"] is not None:
                new_tgt_h = float(new_price) * (1.0 + float(r["expected_move_on_hit_pct"]) / 100.0)
            if new_price is not None and new_tgt_m is None and r["expected_move_on_miss_pct"] is not None:
                new_tgt_m = float(new_price) * (1.0 + float(r["expected_move_on_miss_pct"]) / 100.0)

            # D26 → D33: expectancy_per_week_pct = e_move_pct / max(1, weeks).
            # m_momentum / momentum_score_input / expectancy_pct columns
            # were dropped in D33 so they're no longer written here.
            new_expw = None
            if r["e_move_pct"] is not None:
                weeks_d = max(1, r["weeks_to_catalyst_mid"] or 1)
                new_expw = float(r["e_move_pct"]) / weeks_d
            need_expw = (new_expw is not None
                         and abs((r["expectancy_per_week_pct"] or 0) - new_expw) > 1e-6)

            need_sig    = (r["drug_signature"] != new_sig)
            need_pv     = (r["prompt_version"] != current_pv)
            need_price  = (r["price_at_api_time_usd"] is None and new_price is not None)
            need_tgt_h  = (r["target_price_on_hit_usd"]  is None and new_tgt_h is not None)
            need_tgt_m  = (r["target_price_on_miss_usd"] is None and new_tgt_m is not None)
            if not (need_sig or need_pv or need_price or need_tgt_h or need_tgt_m
                    or need_expw):
                n_skipped_current += 1
                continue

            print(f"  {r['ticker']:6s} run={r['run_id']} drug={r['drug'][:40]:40s}")
            if need_sig:
                old = (r["drug_signature"] or "NULL")[:50]
                print(f"    drug_signature: {old} -> {new_sig[:80]}")
            if need_pv:
                print(f"    prompt_version: {r['prompt_version']} -> {current_pv}")
            if need_price:
                print(f"    price_at_api_time_usd: NULL -> ${new_price:.2f}")
            if need_tgt_h:
                print(f"    target_price_on_hit_usd: NULL -> ${new_tgt_h:.2f}")
            if need_tgt_m:
                print(f"    target_price_on_miss_usd: NULL -> ${new_tgt_m:.2f}")
            if need_expw:
                print(f"    expectancy_per_week_pct: {(r['expectancy_per_week_pct'] or 0):+.2f} -> {new_expw:+.2f} (D26/D33)")

            if not args.dry_run:
                cx.execute(
                    """
                    UPDATE deep_dives
                    SET drug_signature = ?, prompt_version = ?,
                        price_at_api_time_usd = COALESCE(price_at_api_time_usd, ?),
                        target_price_on_hit_usd = COALESCE(target_price_on_hit_usd, ?),
                        target_price_on_miss_usd = COALESCE(target_price_on_miss_usd, ?),
                        expectancy_per_week_pct = ?
                    WHERE snapshot_date = ? AND ticker = ? AND drug = ?
                      AND nct_number = ? AND next_catalyst_type = ? AND run_id = ?
                    """,
                    (new_sig, current_pv, new_price, new_tgt_h, new_tgt_m,
                     new_expw if new_expw is not None else r["expectancy_per_week_pct"],
                     r["snapshot_date"], r["ticker"], r["drug"],
                     r["nct_number"], r["next_catalyst_type"], r["run_id"]),
                )
                n_updated += 1

        if not args.dry_run:
            cx.commit()

        print()
        print("-" * 60)
        print(f"  Total successful rows scanned: {n_total}")
        print(f"  Already current (no change):   {n_skipped_current}")
        print(f"  Updated:                       {n_updated}")
        if args.dry_run:
            print("  (DRY-RUN — no writes performed)")
        print("-" * 60)
    finally:
        cx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
