"""Offline recompute of D46 current-price-anchored scores.

Walks every llm_scores row in llm_scores.db, re-sources the current price
from the M5 pack at scoring time, recomputes the three new fields:
    current_price_at_scoring_usd
    appreciation_from_current_pct
    score_at_current_pct_per_month
…and rewrites final_rankings rows with the updated D46 sort key + columns.

Makes NO API calls. Safe to re-run; idempotent.

Usage:
    python scripts/6_recompute_scores.py                       # all runs
    python scripts/6_recompute_scores.py --run-id 4            # single run
    python scripts/6_recompute_scores.py --quarter 2025Q4
    python scripts/6_recompute_scores.py --dry-run             # preview changes only
    python scripts/6_recompute_scores.py -v
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

from module_6 import (  # noqa: E402
    compute_horizon_score,
    init_llm_scores_schema,
    render_final_ranking_html,
    render_final_ranking_xlsx,
    write_final_rankings,
)

_LOG = logging.getLogger("6_recompute_scores")


def _build_pack_lookup(packs_db: Path, quarter: str) -> dict[str, dict]:
    """Load the M5 pack JSON for every ticker in this quarter."""
    out: dict[str, dict] = {}
    with sqlite3.connect(packs_db) as p:
        rows = p.execute(
            "SELECT ticker, pack_json FROM context_packs WHERE quarter=?", (quarter,)
        ).fetchall()
    for ticker, pack_json in rows:
        try:
            out[ticker] = json.loads(pack_json)
        except Exception as e:
            _LOG.warning("pack JSON parse failed for %s: %s", ticker, e)
    return out


def _recompute_one_row(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    pack: dict | None,
    dry_run: bool,
) -> tuple[str, float | None, float | None, float | None]:
    """Recompute the three D46 fields for one llm_scores row.

    Returns (status, current_price_usd, appreciation_from_current_pct,
    score_at_current_pct_per_month).
    """
    ticker = row["ticker"]
    horizon = row["horizon"]

    # Source current_price from the M5 pack (preferred) or fall back to existing column.
    current_price = None
    if pack:
        current_price = (pack.get("market_snapshot") or {}).get("last_close_usd")
    if current_price is None:
        current_price = row["current_price_at_scoring_usd"]
    if current_price is None or float(current_price) <= 0:
        return ("skip-no-current-price", None, None, None)

    fl = row["fair_entry_low_usd"]
    fh = row["fair_entry_high_usd"]
    rl = row["full_reward_low_usd"]
    rh = row["full_reward_high_usd"]
    target = row["target_price_usd"]
    weeks = row["time_to_catalyst_weeks"]
    prob = row["probability"]
    if any(v is None for v in (fl, fh, rl, rh, target, weeks, prob)):
        return ("skip-incomplete-row", None, None, None)

    fair_mid = (float(fl) + float(fh)) / 2.0
    full_mid = (float(rl) + float(rh)) / 2.0
    hs = compute_horizon_score(
        horizon=horizon,
        target_price_usd=float(target),
        time_to_catalyst_weeks=int(weeks),
        probability=float(prob),
        current_price_usd=float(current_price),
        fair_mid=fair_mid,
        full_mid=full_mid,
    )

    if dry_run:
        return ("dry-run", float(current_price),
                hs.appreciation_from_current_pct,
                hs.score_at_current_pct_per_month)

    conn.execute(
        """
        UPDATE llm_scores
           SET current_price_at_scoring_usd     = ?,
               appreciation_from_current_pct    = ?,
               score_at_current_pct_per_month   = ?
         WHERE ticker = ? AND quarter = ? AND horizon = ?
           AND prompt_version = ? AND model = ?
        """,
        (float(current_price),
         hs.appreciation_from_current_pct,
         hs.score_at_current_pct_per_month,
         ticker, row["quarter"], horizon,
         row["prompt_version"], row["model"]),
    )
    return ("updated", float(current_price),
            hs.appreciation_from_current_pct,
            hs.score_at_current_pct_per_month)


def _build_final_rankings_rows(
    conn: sqlite3.Connection,
    quarter: str,
    run_id: int,
    pack_lookup: dict[str, dict],
) -> list[dict]:
    """Mirror of scripts/6_score.py::_build_final_rankings_rows but for the
    recompute path. Sorts by score_at_current per D46.
    """
    conn.row_factory = sqlite3.Row
    by_ticker: dict[str, dict] = {}
    rows = conn.execute(
        "SELECT * FROM llm_scores WHERE quarter=? AND run_id=? ORDER BY ticker, horizon",
        (quarter, run_id),
    ).fetchall()
    for r in rows:
        t = r["ticker"]
        by_ticker.setdefault(t, {"ticker": t, "quarter": quarter, "run_id": run_id})
        d = by_ticker[t]
        d["fair_entry_low_usd"] = r["fair_entry_low_usd"]
        d["fair_entry_high_usd"] = r["fair_entry_high_usd"]
        d["full_reward_low_usd"] = r["full_reward_low_usd"]
        d["full_reward_high_usd"] = r["full_reward_high_usd"]
        d["fully_diluted_shares_count"] = r["fully_diluted_shares_count"]
        d["rnpv_per_share_usd"] = r["rnpv_per_share_usd"]
        d["moat_score"] = r["moat_score"]
        if "current_price_at_scoring_usd" not in d:
            d["current_price_at_scoring_usd"] = r["current_price_at_scoring_usd"]
        if "fda_pos_adjusted_lead" not in d:
            try:
                rb = json.loads(r["research_brief_json"] or "{}")
                lead_name = ((rb.get("fda") or {}).get("lead_indication") or "").lower()
                indications = rb.get("rnpv_by_indication", []) or []
                pos = None
                for ind in indications:
                    if (ind.get("indication") or "").lower() == lead_name:
                        pos = ind.get("pos_adjusted"); break
                if pos is None and lead_name:
                    for ind in indications:
                        ind_lc = (ind.get("indication") or "").lower()
                        if ind_lc and (ind_lc in lead_name or lead_name in ind_lc):
                            pos = ind.get("pos_adjusted"); break
                if pos is None and indications:
                    top = max(indications,
                              key=lambda i: float(i.get("rnpv_contribution_usd") or 0))
                    pos = top.get("pos_adjusted")
                d["fda_pos_adjusted_lead"] = pos
            except Exception:
                d["fda_pos_adjusted_lead"] = None

        if r["horizon"] == "3mo":
            d["score_at_current_3mo"] = r["score_at_current_pct_per_month"]
            d["score_at_fair_3mo"] = r["score_at_fair_pct_per_month"]
            d["score_at_full_reward_3mo"] = r["score_at_full_reward_pct_per_month"]
            d["target_price_3mo_usd"] = r["target_price_usd"]
            d["appreciation_from_current_3mo_pct"] = r["appreciation_from_current_pct"]
            d["appreciation_from_fair_3mo_pct"] = r["appreciation_from_fair_pct"]
            d["time_to_catalyst_3mo_weeks"] = r["time_to_catalyst_weeks"]
            d["probability_3mo"] = r["probability"]
        elif r["horizon"] == "12mo":
            d["score_at_current_12mo"] = r["score_at_current_pct_per_month"]
            d["score_at_fair_12mo"] = r["score_at_fair_pct_per_month"]
            d["score_at_full_reward_12mo"] = r["score_at_full_reward_pct_per_month"]
            d["target_price_12mo_usd"] = r["target_price_usd"]
            d["appreciation_from_current_12mo_pct"] = r["appreciation_from_current_pct"]
            d["appreciation_from_fair_12mo_pct"] = r["appreciation_from_fair_pct"]
            d["time_to_catalyst_12mo_weeks"] = r["time_to_catalyst_weeks"]
            d["probability_12mo"] = r["probability"]

    out = []
    for t, d in by_ticker.items():
        s3 = d.get("score_at_current_3mo") or 0.0
        s12 = d.get("score_at_current_12mo") or 0.0
        denom = max(abs(s3), abs(s12), 1e-9)
        if abs(s3 - s12) / denom < 0.05:
            d["final_horizon"] = "either"
        else:
            d["final_horizon"] = "3mo" if s3 > s12 else "12mo"
        d["final_score"] = max(s3, s12)

        fl, fh = d.get("fair_entry_low_usd"), d.get("fair_entry_high_usd")
        cp = d.get("current_price_at_scoring_usd")
        if fl is not None and fh is not None and cp:
            fair_mid = (float(fl) + float(fh)) / 2.0
            d["current_vs_fair_mid_pct"] = (
                (float(cp) - fair_mid) / fair_mid * 100.0 if fair_mid > 0 else None
            )
        else:
            d["current_vs_fair_mid_pct"] = None

        pack = pack_lookup.get(t, {})
        d["archetype"] = (pack.get("archetype_verdict") or {}).get("archetype")
        d["composite_best"] = (pack.get("archetype_verdict") or {}).get("composite_best")
        d["fund_count"] = (pack.get("fund_accumulation") or {}).get("fund_count")
        d["market_cap_usd"] = (pack.get("market_snapshot") or {}).get("market_cap_usd")
        d["industry"] = (pack.get("identity") or {}).get("industry")
        out.append(d)

    out.sort(key=lambda r: r.get("final_score") or -1e9, reverse=True)
    for i, r in enumerate(out, 1):
        r["final_rank"] = i
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", type=int, default=None,
                        help="Recompute only this run_id (default: all runs)")
    parser.add_argument("--quarter", default=None,
                        help="Limit to this quarter (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print proposed changes; don't write")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    packs_db = PROJECT_ROOT / "context_packs.db"
    scores_db = PROJECT_ROOT / "llm_scores.db"

    with sqlite3.connect(scores_db) as conn:
        # Apply additive migrations first (adds new D46 columns to old DBs).
        init_llm_scores_schema(conn)
        conn.row_factory = sqlite3.Row

        # Walk every llm_scores row, optionally filtered by run/quarter.
        sql = "SELECT * FROM llm_scores"
        params: list = []
        clauses = []
        if args.run_id is not None:
            clauses.append("run_id = ?"); params.append(args.run_id)
        if args.quarter is not None:
            clauses.append("quarter = ?"); params.append(args.quarter)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY quarter, run_id, ticker, horizon"
        rows = conn.execute(sql, tuple(params)).fetchall()
        if not rows:
            print("No matching llm_scores rows.")
            return 0

        # Group by quarter so we load packs once per quarter.
        from itertools import groupby
        rows_sorted = sorted(rows, key=lambda r: r["quarter"])
        per_quarter_packs: dict[str, dict] = {}
        for q, _ in groupby(rows_sorted, key=lambda r: r["quarter"]):
            per_quarter_packs[q] = _build_pack_lookup(packs_db, q)

        stats = {"updated": 0, "dry-run": 0,
                 "skip-no-current-price": 0, "skip-incomplete-row": 0}
        run_quarters_touched: dict[tuple[int, str], None] = {}
        for row in rows:
            pack = per_quarter_packs.get(row["quarter"], {}).get(row["ticker"])
            status, cp, appr, score = _recompute_one_row(
                conn, row, pack, dry_run=args.dry_run,
            )
            stats[status] = stats.get(status, 0) + 1
            run_quarters_touched[(row["run_id"], row["quarter"])] = None
            if args.verbose:
                _LOG.info("%s %s/%s/%s/%s  current=$%s  appr=%s%%  score=%s",
                          status, row["ticker"], row["horizon"],
                          row["prompt_version"], row["model"],
                          f"{cp:.2f}" if cp else "n/a",
                          f"{appr:+.1f}" if appr is not None else "n/a",
                          f"{score:+.2f}" if score is not None else "n/a")

        print()
        print("Recompute summary:")
        for k, v in stats.items():
            print(f"  {k:30s}: {v}")

        if not args.dry_run:
            # Rewrite final_rankings + re-render merged HTML/XLSX per touched (run_id, quarter).
            outputs_dir = PROJECT_ROOT / "Outputs"
            for (run_id, quarter), _ in run_quarters_touched.items():
                ranking_rows = _build_final_rankings_rows(
                    conn, quarter, run_id, per_quarter_packs.get(quarter, {}),
                )
                write_final_rankings(conn, run_id=run_id, quarter=quarter, rows=ranking_rows)
                conn.commit()
                # Re-render the merged HTML (with arrow-expand details) + XLSX.
                conn.row_factory = sqlite3.Row
                score_rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM llm_scores WHERE run_id=? AND quarter=? ORDER BY ticker, horizon",
                    (run_id, quarter),
                ).fetchall()]
                render_final_ranking_html(
                    ranking_rows, outputs_dir / f"final_ranking_{quarter}.html",
                    quarter=quarter, run_id=run_id, score_rows=score_rows,
                )
                render_final_ranking_xlsx(
                    ranking_rows, outputs_dir / f"final_ranking_{quarter}.xlsx",
                    quarter=quarter, run_id=run_id,
                )
                # Drop legacy standalone llm_responses HTML now that the merged view supersedes it.
                legacy = outputs_dir / f"llm_responses_{quarter}.html"
                if legacy.exists():
                    legacy.unlink()
                print(f"  rewrote final_rankings + merged HTML for run_id={run_id} "
                      f"quarter={quarter} ({len(ranking_rows)} rows)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
