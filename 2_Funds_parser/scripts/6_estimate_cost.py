"""Module 6 — pre-flight cost estimator (step B).

Standalone, no Anthropic API calls, no network. Reads:
  context_packs.db                                  (M5 output)
  config/scoring.yaml
  config/module_6_system_prompt.md
  config/module_6_few_shots.md
  llm_scores.db                                     (created on first run)

Writes:
  Outputs/cost_estimate_{quarter}.html
  (prints summary to stdout)

Usage:
  python scripts/6_estimate_cost.py
  python scripts/6_estimate_cost.py --quarter 2025Q4
  python scripts/6_estimate_cost.py --composite-best-min 7
  python scripts/6_estimate_cost.py --industry-allowlist Biotechnology
  python scripts/6_estimate_cost.py --market-cap-max-usd 3700000000
  python scripts/6_estimate_cost.py --no-rescue
  python scripts/6_estimate_cost.py --no-html
  python scripts/6_estimate_cost.py -v
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_5 import query_packs_for_quarter  # noqa: E402
from module_6 import (  # noqa: E402
    EstimateInputs,
    classify_ticker_tier,
    estimate_cost,
    init_llm_scores_schema,
    load_cacheable_prefix,
    render_cost_html,
)

_LOG = logging.getLogger("6_estimate_cost")

# Module-5-defined "train has left" archetype set used by D29 rescue.
_TRAIN_LEFT_ARCHETYPES = (
    "extended_uptrend",
    "late_stage_extension",
    "broken_trend",
    "sustained_decline",
    "parabolic_blowoff",
)


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve_quarter(context_packs_db: Path, requested: str | None) -> str:
    """Pick the latest quarter in context_packs.db when --quarter not given."""
    if requested:
        return requested
    with sqlite3.connect(context_packs_db) as conn:
        row = conn.execute(
            "SELECT quarter FROM context_packs GROUP BY quarter ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise SystemExit(f"No packs found in {context_packs_db}")
    return row[0]


def _apply_cli_gate_overrides(yaml_gates: dict, args: argparse.Namespace) -> dict:
    """Merge CLI --gate flags onto scoring.yaml::gates, returning the final dict."""
    g = json.loads(json.dumps(yaml_gates))  # deep copy
    if args.composite_best_min is not None:
        g["composite_best_min"] = float(args.composite_best_min)
    if args.industry_allowlist is not None:
        g["industry_allowlist"] = [s.strip() for s in args.industry_allowlist.split(",") if s.strip()]
    if args.market_cap_max_usd is not None:
        g["market_cap_max_usd"] = float(args.market_cap_max_usd)
    if args.no_rescue:
        g.setdefault("rescue", {})["enabled"] = False
    return g


def _print_summary(out, gates: dict) -> None:
    """Single-screen stdout summary. ASCII-only to survive Windows cp1252 console."""
    sep = "-" * 70
    print(sep)
    print(f"Module 6 - pre-flight cost estimate ({out.quarter})")
    print(sep)
    print(f"Model:           {out.model}")
    print(f"Prompt version:  {out.prompt_version}")
    print(f"Feed size:       {out.feed_size} tickers")
    print()
    print("Active gates (D21/D27/D28/D29):")
    for k, v in gates.items():
        print(f"  {k}: {v}")
    print()
    print(f"{'Tier':<8}{'Tickers':>10}{'API calls':>12}"
          f"{'Avg in tok':>14}{'Avg out tok':>14}{'Avg search':>12}")
    for tb in out.tier_breakdowns:
        print(f"{tb.tier.value:<8}{tb.ticker_count:>10}{tb.api_calls:>12}"
              f"{tb.avg_input_tokens_per_call:>14,}"
              f"{tb.avg_output_tokens_per_call:>14,}"
              f"{tb.avg_search_calls_per_call:>12,}")
    print()
    print(f"{'Scenario':<14}{'Token cost':>14}{'Search fee':>14}{'TOTAL':>14}")
    for s in out.scenarios:
        marker = "  <- prod" if s.name == "cache+batch" else ""
        print(f"{s.name:<14}${s.token_cost_usd:>12,.2f}"
              f"${s.search_fee_usd_upper_bound:>12,.2f}"
              f"${s.total_usd:>12,.2f}{marker}")
    print()
    print(f"Worst case (all Tier B escalate to Tier C):  "
          f"${out.worst_case_if_all_b_escalate_usd:,.2f}")
    print()
    print("Notes:")
    for n in out.notes:
        # strip non-ASCII chars (like the en-dash) defensively
        clean = n.encode("ascii", errors="replace").decode("ascii")
        print(f"  - {clean}")
    print(sep)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--quarter", default=None,
                        help="Quarter YYYYQn (default: latest in context_packs.db)")
    parser.add_argument("--composite-best-min", type=float, default=None,
                        help="Override D21 composite_best_min from scoring.yaml")
    parser.add_argument("--industry-allowlist", default=None,
                        help="Override D27 industry_allowlist (comma-separated)")
    parser.add_argument("--market-cap-max-usd", type=float, default=None,
                        help="Override D28 market_cap_max_usd")
    parser.add_argument("--no-rescue", action="store_true",
                        help="Disable D29 fund-flow rescue")
    parser.add_argument("--no-html", action="store_true",
                        help="Skip writing the HTML report — stdout only")
    parser.add_argument("--sample-packs", type=int, default=5,
                        help="Number of packs to sample for suffix-token averaging (default 5)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_dir = PROJECT_ROOT / "config"
    scoring = _load_yaml(config_dir / "scoring.yaml")

    context_packs_db = PROJECT_ROOT / "context_packs.db"
    llm_scores_db = PROJECT_ROOT / "llm_scores.db"
    if not context_packs_db.exists():
        raise SystemExit(f"context_packs.db not found at {context_packs_db}. "
                         "Run scripts/5_build_context_packs.py first.")

    quarter = _resolve_quarter(context_packs_db, args.quarter)
    gates = _apply_cli_gate_overrides(scoring["gates"], args)

    # ---------- 1. Query the feed via M5's helper ----------
    rescue = gates.get("rescue", {}) or {}
    with sqlite3.connect(context_packs_db) as packs_conn:
        packs_conn.row_factory = sqlite3.Row
        feed_rows = query_packs_for_quarter(
            packs_conn,
            quarter,
            "m5-v1",  # M5 pack_version (current)
            composite_best_min=float(gates["composite_best_min"]),
            industry_allowlist=gates.get("industry_allowlist"),
            market_cap_max_usd=float(gates["market_cap_max_usd"])
                if gates.get("market_cap_max_usd") else None,
            rescue_enabled=bool(rescue.get("enabled", False)),
            rescue_new_positions_min=int(rescue.get("new_positions_min", 1)),
            rescue_qoq_fund_count_change_min=int(rescue.get("qoq_fund_count_change_min", 1)),
            rescue_train_has_left_archetypes=list(
                rescue.get("excluded_archetypes", _TRAIN_LEFT_ARCHETYPES)
            ),
        )
    _LOG.info("Feed size: %d tickers", len(feed_rows))

    if not feed_rows:
        raise SystemExit(
            f"No tickers passed gates for {quarter}. Loosen gates and retry."
        )

    # ---------- 2. Sample packs to estimate suffix tokens ----------
    sample_n = min(args.sample_packs, len(feed_rows))
    pack_jsons_sample = [feed_rows[i]["pack_json"] for i in range(sample_n)]

    # ---------- 3. Tier each ticker against llm_scores.db ----------
    refresh_3mo_days = scoring["cache"]["refresh_threshold_weeks_3mo"] * 7
    refresh_12mo_days = scoring["cache"]["refresh_threshold_weeks_12mo"] * 7
    feed_tiers = []
    with sqlite3.connect(llm_scores_db) as scores_conn:
        init_llm_scores_schema(scores_conn)  # idempotent — creates schema on first run
        for row in feed_rows:
            pack = json.loads(row["pack_json"])
            tc = classify_ticker_tier(
                conn=scores_conn,
                ticker=row["ticker"],
                current_quarter=quarter,
                current_pack_hash=pack.get("source_rank_hash", ""),
                prompt_version=scoring["prompt_version"],
                model=scoring["model"],
                refresh_threshold_days_3mo=refresh_3mo_days,
                refresh_threshold_days_12mo=refresh_12mo_days,
            )
            feed_tiers.append(tc)

    # ---------- 4. Load cacheable prefix and run estimator ----------
    cached_prefix = load_cacheable_prefix(
        config_dir / Path(scoring["system_prompt_path"]).name,
        config_dir / Path(scoring["few_shot_examples_path"]).name,
    )

    estimate_inputs = EstimateInputs(
        quarter=quarter,
        prompt_version=scoring["prompt_version"],
        model=scoring["model"],
        cached_prefix_text=cached_prefix,
        pack_jsons_sample=pack_jsons_sample,
        feed_tiers=feed_tiers,
        max_output_tokens_full=int(scoring["max_output_tokens"]),
        max_output_tokens_tier_b=int(scoring["cache"]["tier_b_max_output_tokens"]),
        max_uses_full=int(scoring["web_search"]["max_uses"]),
        max_uses_tier_b=int(scoring["web_search"]["max_uses_tier_b"]),
        pricing=scoring["pricing"],
    )
    out = estimate_cost(estimate_inputs)

    # ---------- 5. Render outputs ----------
    if not args.no_html:
        outputs_dir = PROJECT_ROOT / "Outputs"
        report_path = outputs_dir / f"cost_estimate_{quarter}.html"
        render_cost_html(out, gates, report_path)
        print(f"Wrote {report_path}")
    _print_summary(out, gates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
