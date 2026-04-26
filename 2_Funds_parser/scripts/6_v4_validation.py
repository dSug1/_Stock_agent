"""m6-v4 validation orchestrator.

Workflow:
  1. Backup current m6-v3 llm_scores + final_rankings rows for the supplied
     tickers into a temp JSON file.
  2. Run M6 dispatch under m6-v4 for those tickers (pass-through to
     `scripts/6_score.py --tickers ... --mode sync`).
  3. Compare m6-v3 backup vs newly-written m6-v4 rows: per-(ticker, horizon)
     diff of target / time / probability / score / modifier / cost / search
     count. Print stdout summary.
  4. Delete the temp file.

Usage:
  python scripts/6_v4_validation.py --tickers NTLA,TCRX,BCYC,GLUE,GRAL,LEGN,ABEO --backup
  python scripts/6_v4_validation.py --tickers ... --dispatch --yes [--sync-concurrency 1]
  python scripts/6_v4_validation.py --tickers ... --compare
  python scripts/6_v4_validation.py --tickers ... --cleanup
  python scripts/6_v4_validation.py --tickers ... --all --yes   # full pipeline

Each phase is its own flag so the user can pause for cost approval between
backup and dispatch.

The temp file lives at:
  _intermediate_outputs/m6_v3_backup_<quarter>.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_1 import load_config  # noqa: E402

LOG = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _backup_path(quarter: str) -> Path:
    return PROJECT_ROOT / "_intermediate_outputs" / f"m6_v3_backup_{quarter}.json"


def _connect_scores() -> sqlite3.Connection:
    conn = sqlite3.connect(PROJECT_ROOT / "llm_scores.db")
    conn.row_factory = sqlite3.Row
    return conn


def _read_rows(
    conn: sqlite3.Connection, tickers: list[str], prompt_version: str,
) -> dict[str, list[dict]]:
    """Returns {table: [row_dict, ...]} for llm_scores + final_rankings + llm_runs."""
    placeholders = ",".join("?" * len(tickers))
    out: dict[str, list[dict]] = {}

    out["llm_scores"] = [
        dict(r) for r in conn.execute(
            f"SELECT * FROM llm_scores WHERE ticker IN ({placeholders}) "
            f"AND prompt_version = ? ORDER BY ticker, horizon",
            list(tickers) + [prompt_version],
        ).fetchall()
    ]

    out["final_rankings"] = [
        dict(r) for r in conn.execute(
            f"SELECT * FROM final_rankings WHERE ticker IN ({placeholders}) "
            f"ORDER BY ticker, run_id DESC",
            tickers,
        ).fetchall()
    ]
    # Filter final_rankings to runs that match the prompt_version
    relevant_run_ids = {r["run_id"] for r in out["llm_scores"]}
    out["final_rankings"] = [
        r for r in out["final_rankings"] if r["run_id"] in relevant_run_ids
    ]

    out["llm_runs"] = [
        dict(r) for r in conn.execute(
            "SELECT * FROM llm_runs WHERE prompt_version = ? "
            "AND run_id IN (SELECT DISTINCT run_id FROM llm_scores WHERE prompt_version = ? "
            f"AND ticker IN ({placeholders}))",
            [prompt_version, prompt_version] + list(tickers),
        ).fetchall()
    ]

    return out


# ─── Phase 1: backup ─────────────────────────────────────────────────────────

def do_backup(tickers: list[str], quarter: str) -> int:
    conn = _connect_scores()
    snap = _read_rows(conn, tickers, prompt_version="m6-v3")

    if not snap["llm_scores"]:
        print(f"[WARN] No m6-v3 llm_scores rows found for tickers {tickers}. "
              f"Nothing to back up.")
        return 0

    backup = _backup_path(quarter)
    backup.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "prompt_version": "m6-v3",
        "tickers": tickers,
        "n_llm_scores_rows": len(snap["llm_scores"]),
        "n_final_rankings_rows": len(snap["final_rankings"]),
        "n_llm_runs_rows": len(snap["llm_runs"]),
        "data": snap,
    }
    backup.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(f"  Backup written: {backup}")
    print(f"  Rows: llm_scores={len(snap['llm_scores'])}  "
          f"final_rankings={len(snap['final_rankings'])}  "
          f"llm_runs={len(snap['llm_runs'])}")
    return 0


# ─── Phase 2: dispatch ───────────────────────────────────────────────────────

def do_dispatch(tickers: list[str], yes: bool, sync_concurrency: int) -> int:
    cmd = [
        str(PROJECT_ROOT / ".." / ".venv" / "Scripts" / "python.exe"),
        str(PROJECT_ROOT / "scripts" / "6_score.py"),
        "--tickers", ",".join(tickers),
        "--mode", "sync",
        "--sync-concurrency", str(sync_concurrency),
        "-v",
    ]
    if yes:
        cmd.append("--yes")
    print(f"  Dispatch command: {' '.join(cmd)}")
    env = {
        **__import__("os").environ,
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
    }
    rc = subprocess.call(cmd, env=env, cwd=str(PROJECT_ROOT))
    return rc


# ─── Phase 3: compare ────────────────────────────────────────────────────────

_SCORE_FIELDS_PER_HORIZON = [
    "target_price_usd", "time_to_catalyst_weeks", "probability",
    "current_price_at_scoring_usd", "score_at_current_pct_per_month",
    "score_modifier", "score_at_current_adjusted_pct_per_month",
    "fully_diluted_shares_count", "cash_and_equivalents_usd",
    "runway_months", "rnpv_per_share_usd",
    "web_search_calls", "usd_cost",
]


def _index_by_horizon(rows: list[dict]) -> dict[tuple[str, str], dict]:
    return {(r["ticker"], r["horizon"]): r for r in rows}


def _fmt(v) -> str:
    if v is None:
        return "  null"
    if isinstance(v, float):
        return f"{v:>8.3f}"
    if isinstance(v, int):
        return f"{v:>8d}"
    return f"{str(v):>8s}"


def do_compare(tickers: list[str], quarter: str) -> int:
    backup = _backup_path(quarter)
    if not backup.exists():
        print(f"[ERROR] No backup found at {backup}. Run --backup first.")
        return 2

    payload = json.loads(backup.read_text(encoding="utf-8"))
    v3_rows = payload["data"]["llm_scores"]
    v3_runs = payload["data"]["llm_runs"]
    v3_idx = _index_by_horizon(v3_rows)

    conn = _connect_scores()
    v4_data = _read_rows(conn, tickers, prompt_version="m6-v4")
    v4_rows = v4_data["llm_scores"]
    v4_runs = v4_data["llm_runs"]
    v4_idx = _index_by_horizon(v4_rows)

    print()
    print("=" * 100)
    print(f"  m6-v3 -> m6-v4 comparison ({len(tickers)} tickers, both horizons)")
    print("=" * 100)

    # Run-level summary
    def _sum_field(rows, key):
        return sum((r.get(key) or 0) for r in rows)

    v3_cost = _sum_field(v3_runs, "usd_cost_total")
    v4_cost = _sum_field(v4_runs, "usd_cost_total")
    v3_search = _sum_field(v3_rows, "web_search_calls")
    v4_search = _sum_field(v4_rows, "web_search_calls")
    v3_input = _sum_field(v3_runs, "input_tokens_total")
    v4_input = _sum_field(v4_runs, "input_tokens_total")
    v3_output = _sum_field(v3_runs, "output_tokens_total")
    v4_output = _sum_field(v4_runs, "output_tokens_total")

    print()
    print(f"  RUN-LEVEL TOTALS (sum across all runs that produced these scores)")
    print(f"  {'metric':32s} {'m6-v3':>14s} {'m6-v4':>14s} {'delta':>14s}")
    for label, v3, v4 in [
        ("usd_cost_total ($)", v3_cost, v4_cost),
        ("input_tokens_total", v3_input, v4_input),
        ("output_tokens_total", v3_output, v4_output),
        ("web_search_calls (sum/score row)", v3_search, v4_search),
    ]:
        delta = v4 - v3
        delta_pct = (delta / v3 * 100) if v3 else float("nan")
        print(f"  {label:32s} {v3:>14.4g} {v4:>14.4g} "
              f"{delta:>14.4g}  ({delta_pct:+6.1f}%)")

    # Per (ticker, horizon) diff
    print()
    print("  PER-TICKER × HORIZON DIFF (m6-v4 vs m6-v3)")
    keys = sorted(set(v3_idx) | set(v4_idx))
    for tk, hz in keys:
        r3 = v3_idx.get((tk, hz))
        r4 = v4_idx.get((tk, hz))
        if not r3:
            print(f"\n  {tk:6s} {hz:5s}  [m6-v3 missing — new ticker?]")
            continue
        if not r4:
            print(f"\n  {tk:6s} {hz:5s}  [m6-v4 missing — dispatch failed?]")
            continue
        print(f"\n  {tk:6s} {hz:5s}")
        print(f"  {'field':40s} {'m6-v3':>10s} {'m6-v4':>10s} {'delta':>10s}")
        for f in _SCORE_FIELDS_PER_HORIZON:
            v3 = r3.get(f)
            v4 = r4.get(f)
            if v3 is None and v4 is None:
                continue
            delta_str = ""
            if isinstance(v3, (int, float)) and isinstance(v4, (int, float)):
                d = v4 - v3
                if v3:
                    pct = d / v3 * 100
                    delta_str = f"{d:>+8.3f}  ({pct:+5.1f}%)"
                else:
                    delta_str = f"{d:>+8.3f}"
            print(f"  {f:40s} {_fmt(v3):>10s} {_fmt(v4):>10s}  {delta_str}")

    # Modifier-component breakdown
    print()
    print("  SCORE_MODIFIER BREAKDOWN (12mo only — m6-v3 vs m6-v4)")
    print(f"  {'ticker':8s} {'component':24s} {'m6-v3':>8s} {'m6-v4':>8s}")
    for tk in sorted(tickers):
        r3 = v3_idx.get((tk, "12mo"))
        r4 = v4_idx.get((tk, "12mo"))
        if not r3 or not r4:
            continue
        try:
            j3 = json.loads(r3.get("score_modifier_json") or "{}")
            j4 = json.loads(r4.get("score_modifier_json") or "{}")
        except Exception:
            continue
        comps3 = j3.get("components", {}) or {}
        comps4 = j4.get("components", {}) or {}
        all_comp_keys = sorted(set(comps3) | set(comps4))
        for ck in all_comp_keys:
            f3 = (comps3.get(ck) or {}).get("factor")
            f4 = (comps4.get(ck) or {}).get("factor")
            if f3 == f4:
                continue
            print(f"  {tk:8s} {ck:24s} {_fmt(f3):>8s} {_fmt(f4):>8s}")

    print()
    print("=" * 100)
    return 0


# ─── Phase 4: cleanup ────────────────────────────────────────────────────────

def do_cleanup(quarter: str) -> int:
    backup = _backup_path(quarter)
    if backup.exists():
        backup.unlink()
        print(f"  Deleted: {backup}")
    else:
        print(f"  Nothing to delete (no file at {backup})")
    return 0


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", required=True,
                   help="Comma-separated ticker list")
    p.add_argument("--quarter", default=None,
                   help="Quarter YYYYQn (default: pipeline.yaml -> auto-latest)")

    p.add_argument("--backup",   action="store_true", help="Phase 1 only")
    p.add_argument("--dispatch", action="store_true", help="Phase 2 only")
    p.add_argument("--compare",  action="store_true", help="Phase 3 only")
    p.add_argument("--cleanup",  action="store_true", help="Phase 4 only")
    p.add_argument("--all",      action="store_true",
                   help="Run all four phases sequentially (backup → dispatch → compare → cleanup)")

    p.add_argument("--yes", action="store_true",
                   help="Pass --yes to 6_score.py (bypass mandatory [y/N] gate)")
    p.add_argument("--sync-concurrency", type=int, default=1,
                   help="6_score.py --sync-concurrency value (default 1 — safest for rate limits)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        raise SystemExit("--tickers must not be empty")

    config = load_config()
    quarter = args.quarter or config.quarter
    print(f"  Tickers: {tickers}")
    print(f"  Quarter: {quarter}")
    print()

    do_all = args.all
    rc = 0

    if do_all or args.backup:
        print("--- Phase 1: backup m6-v3 rows ---")
        rc = do_backup(tickers, quarter)
        if rc:
            return rc

    if do_all or args.dispatch:
        print()
        print("--- Phase 2: dispatch under m6-v4 ---")
        rc = do_dispatch(tickers, yes=args.yes, sync_concurrency=args.sync_concurrency)
        if rc:
            print(f"[ERROR] dispatch returned {rc}; skipping compare/cleanup")
            return rc

    if do_all or args.compare:
        print()
        print("--- Phase 3: compare m6-v3 backup vs m6-v4 fresh ---")
        rc = do_compare(tickers, quarter)
        if rc:
            return rc

    if do_all or args.cleanup:
        print()
        print("--- Phase 4: delete temp backup ---")
        rc = do_cleanup(quarter)

    return rc


if __name__ == "__main__":
    sys.exit(main())
