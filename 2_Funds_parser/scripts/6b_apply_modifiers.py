"""Module 6b — standalone CLI for the composite score modifier (D47).

Computes per-ticker modifiers from llm_scores.research_brief_json + the
config in ``config/scoring_modifier.yaml``, writes them back to
``llm_scores`` (3 cols) and ``final_rankings`` (3 cols), then re-renders
the report HTML/XLSX with the new columns + modifier breakdown panel.

Makes NO API calls. Safe to re-run; idempotent.

Usage:
  python scripts/6b_apply_modifiers.py                             # all (run, quarter) pairs in DB
  python scripts/6b_apply_modifiers.py --run-id 4                  # single run
  python scripts/6b_apply_modifiers.py --quarter 2025Q4            # all runs for one quarter
  python scripts/6b_apply_modifiers.py --run-id 4 --no-render      # skip HTML/XLSX rewrite
  python scripts/6b_apply_modifiers.py --dry-run -v                # report-only

Spec: 2_Funds_parser/spec/module_6b_spec.md § Part (a).
Decision: 2_Funds_parser/spec/decisions.md § D47.
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6 import (  # noqa: E402
    init_llm_scores_schema,
    render_final_ranking_html,
    render_final_ranking_xlsx,
)
from module_6b import (  # noqa: E402
    apply_modifiers_to_run,
    collect_ticker_factors,
    load_modifier_config,
    load_selection_json,
    merge_modifier_weights,
    merge_selection,
    selection_json_path,
    write_selection_json,
)

_LOG = logging.getLogger("6b_apply_modifiers")


def _list_runs(conn: sqlite3.Connection, run_id: int | None,
               quarter: str | None) -> list[tuple[int, str]]:
    where = []
    params: list = []
    if run_id is not None:
        where.append("run_id = ?")
        params.append(run_id)
    if quarter is not None:
        where.append("quarter = ?")
        params.append(quarter)
    sql = "SELECT DISTINCT run_id, quarter FROM llm_scores"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY quarter, run_id"
    return [(int(r[0]), str(r[1])) for r in conn.execute(sql, params).fetchall()]


def _build_pack_lookup(packs_db: Path, quarter: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not packs_db.exists():
        return out
    with sqlite3.connect(packs_db) as p:
        for ticker, pack_json in p.execute(
            "SELECT ticker, pack_json FROM context_packs WHERE quarter=?",
            (quarter,),
        ).fetchall():
            try:
                out[ticker] = json.loads(pack_json)
            except Exception:
                pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--quarter", default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="Compute + print summary; do not write back to DB or re-render.")
    parser.add_argument("--no-render", action="store_true",
                        help="Update DB but skip HTML/XLSX re-render.")
    parser.add_argument("--config", default=None,
                        help="Override path to scoring_modifier.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_modifier_config(args.config) if args.config else load_modifier_config()
    now = datetime.now(timezone.utc)

    db_path = PROJECT_ROOT / "llm_scores.db"
    packs_db = PROJECT_ROOT / "context_packs.db"
    outputs_dir = PROJECT_ROOT / "Outputs"
    if not db_path.exists():
        raise SystemExit(f"llm_scores.db not found at {db_path}")

    with sqlite3.connect(db_path) as conn:
        init_llm_scores_schema(conn)
        runs = _list_runs(conn, args.run_id, args.quarter)
        if not runs:
            raise SystemExit("No matching (run_id, quarter) found in llm_scores.")

        print(f"Applying modifiers to {len(runs)} (run, quarter) pair(s).")
        for run_id, quarter in runs:
            print(f"\n  run_id={run_id} quarter={quarter}")
            if args.dry_run:
                # Still compute so the user sees what would happen.
                from module_6b.apply import compute_for_row
                conn.row_factory = sqlite3.Row
                rows = list(conn.execute(
                    "SELECT ticker, research_brief_json FROM llm_scores "
                    "WHERE run_id=? AND quarter=? AND horizon='12mo' "
                    "ORDER BY ticker", (run_id, quarter),
                ).fetchall())
                seen: set[str] = set()
                for r in rows:
                    if r["ticker"] in seen: continue
                    seen.add(r["ticker"])
                    res = compute_for_row(r["research_brief_json"], cfg, now=now)
                    print(f"    {r['ticker']:6s}  modifier={res['modifier']:.4f}  "
                          f"raw={res['raw_product']:.4f}  "
                          f"clipped={res['clipped_to']}")
                continue

            summary = apply_modifiers_to_run(
                conn, run_id=run_id, quarter=quarter, cfg=cfg, now=now,
            )
            for ticker, s in sorted(summary.items()):
                fs = s.get("final_score")
                fa = s.get("final_score_adjusted")
                print(f"    {ticker:6s}  modifier={s['modifier']:.4f}  "
                      f"final_score={fs:.3f}  adjusted={fa:.3f}"
                      if fs is not None and fa is not None else
                      f"    {ticker:6s}  modifier={s['modifier']:.4f}  "
                      f"(no final_score)")

            # D55 — M7-alpha snapshot hook. Re-snapshot whenever modifiers
            # are re-applied so the predictions table reflects the latest
            # score_modifier_json for this run. Idempotent (replaces prior
            # snapshots for the same run_id).
            try:
                from module_7 import snapshot_run as _snapshot_run
                _outcomes_db = PROJECT_ROOT / "data" / "outcomes.db"
                _packs_db = PROJECT_ROOT / "context_packs.db"
                _n = _snapshot_run(
                    conn,
                    run_id=run_id,
                    outcomes_db_path=_outcomes_db,
                    packs_db_path=_packs_db if _packs_db.exists() else None,
                )
                print(f"    M7 snapshot: {_n} predictions")
            except Exception as e:
                print(f"    [WARN] M7 snapshot_run failed (run={run_id}): {e}")

        if args.dry_run or args.no_render:
            return 0

        # Re-render the merged HTML + XLSX per touched quarter using the
        # latest scores. Defer to scripts/6_score.py-style helpers via
        # an inline aggregation here (kept minimal — we only need the
        # rendering, not the dispatch flow).
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_s6_helpers", PROJECT_ROOT / "scripts" / "6_score.py")
        s6 = importlib.util.module_from_spec(spec); spec.loader.exec_module(s6)

        for quarter in sorted({q for _, q in runs}):
            pack_lookup = _build_pack_lookup(packs_db, quarter)
            ranking_rows = s6._build_merged_ranking_rows(
                conn, quarter, run_id=-1, pack_lookup=pack_lookup,
            )
            if not ranking_rows:
                continue
            score_rows = s6._latest_score_rows(conn, quarter, None)
            html_path = outputs_dir / f"final_ranking_{quarter}.html"
            xlsx_path = outputs_dir / f"final_ranking_{quarter}.xlsx"
            json_path = selection_json_path(html_path)
            prior = load_selection_json(json_path)
            new_pool = sorted({(r.get("ticker") or "") for r in ranking_rows
                               if r.get("ticker")})
            sidecar_selection = merge_selection(new_pool=new_pool, prior=prior)
            sidecar_weights = merge_modifier_weights(prior=prior)
            ticker_factors = collect_ticker_factors(
                conn, quarter=quarter, tickers=new_pool, cfg=cfg, now=now,
            )
            write_selection_json(
                json_path, quarter=quarter,
                all_tickers=new_pool,
                selected_tickers=sorted(sidecar_selection),
                modifier_weights=sidecar_weights,
            )
            render_final_ranking_html(
                ranking_rows, html_path,
                quarter=quarter, run_id=max(r for r, _ in runs),
                score_rows=score_rows,
                prior_selection=sidecar_selection,
                modifier_weights=sidecar_weights,
                ticker_factors=ticker_factors,
                modifier_cfg=cfg,
            )
            render_final_ranking_xlsx(
                ranking_rows, xlsx_path,
                quarter=quarter, run_id=max(r for r, _ in runs),
            )
            print(f"\n  rewrote HTML + XLSX for {quarter} "
                  f"({len(ranking_rows)} tickers)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
