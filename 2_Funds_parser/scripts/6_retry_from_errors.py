"""One-shot retry from llm_errors raw_text — re-parses + writes to llm_scores
WITHOUT re-billing Anthropic.

Use case: a parser bug (e.g. m6-v4 null PFW handling) caused valid LLM responses
to land in llm_errors with `error_kind='db_write_error'`. After fixing the
parser, this script re-parses the cached raw_text and writes the rows.

Usage:
  python scripts/6_retry_from_errors.py --run-id 11 --tickers BCYC,NTLA
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6.parsing import parse_full_score  # noqa: E402
from module_6.scoring import compute_ticker_score  # noqa: E402
from module_6.scores_db_writes import write_full_score_rows  # noqa: E402
from module_6b import apply_modifiers_to_run, load_modifier_config  # noqa: E402

LOG = logging.getLogger(__name__)


def _load_pack(packs_conn: sqlite3.Connection, ticker: str, quarter: str) -> dict | None:
    row = packs_conn.execute(
        "SELECT pack_json FROM context_packs WHERE ticker=? AND quarter=?",
        (ticker, quarter),
    ).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def _load_run_meta(scores_conn: sqlite3.Connection, run_id: int) -> dict:
    row = scores_conn.execute(
        "SELECT quarter, prompt_version, model FROM llm_runs WHERE run_id=?",
        (run_id,),
    ).fetchone()
    if not row:
        raise SystemExit(f"run_id={run_id} not found in llm_runs")
    return {"quarter": row[0], "prompt_version": row[1], "model": row[2]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--tickers", required=True,
                   help="Comma-separated tickers")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    scores_db = PROJECT_ROOT / "llm_scores.db"
    packs_db = PROJECT_ROOT / "context_packs.db"

    scores_conn = sqlite3.connect(scores_db, timeout=30)
    scores_conn.row_factory = sqlite3.Row
    packs_conn = sqlite3.connect(packs_db, timeout=30)

    run_meta = _load_run_meta(scores_conn, args.run_id)
    quarter = run_meta["quarter"]
    print(f"  run_id={args.run_id}  quarter={quarter}  prompt_version={run_meta['prompt_version']}")

    written = 0
    for tk in tickers:
        err_row = scores_conn.execute(
            """SELECT error_id, raw_text FROM llm_errors
               WHERE run_id=? AND ticker=? AND raw_text IS NOT NULL
               ORDER BY error_id DESC LIMIT 1""",
            (args.run_id, tk),
        ).fetchone()
        if not err_row:
            print(f"  [{tk}] no llm_errors row with raw_text — skipping")
            continue

        try:
            parsed = parse_full_score(err_row["raw_text"])
        except Exception as e:
            print(f"  [{tk}] re-parse FAILED: {e}")
            continue

        pack = _load_pack(packs_conn, tk, quarter)
        if not pack:
            print(f"  [{tk}] no context pack for {quarter} — skipping")
            continue
        current_price = pack.get("market_snapshot", {}).get("price_today_usd")
        if current_price is None:
            print(f"  [{tk}] pack has no price_today_usd — skipping")
            continue

        score = compute_ticker_score(
            ticker=tk,
            current_price_usd=float(current_price),
            entry_price_ranges=parsed.entry_price_ranges,
            near_term_3mo=parsed.near_term_3mo,
            long_term_12mo=parsed.long_term_12mo,
        )
        pack_hash = pack.get("source_rank_hash") or ""

        # Token counts: unknown post-error. Write zeros — the run-level
        # totals on llm_runs already captured the actual spend.
        write_full_score_rows(
            scores_conn,
            parsed=parsed, score=score,
            quarter=quarter,
            prompt_version=run_meta["prompt_version"],
            model=run_meta["model"],
            run_id=args.run_id,
            raw_text=err_row["raw_text"],
            response_id=f"recovered_from_error_{err_row['error_id']}",
            input_tokens=0, output_tokens=0,
            cache_read_tokens=0, cache_creation_tokens=0,
            web_search_calls=0, usd_cost=0.0,
            pack_source_rank_hash=pack_hash,
            source_tier="C",
            refreshed_from_row_id=None,
        )
        scores_conn.commit()
        print(f"  [{tk}] wrote llm_scores rows (current=${current_price:.2f}, "
              f"3mo target=${parsed.near_term_3mo['target_price_usd']:.2f}, "
              f"12mo target=${parsed.long_term_12mo['target_price_usd']:.2f})")
        written += 1

    if written:
        print(f"\n  Re-applying modifiers + final_rankings for run {args.run_id}...")
        modifier_cfg = load_modifier_config()
        apply_modifiers_to_run(
            scores_conn, run_id=args.run_id, quarter=quarter, cfg=modifier_cfg,
        )
        scores_conn.commit()
        # M7 snapshot — re-fire so the re-parsed tickers join the prediction set
        try:
            from module_7 import snapshot_run as _snap
            n = _snap(scores_conn, run_id=args.run_id,
                      outcomes_db_path=PROJECT_ROOT / "data" / "outcomes.db",
                      packs_db_path=packs_db)
            print(f"  M7 snapshot: {n} predictions for run {args.run_id}")
        except Exception as e:
            print(f"  [WARN] M7 snapshot failed: {e}")

    scores_conn.close()
    packs_conn.close()
    print(f"\n  Recovered {written} ticker(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
