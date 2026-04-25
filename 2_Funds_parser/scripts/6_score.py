"""Module 6 — LLM scoring CLI (step C).

Reads:
  context_packs.db                                  (M5 output)
  config/scoring.yaml
  config/module_6_system_prompt.md
  config/module_6_few_shots.md
  config/module_6_web_search_whitelists.yaml
  llm_scores.db                                     (created on first run)
  .env                                              (ANTHROPIC_API_KEY)

Writes:
  llm_scores.db                                     (scores, runs, errors, search cache, final rankings)
  Outputs/final_ranking_{quarter}.html
  Outputs/final_ranking_{quarter}.xlsx
  Outputs/llm_responses_{quarter}.html
  Outputs/cost_estimate_{quarter}.html              (re-rendered before dispatch)

Usage:
  python scripts/6_score.py                                    # interactive, batch mode
  python scripts/6_score.py --mode sync                        # parallel async fan-out
  python scripts/6_score.py --ticker TCRX                      # single-ticker debug
  python scripts/6_score.py --tickers ABEO,RCKT,SVRA           # comma-list test slice
  python scripts/6_score.py --selection-from-html Outputs/final_ranking_2025Q4.html   # D48 — re-score only the user-checked tickers
  python scripts/6_score.py --limit 3                          # first N tickers from feed
  python scripts/6_score.py --print-prompt TCRX                # dump user message, no dispatch
  python scripts/6_score.py --dry-run                          # cost estimate only, no dispatch
  python scripts/6_score.py --non-interactive                  # use scoring.yaml::gates fallback
  python scripts/6_score.py --force-refresh                    # bypass tiering, all Tier C
  python scripts/6_score.py --no-light-refresh                 # Tier B demoted to Tier C
  python scripts/6_score.py --quarter 2025Q4                   # explicit quarter
  python scripts/6_score.py --yes                              # D48 — bypass mandatory [y/N] gate (single-shot)
  python scripts/6_score.py -v                                 # verbose

Cost-control gates default to scoring.yaml::gates. The --interactive default
runs D30 prompts when stdin is a TTY. The pre-dispatch [y/N] confirmation
gate (D48) is mandatory regardless of TTY state — only `--yes` bypasses.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
REPO_ROOT = PROJECT_ROOT.parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_5 import query_packs_for_quarter  # noqa: E402
from module_6b import (  # noqa: E402
    apply_modifiers_to_run,
    collect_ticker_factors,
    load_modifier_config,
    load_selection_json,
    merge_modifier_weights,
    merge_selection,
    parse_all_tickers,
    parse_selected_tickers,
    selection_json_path,
    write_selection_json,
)
from module_6 import (  # noqa: E402
    EstimateInputs,
    ParseError,
    Tier,
    build_user_message_full,
    build_web_search_tool_def,
    classify_ticker_tier,
    close_run,
    compute_ticker_score,
    dispatch_batch,
    dispatch_sync,
    estimate_cost,
    init_llm_scores_schema,
    load_allowed_domains_for_industry,
    load_cacheable_prefix,
    open_run,
    parse_full_score,
    poll_and_collect_batch,
    render_cost_html,
    render_final_ranking_html,
    render_final_ranking_xlsx,
    render_llm_responses_html,
    submit_batch,
    update_run_batch_id,
    upsert_web_search_cache_row,
    write_error_row,
    write_final_rankings,
    write_full_score_rows,
)

_LOG = logging.getLogger("6_score")

_TRAIN_LEFT_ARCHETYPES = (
    "extended_uptrend", "late_stage_extension", "broken_trend",
    "sustained_decline", "parabolic_blowoff",
)


# ─────────────────────────────────────────────────────────────────────────────
# Config / paths helpers
# ─────────────────────────────────────────────────────────────────────────────


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve_quarter(context_packs_db: Path, requested: str | None) -> str:
    if requested:
        return requested
    with sqlite3.connect(context_packs_db) as conn:
        row = conn.execute(
            "SELECT quarter FROM context_packs GROUP BY quarter ORDER BY quarter DESC LIMIT 1"
        ).fetchone()
    if not row:
        raise SystemExit(f"No packs found in {context_packs_db}")
    return row[0]


def _domain_of(url: str) -> str:
    """Crude domain extraction (no urllib heavy dep). 'https://x.y.com/path' → 'x.y.com'."""
    if not url:
        return ""
    s = url.split("//", 1)[-1]
    return s.split("/", 1)[0].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Gate prompts (D30) — ASCII only for Windows console safety
# ─────────────────────────────────────────────────────────────────────────────


def _query_count(packs_conn: sqlite3.Connection, quarter: str, **gates) -> int:
    """Live count of feed size at given gates — used by interactive prompts."""
    rescue = gates.get("rescue", {}) or {}
    rows = query_packs_for_quarter(
        packs_conn, quarter, "m5-v1",
        composite_best_min=float(gates.get("composite_best_min", 6.0)),
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
    return len(rows)


def _prompt_d21(packs_conn, quarter, current_gates) -> float:
    """D21 composite_best_min — multiple-choice."""
    g = lambda v: dict(current_gates, composite_best_min=v)
    print()
    print("D21 - composite_best_min (minimum score from M4b)")
    print(f"  [1]  >= 5    ({_query_count(packs_conn, quarter, **g(5.0))} tickers)")
    print(f"  [2]  >= 6    ({_query_count(packs_conn, quarter, **g(6.0))} tickers)  <-- default")
    print(f"  [3]  >= 7    ({_query_count(packs_conn, quarter, **g(7.0))} tickers)")
    print(f"  [4]  >= 8    ({_query_count(packs_conn, quarter, **g(8.0))} tickers)")
    print(f"  [5]  custom")
    while True:
        choice = input("Choose [1-5, default 2]: ").strip() or "2"
        if choice in {"1", "2", "3", "4"}:
            return float({"1": 5.0, "2": 6.0, "3": 7.0, "4": 8.0}[choice])
        if choice == "5":
            try:
                return float(input("Enter composite_best_min (e.g. 6.5): ").strip())
            except ValueError:
                print("Not a number, try again.")
                continue
        print("Invalid choice.")


def _prompt_d27(packs_conn, quarter, current_gates) -> list[str] | None:
    """D27 industry_allowlist — top industries computed live."""
    rows = packs_conn.execute(
        "SELECT industry, COUNT(*) FROM context_packs WHERE quarter=? "
        "GROUP BY industry ORDER BY 2 DESC LIMIT 12",
        (quarter,),
    ).fetchall()
    print()
    print("D27 - industry_allowlist (top industries this quarter)")
    for i, (ind, cnt) in enumerate(rows, 1):
        print(f"  [{i}]  {ind}  ({cnt})")
    print(f"  [a]  All Healthcare-sector industries (default)")
    print(f"  [c]  Custom (comma-separated)")
    print(f"  [n]  No filter (pass-through)")
    while True:
        choice = input("Choose number, multiple comma-separated, or [a/c/n]: ").strip().lower() or "a"
        if choice == "n":
            return None
        if choice == "c":
            txt = input("Enter industries (comma-separated): ").strip()
            return [s.strip() for s in txt.split(",") if s.strip()] or None
        if choice == "a":
            # Default biotech-heavy preset
            return ["Biotechnology", "Drug Manufacturers - Specialty & Generic"]
        try:
            picks = [int(x.strip()) for x in choice.split(",")]
            return [rows[i - 1][0] for i in picks if 1 <= i <= len(rows)]
        except ValueError:
            print("Invalid choice.")


def _prompt_d28(packs_conn, quarter, current_gates) -> float | None:
    g = lambda v: dict(current_gates, market_cap_max_usd=v)
    print()
    print("D28 - market_cap_max_usd (with current D21+D27)")
    bands = [
        ("$500M", 500_000_000),
        ("$1B", 1_000_000_000),
        ("$2B", 2_000_000_000),
        ("$3.7B", 3_700_000_000),
        ("$5B", 5_000_000_000),
        ("$7.5B", 7_500_000_000),
        ("$10B", 10_000_000_000),
    ]
    for i, (label, val) in enumerate(bands, 1):
        marker = "  <-- default" if val == 3_700_000_000 else ""
        print(f"  [{i}]  {label}  ({_query_count(packs_conn, quarter, **g(val))} tickers){marker}")
    print(f"  [{len(bands) + 1}]  No cap")
    while True:
        choice = input(f"Choose [1-{len(bands) + 1}, default 4]: ").strip() or "4"
        try:
            n = int(choice)
            if 1 <= n <= len(bands):
                return float(bands[n - 1][1])
            if n == len(bands) + 1:
                return None
        except ValueError:
            pass
        print("Invalid choice.")


def _prompt_d29(packs_conn, quarter, current_gates) -> bool:
    g_on = dict(current_gates); g_on["rescue"] = dict(g_on.get("rescue", {}), enabled=True)
    g_off = dict(current_gates); g_off["rescue"] = dict(g_off.get("rescue", {}), enabled=False)
    on = _query_count(packs_conn, quarter, **g_on)
    off = _query_count(packs_conn, quarter, **g_off)
    print()
    print(f"D29 - fund-flow rescue clause")
    print(f"  [1]  ENABLED   (final feed: {on} tickers — rescues +{on - off})  <-- default")
    print(f"  [2]  DISABLED  (final feed: {off} tickers — strict D21 only)")
    while True:
        choice = input("Choose [1-2, default 1]: ").strip() or "1"
        if choice in {"1", "2"}:
            return choice == "1"
        print("Invalid choice.")


def _interactive_gates(packs_conn, quarter, yaml_gates: dict) -> dict:
    """Run all four D30 prompts against scoring.yaml fallback."""
    g = dict(yaml_gates)
    g["composite_best_min"] = _prompt_d21(packs_conn, quarter, g)
    g["industry_allowlist"] = _prompt_d27(packs_conn, quarter, g)
    g["market_cap_max_usd"] = _prompt_d28(packs_conn, quarter, g)
    rescue = dict(g.get("rescue", {})) or {}
    rescue["enabled"] = _prompt_d29(packs_conn, quarter, g)
    g["rescue"] = rescue
    return g


# ─────────────────────────────────────────────────────────────────────────────
# Cost computation + USD pricing helper
# ─────────────────────────────────────────────────────────────────────────────


def _usd_cost_per_call(
    *,
    pricing: dict,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    web_search_calls: int,
    batch_mode: bool,
) -> float:
    in_per_mtok = float(pricing["input_per_mtok"])
    out_per_mtok = float(pricing["output_per_mtok"])
    cache_read_mult = float(pricing["cache_read_multiplier"])
    cache_create_mult = float(pricing["cache_creation_multiplier"])
    batch_disc = float(pricing["batch_discount"]) if batch_mode else 1.0
    search_per_1k = float(pricing["web_search_per_1k"])
    non_cached_input = max(0, input_tokens - cache_read_tokens - cache_creation_tokens)
    token_cost = (
        (non_cached_input / 1_000_000.0) * in_per_mtok
        + (cache_read_tokens / 1_000_000.0) * in_per_mtok * cache_read_mult
        + (cache_creation_tokens / 1_000_000.0) * in_per_mtok * cache_create_mult
        + (output_tokens / 1_000_000.0) * out_per_mtok
    ) * batch_disc
    search_fee = (web_search_calls / 1000.0) * search_per_1k
    return token_cost + search_fee


# ─────────────────────────────────────────────────────────────────────────────
# Per-ticker prep — build user messages + tool defs for the dispatcher
# ─────────────────────────────────────────────────────────────────────────────


def _prepare_per_ticker(
    feed_rows: list[sqlite3.Row],
    feed_tiers: list,
    *,
    scores_conn: sqlite3.Connection,
    scoring: dict,
    config_dir: Path,
    only_tier_c: bool,
) -> list[dict]:
    """For each ticker, build (ticker, pack, user_message, web_search_tool, tier).

    only_tier_c=True restricts to tickers needing a full call (skips Tier A;
    for step B/C bring-up Tier B is included as a Tier C call to keep the
    code path simple — light-refresh dispatch can be a step-D refinement).
    """
    out = []
    whitelists_path = str(config_dir / Path(scoring["web_search"]["whitelists_path"]).name)
    cache_cfg = scoring["cache"]
    web_search = scoring["web_search"]
    for row, tc in zip(feed_rows, feed_tiers):
        if tc.overall_tier == Tier.A and only_tier_c:
            # Cache hit; no API call this run
            continue
        pack = json.loads(row["pack_json"])
        industry = pack.get("identity", {}).get("industry", "")
        allowed_domains = load_allowed_domains_for_industry(whitelists_path, industry)
        tool_def = build_web_search_tool_def(
            allowed_domains=allowed_domains,
            max_uses=int(web_search["max_uses"]),
        )
        user_message = build_user_message_full(
            pack=pack,
            scores_conn=scores_conn,
            ticker=row["ticker"],
            prompt_version=scoring["prompt_version"],
            model=scoring["model"],
            web_search_lookback_days=int(cache_cfg["web_search_lookback_days"]),
            prior_thesis_max_per_horizon=int(cache_cfg["prior_thesis_max_per_horizon"]),
        )
        out.append({
            "ticker": row["ticker"],
            "pack": pack,
            "user_message": user_message,
            "web_search_tool": tool_def,
            "tier": tc.overall_tier,
            "pack_source_rank_hash": pack.get("source_rank_hash", ""),
        })
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Final rankings projection from llm_scores
# ─────────────────────────────────────────────────────────────────────────────


def _aggregate_ranking_from_score_rows(
    rows: list,
    quarter: str,
    run_id: int,
    pack_lookup: dict[str, dict],
) -> list[dict]:
    """Shared aggregation: take an iterable of llm_scores Row/dict objects
    and project them to ``final_rankings`` shape (one row per ticker).

    Used by both the per-run builder (audit table write) and the merged
    "latest score per ticker" builder (selective-dispatch re-render).
    """
    by_ticker: dict[str, dict] = {}
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
        # Lead indication's pos_adjusted: pull from research_brief_json
        if "fda_pos_adjusted_lead" not in d:
            try:
                rb = json.loads(r["research_brief_json"] or "{}")
                lead_name = (rb.get("fda") or {}).get("lead_indication") or ""
                lead_lc = lead_name.lower()
                indications = rb.get("rnpv_by_indication", []) or []
                pos = None
                # 1. Exact name match
                for ind in indications:
                    if (ind.get("indication") or "").lower() == lead_lc:
                        pos = ind.get("pos_adjusted"); break
                # 2. Substring match (model often abbreviates one side)
                if pos is None and lead_lc:
                    for ind in indications:
                        ind_lc = (ind.get("indication") or "").lower()
                        if ind_lc and (ind_lc in lead_lc or lead_lc in ind_lc):
                            pos = ind.get("pos_adjusted"); break
                # 3. Fall back to highest-rnpv-contribution indication
                if pos is None and indications:
                    top = max(indications,
                              key=lambda i: float(i.get("rnpv_contribution_usd") or 0))
                    pos = top.get("pos_adjusted")
                d["fda_pos_adjusted_lead"] = pos
            except Exception:
                d["fda_pos_adjusted_lead"] = None

        # D46 — current price snapshot is the same on both horizon rows; capture once.
        if "current_price_at_scoring_usd" not in d:
            d["current_price_at_scoring_usd"] = r["current_price_at_scoring_usd"]

        if r["horizon"] == "3mo":
            d["score_at_current_3mo"] = r["score_at_current_pct_per_month"]      # D46 — primary
            d["score_at_fair_3mo"] = r["score_at_fair_pct_per_month"]
            d["score_at_full_reward_3mo"] = r["score_at_full_reward_pct_per_month"]
            d["target_price_3mo_usd"] = r["target_price_usd"]
            d["appreciation_from_current_3mo_pct"] = r["appreciation_from_current_pct"]
            d["appreciation_from_fair_3mo_pct"] = r["appreciation_from_fair_pct"]
            d["time_to_catalyst_3mo_weeks"] = r["time_to_catalyst_weeks"]
            d["probability_3mo"] = r["probability"]
        elif r["horizon"] == "12mo":
            d["score_at_current_12mo"] = r["score_at_current_pct_per_month"]     # D46 — primary
            d["score_at_fair_12mo"] = r["score_at_fair_pct_per_month"]
            d["score_at_full_reward_12mo"] = r["score_at_full_reward_pct_per_month"]
            d["target_price_12mo_usd"] = r["target_price_usd"]
            d["appreciation_from_current_12mo_pct"] = r["appreciation_from_current_pct"]
            d["appreciation_from_fair_12mo_pct"] = r["appreciation_from_fair_pct"]
            d["time_to_catalyst_12mo_weeks"] = r["time_to_catalyst_weeks"]
            d["probability_12mo"] = r["probability"]

    # Compute final_horizon + final_score (D46 — uses score_at_current); sort and rank
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

        # D46 — positioning gap: how far above/below fair_mid is the current price?
        fl, fh = d.get("fair_entry_low_usd"), d.get("fair_entry_high_usd")
        cp = d.get("current_price_at_scoring_usd")
        if fl is not None and fh is not None and cp:
            fair_mid = (float(fl) + float(fh)) / 2.0
            d["current_vs_fair_mid_pct"] = (
                (float(cp) - fair_mid) / fair_mid * 100.0 if fair_mid > 0 else None
            )
        else:
            d["current_vs_fair_mid_pct"] = None

        # Pack passthrough (M5 fields)
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


def _build_final_rankings_rows(
    scores_conn: sqlite3.Connection,
    quarter: str,
    run_id: int,
    pack_lookup: dict[str, dict],
) -> list[dict]:
    """Per-run rankings — used by `write_final_rankings` (audit-trail write)."""
    scores_conn.row_factory = sqlite3.Row
    rows = scores_conn.execute(
        "SELECT * FROM llm_scores WHERE quarter=? AND run_id=? ORDER BY ticker, horizon",
        (quarter, run_id),
    ).fetchall()
    return _aggregate_ranking_from_score_rows(rows, quarter, run_id, pack_lookup)


def _latest_score_rows(
    scores_conn: sqlite3.Connection,
    quarter: str,
    tickers: list[str] | None = None,
) -> list[dict]:
    """Latest llm_scores row per ``(ticker, horizon)`` across the quarter.

    When ``tickers`` is given, only those are considered; else all tickers
    in the quarter. Selects the highest ``run_id`` per (ticker, horizon).
    Returns plain dicts so downstream renderers don't depend on Row objects.
    """
    scores_conn.row_factory = sqlite3.Row
    query = "SELECT * FROM llm_scores WHERE quarter=?"
    params: list = [quarter]
    if tickers:
        placeholders = ",".join("?" for _ in tickers)
        query += f" AND ticker IN ({placeholders})"
        params.extend(tickers)
    query += " ORDER BY ticker, horizon, run_id"
    raw = scores_conn.execute(query, params).fetchall()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for r in raw:
        latest[(r["ticker"], r["horizon"])] = r
    return [dict(r) for r in latest.values()]


def _build_merged_ranking_rows(
    scores_conn: sqlite3.Connection,
    quarter: str,
    run_id: int,
    pack_lookup: dict[str, dict],
    tickers: list[str] | None = None,
) -> list[dict]:
    """Latest-per-ticker rankings — used for re-rendering after a selective
    dispatch (D48), so unselected tickers stay visible with their stale scores
    while newly-dispatched tickers show their fresh scores.
    """
    rows = _latest_score_rows(scores_conn, quarter, tickers)
    return _aggregate_ranking_from_score_rows(rows, quarter, run_id, pack_lookup)


def _load_packs_for_tickers(
    context_packs_db: Path,
    quarter: str,
    tickers: list[str],
) -> dict[str, dict]:
    """Bulk-load M5 packs for an arbitrary ticker list."""
    if not tickers:
        return {}
    out: dict[str, dict] = {}
    with sqlite3.connect(context_packs_db) as conn:
        placeholders = ",".join("?" for _ in tickers)
        rows = conn.execute(
            f"SELECT ticker, pack_json FROM context_packs "
            f"WHERE quarter=? AND ticker IN ({placeholders})",
            (quarter, *tickers),
        ).fetchall()
    for ticker, pack_json in rows:
        try:
            out[ticker] = json.loads(pack_json)
        except Exception:
            pass
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quarter", default=None)
    parser.add_argument("--mode", default=None, choices=("sync", "batch"),
                        help="Override scoring.yaml::dispatch.mode")
    feed_group = parser.add_mutually_exclusive_group()
    feed_group.add_argument("--ticker", default=None,
                            help="Single-ticker debug run (skips gates, uses pack as-is)")
    feed_group.add_argument("--tickers", default=None,
                            help="Comma-separated explicit ticker list (skips gates)")
    feed_group.add_argument("--selection-from-html", default=None, metavar="PATH",
                            help="D48 — parse PATH (saved final-ranking HTML) and "
                                 "dispatch only the tickers whose checkboxes are checked.")
    parser.add_argument("--limit", type=int, default=None,
                        help="After gating, score only the first N tickers")
    parser.add_argument("--print-prompt", default=None, metavar="TICKER",
                        help="Print the user message for one ticker and exit (no API call)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run cost estimator only; no API call")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Use scoring.yaml::gates fallback without D30 prompts")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Bypass tiering — every selected ticker becomes Tier C")
    parser.add_argument("--no-light-refresh", action="store_true",
                        help="Tier B demoted to Tier C (no light-refresh path)")
    parser.add_argument("--composite-best-min", type=float, default=None)
    parser.add_argument("--industry-allowlist", default=None,
                        help="Comma-separated industries (overrides yaml + interactive)")
    parser.add_argument("--market-cap-max-usd", type=float, default=None)
    parser.add_argument("--no-rescue", action="store_true")
    parser.add_argument("--yes", action="store_true",
                        help="D48 — single-shot bypass of the mandatory pre-dispatch "
                             "[y/N] confirmation gate. Use only when fully aware of cost.")
    parser.add_argument("--sync-concurrency", type=int, default=None,
                        help="D51 — override scoring.yaml::dispatch.sync_concurrency "
                             "for this run. Lower values reduce 429 rate-limit pressure.")
    parser.add_argument("--resume-run", type=int, default=None, metavar="RUN_ID",
                        help="D51 — recover a half-completed batch run. Skip submit; "
                             "look up batch_id from llm_runs and poll-collect-write only.")
    parser.add_argument("-v", "--verbose", action="store_true")
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
        raise SystemExit(f"context_packs.db not found at {context_packs_db}")

    # Load .env from repo root for ANTHROPIC_API_KEY
    load_dotenv(REPO_ROOT / ".env")

    # ────────────── Step 0 — resume an in-progress batch (D51) ──────────────
    # Skip submit + open_run + the cost gate; just poll the prior batch and
    # write its results. Used when an earlier `--mode batch` script crashed
    # mid-poll (the run record + batch_id are already in llm_runs from the
    # early-commit path in step 8). No new Anthropic charges; the batch's
    # tokens were already paid at submit time.
    if args.resume_run is not None:
        if not llm_scores_db.exists():
            raise SystemExit(f"--resume-run: llm_scores.db not found at {llm_scores_db}")
        with sqlite3.connect(llm_scores_db) as _c:
            _c.row_factory = sqlite3.Row
            run_row = _c.execute(
                "SELECT * FROM llm_runs WHERE run_id=?", (args.resume_run,),
            ).fetchone()
        if run_row is None:
            raise SystemExit(f"--resume-run {args.resume_run}: not found in llm_runs")
        if not run_row["batch_id"]:
            raise SystemExit(
                f"--resume-run {args.resume_run}: no batch_id (was not a batch dispatch). "
                "Resume only applies to runs with batch mode."
            )
        run_id = int(run_row["run_id"])
        batch_id = run_row["batch_id"]
        quarter = run_row["quarter"]
        mode = "batch"
        gates = json.loads(run_row["gate_config_json"] or "{}")
        selection_source = gates.get("selection_source", "resume")
        explicit_ticker_list = None
        selection_html_path = None
        all_input_tickers = None
        prior_selection = None
        feed_rows = []                                  # synthetic; not used in step 9
        per_ticker = []                                 # prep_index will be empty; pack_source_rank_hash defaults to ""
        counts = {Tier.A: int(run_row["tier_a_count"] or 0),
                  Tier.B: int(run_row["tier_b_count"] or 0),
                  Tier.C: int(run_row["tier_c_count"] or 0)}
        cached_prefix = ""                              # unused in resume

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY not set in environment / .env")

        print()
        print("=" * 64)
        print(f"  RESUME mode (D51)")
        print(f"    run_id:    {run_id}")
        print(f"    batch_id:  {batch_id}")
        print(f"    quarter:   {quarter}")
        print(f"    polling for results (no new Anthropic charges)…")
        print("=" * 64)
        started = time.time()
        try:
            results = poll_and_collect_batch(
                api_key=api_key, batch_id=batch_id,
                poll_interval_s=int(scoring["dispatch"]["poll_interval_s"]),
                timeout_s=int(scoring["dispatch"]["timeout_s"]),
            )
        except Exception as e:
            raise SystemExit(
                f"poll_and_collect_batch failed: {e}\n"
                "If the batch is still processing, just rerun this command later."
            )
        elapsed = time.time() - started
        result_tickers = sorted({r.ticker for r in results})
        pack_lookup = _load_packs_for_tickers(context_packs_db, quarter, result_tickers)
        # Fall through to step 9 with run_id, results, pack_lookup all set.
    else:
        run_id = None                                   # opened later in step 9
        quarter = _resolve_quarter(context_packs_db, args.quarter)
        explicit_ticker_list = None
        selection_html_path = None
        prior_selection = None
        all_input_tickers = None
        selection_source = "gates"

    # In resume mode (D51), Steps 1-8 are skipped entirely — every variable
    # they would set is already populated from the Step 0 short-circuit
    # at the top of main(). The body below is indented 4 more spaces.
    if args.resume_run is None:
        # ────────────── Step 1 — gates ──────────────
        yaml_gates = scoring["gates"]
        explicit_ticker_list: list[str] | None = None
        selection_html_path: Path | None = None
        prior_selection: set[str] | None = None
        all_input_tickers: list[str] | None = None
        selection_source = "gates"
        if args.selection_from_html:
            # D49 — derive feed from the sidecar JSON (preferred) written by the
            # local server (scripts/6_serve_report.py). Falls back to D48 HTML
            # `checked`-attribute parsing for back-compat with reports rendered
            # before D49.
            selection_html_path = Path(args.selection_from_html)
            if not selection_html_path.exists():
                raise SystemExit(
                    f"--selection-from-html path not found: {selection_html_path}"
                )
            json_path = selection_json_path(selection_html_path)
            sidecar = load_selection_json(json_path)
            if sidecar is not None:
                parsed_checked = list(sidecar.get("selected_tickers") or [])
                all_input_tickers = list(sidecar.get("all_tickers") or [])
                selection_source = "json_sidecar"
                origin_label = json_path.name
            else:
                parsed_checked = parse_selected_tickers(selection_html_path)
                all_input_tickers = parse_all_tickers(selection_html_path)
                selection_source = "html"
                origin_label = (selection_html_path.name
                                + " (no sidecar JSON; fell back to HTML attrs)")
            if not parsed_checked:
                raise SystemExit(
                    f"No tickers selected for {selection_html_path}.\n"
                    "Start the local server (python scripts/6_serve_report.py), "
                    "edit checkboxes in your browser (auto-saves), then re-run."
                )
            explicit_ticker_list = parsed_checked
            prior_selection = set(parsed_checked)
            gates = dict(yaml_gates)  # gates ignored under selection-from-html
            print(f"Selection-from-{selection_source}: {len(parsed_checked)} "
                  f"ticker(s) selected of {len(all_input_tickers)} in {origin_label}")
        elif args.ticker or args.tickers:
            names = [args.ticker] if args.ticker else [s.strip() for s in args.tickers.split(",") if s.strip()]
            explicit_ticker_list = [n for n in names if n]
            gates = dict(yaml_gates)  # gates ignored when explicit ticker list is given
            selection_source = "tickers_flag"
            print(f"Explicit ticker list: {explicit_ticker_list}")
        elif args.non_interactive or not sys.stdin.isatty():
            gates = dict(yaml_gates)
            if args.composite_best_min is not None:
                gates["composite_best_min"] = float(args.composite_best_min)
            if args.industry_allowlist is not None:
                gates["industry_allowlist"] = [s.strip() for s in args.industry_allowlist.split(",") if s.strip()]
            if args.market_cap_max_usd is not None:
                gates["market_cap_max_usd"] = float(args.market_cap_max_usd)
            if args.no_rescue:
                gates.setdefault("rescue", {})["enabled"] = False
        else:
            with sqlite3.connect(context_packs_db) as packs_conn:
                packs_conn.row_factory = sqlite3.Row
                gates = _interactive_gates(packs_conn, quarter, yaml_gates)

        # ────────────── Step 2 — feed ──────────────
        with sqlite3.connect(context_packs_db) as packs_conn:
            packs_conn.row_factory = sqlite3.Row
            if explicit_ticker_list:
                placeholders = ",".join("?" for _ in explicit_ticker_list)
                feed_rows = packs_conn.execute(
                    f"SELECT * FROM context_packs WHERE quarter=? AND ticker IN ({placeholders})",
                    (quarter, *explicit_ticker_list),
                ).fetchall()
                missing = set(explicit_ticker_list) - {r["ticker"] for r in feed_rows}
                if missing:
                    print(f"WARNING: tickers not found in context_packs: {sorted(missing)}")
            else:
                rescue = gates.get("rescue", {}) or {}
                feed_rows = query_packs_for_quarter(
                    packs_conn, quarter, "m5-v1",
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
            if args.limit is not None:
                feed_rows = feed_rows[: args.limit]
        if not feed_rows:
            raise SystemExit(f"No tickers in feed for {quarter}.")
        print(f"Feed size: {len(feed_rows)} tickers")

        # ────────────── Step 3 — tier classification ──────────────
        refresh_3mo = scoring["cache"]["refresh_threshold_weeks_3mo"] * 7
        refresh_12mo = scoring["cache"]["refresh_threshold_weeks_12mo"] * 7
        feed_tiers = []
        with sqlite3.connect(llm_scores_db) as scores_conn:
            init_llm_scores_schema(scores_conn)
            for r in feed_rows:
                pack = json.loads(r["pack_json"])
                tc = classify_ticker_tier(
                    conn=scores_conn,
                    ticker=r["ticker"],
                    current_quarter=quarter,
                    current_pack_hash=pack.get("source_rank_hash", ""),
                    prompt_version=scoring["prompt_version"],
                    model=scoring["model"],
                    refresh_threshold_days_3mo=refresh_3mo,
                    refresh_threshold_days_12mo=refresh_12mo,
                )
                if args.force_refresh:
                    tc = type(tc)(
                        ticker=tc.ticker, overall_tier=Tier.C,
                        tier_3mo=Tier.C, tier_12mo=Tier.C,
                        days_since_3mo=tc.days_since_3mo, days_since_12mo=tc.days_since_12mo,
                        pack_hash_match_3mo=False, pack_hash_match_12mo=False,
                    )
                feed_tiers.append(tc)

        counts = {Tier.A: 0, Tier.B: 0, Tier.C: 0}
        for tc in feed_tiers:
            counts[tc.overall_tier] += 1
        print(f"Tier breakdown: A={counts[Tier.A]}  B={counts[Tier.B]}  C={counts[Tier.C]}")

        # ────────────── Step 4 — print-prompt mode ──────────────
        if args.print_prompt:
            with sqlite3.connect(llm_scores_db) as scores_conn:
                init_llm_scores_schema(scores_conn)
                for row in feed_rows:
                    if row["ticker"] != args.print_prompt:
                        continue
                    pack = json.loads(row["pack_json"])
                    user_msg = build_user_message_full(
                        pack=pack, scores_conn=scores_conn,
                        ticker=row["ticker"],
                        prompt_version=scoring["prompt_version"],
                        model=scoring["model"],
                        web_search_lookback_days=int(scoring["cache"]["web_search_lookback_days"]),
                        prior_thesis_max_per_horizon=int(scoring["cache"]["prior_thesis_max_per_horizon"]),
                    )
                    print("\n" + "=" * 70)
                    print(f"USER MESSAGE for {row['ticker']} (length: {len(user_msg)} chars)")
                    print("=" * 70)
                    print(user_msg)
                    return 0
                print(f"Ticker {args.print_prompt} not in feed.")
                return 1

        # ────────────── Step 5 — cost estimate ──────────────
        cached_prefix = load_cacheable_prefix(
            config_dir / Path(scoring["system_prompt_path"]).name,
            config_dir / Path(scoring["few_shot_examples_path"]).name,
        )
        sample_packs = [feed_rows[i]["pack_json"] for i in range(min(5, len(feed_rows)))]
        estimate = estimate_cost(EstimateInputs(
            quarter=quarter,
            prompt_version=scoring["prompt_version"],
            model=scoring["model"],
            cached_prefix_text=cached_prefix,
            pack_jsons_sample=sample_packs,
            feed_tiers=feed_tiers,
            max_output_tokens_full=int(scoring["max_output_tokens"]),
            max_output_tokens_tier_b=int(scoring["cache"]["tier_b_max_output_tokens"]),
            max_uses_full=int(scoring["web_search"]["max_uses"]),
            max_uses_tier_b=int(scoring["web_search"]["max_uses_tier_b"]),
            pricing=scoring["pricing"],
        ))
        cost_html = PROJECT_ROOT / "Outputs" / f"cost_estimate_{quarter}.html"
        render_cost_html(estimate, gates, cost_html)
        print(f"Wrote {cost_html}")
        print(f"Estimated production cost: ${estimate.scenarios[2].total_usd:,.2f}  "
              f"(worst-case: ${estimate.worst_case_if_all_b_escalate_usd:,.2f})")

        if args.dry_run:
            return 0

        # ────────────── Step 6 — mandatory approval gate (D48) ──────────────
        # The gate ALWAYS prompts regardless of TTY state. Only `--yes` bypasses,
        # and only as an explicit single-shot flag — never config-driven.
        # When zero calls are scheduled (all-Tier-A), the gate is skipped — there
        # is nothing to confirm and the run still proceeds to re-render reports.
        n_dispatch = counts[Tier.C] + counts[Tier.B]
        if n_dispatch == 0:
            print("\nAll selected tickers are Tier-A cache hits — no API calls; "
                  "skipping confirmation gate and proceeding to report re-render.")
        elif args.yes:
            print(f"\n--yes specified: dispatching {n_dispatch} API call(s) "
                  f"for ~${estimate.scenarios[2].total_usd:,.2f} without confirmation.")
        else:
            try:
                prompt = (
                    f"\nProceed to dispatch {n_dispatch} API call(s) "
                    f"for ~${estimate.scenarios[2].total_usd:,.2f}? [y/N] "
                )
                answer = input(prompt).strip().lower() or "n"
            except EOFError:
                print("\n[stdin closed] aborting. Re-run with --yes to bypass the gate.")
                answer = "n"
            if answer != "y":
                print("Aborted.")
                return 0

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY not set in environment / .env")

        # ────────────── Step 7 — prepare per-ticker payloads ──────────────
        pack_lookup: dict[str, dict] = {}
        with sqlite3.connect(llm_scores_db) as scores_conn:
            scores_conn.row_factory = sqlite3.Row
            per_ticker = _prepare_per_ticker(
                feed_rows, feed_tiers,
                scores_conn=scores_conn,
                scoring=scoring,
                config_dir=config_dir,
                only_tier_c=True,  # step C: dispatch any non-Tier-A as full call
            )
            for r in feed_rows:
                pack_lookup[r["ticker"]] = json.loads(r["pack_json"])

        if not per_ticker:
            print("All tickers Tier-A cache hits — no API calls needed. Refreshing reports only.")

        mode = args.mode or scoring["dispatch"]["mode"]
        # D51 — sync-concurrency override + sync+large-feed sanity check.
        sync_concurrency = (
            int(args.sync_concurrency) if args.sync_concurrency is not None
            else int(scoring["dispatch"]["sync_concurrency"])
        )
        SYNC_FEED_WARN_THRESHOLD = 5
        if mode == "sync" and len(per_ticker) > SYNC_FEED_WARN_THRESHOLD:
            print()
            print("=" * 64)
            print(f"  WARNING: --mode sync with {len(per_ticker)} tickers may hit")
            print(f"  Anthropic's per-minute input-token rate limit (~30K tok/min on")
            print(f"  free tier). Each m6-v3 call burns ~90-130K tokens (input +")
            print(f"  cache_read + cache_create), so concurrent fan-out will trigger")
            print(f"  429s. Per-ticker failures will land in llm_errors and the run")
            print(f"  will continue, but consider:")
            print(f"    --mode batch          (50% off, no per-min limit, async)")
            print(f"    --sync-concurrency 1  (serialise sync; SDK still retries 429s)")
            print(f"  Current sync_concurrency = {sync_concurrency}.")
            print("=" * 64)
            print()
        print(f"\nDispatching {len(per_ticker)} calls in mode={mode}...")

        # ────────────── Step 8 — dispatch ──────────────
        started = time.time()
        batch_id: str | None = None
        run_id: int | None = None                              # set early for batch mode (D51)
        if per_ticker:
            if mode == "sync":
                results = dispatch_sync(
                    api_key=api_key, model=scoring["model"],
                    max_output_tokens=int(scoring["max_output_tokens"]),
                    system_prompt=cached_prefix,
                    per_ticker=per_ticker,
                    sync_concurrency=sync_concurrency,
                )
            else:
                # D51 — split submit + poll so the run record (with batch_id)
                # can be persisted to the DB BEFORE the long poll begins.
                # Without this, a script crash mid-poll orphans the batch
                # on Anthropic's side with no way to recover the costs paid.
                batch_id = submit_batch(
                    api_key=api_key, model=scoring["model"],
                    max_output_tokens=int(scoring["max_output_tokens"]),
                    system_prompt=cached_prefix,
                    per_ticker=per_ticker,
                )
                # Open the run record IMMEDIATELY with the batch_id and commit.
                audit_gates_early = dict(gates)
                audit_gates_early["selection_source"] = selection_source
                audit_gates_early["selection_count"] = (len(explicit_ticker_list)
                    if explicit_ticker_list else len(feed_rows))
                if selection_html_path:
                    audit_gates_early["selection_html_path"] = str(selection_html_path)
                    audit_gates_early["selection_pool_size"] = len(all_input_tickers or [])
                _early_conn = sqlite3.connect(llm_scores_db)
                try:
                    init_llm_scores_schema(_early_conn)
                    run_id = open_run(
                        _early_conn,
                        quarter=quarter, prompt_version=scoring["prompt_version"],
                        model=scoring["model"], mode=mode,
                        feed_size=len(feed_rows),
                        tier_a_count=counts[Tier.A], tier_b_count=counts[Tier.B], tier_c_count=counts[Tier.C],
                        gate_config=audit_gates_early, batch_id=batch_id,
                    )
                    _early_conn.commit()
                finally:
                    _early_conn.close()
                print(f"\nBatch submitted: batch_id={batch_id}  run_id={run_id}")
                print(f"  → if this script crashes during poll, recover with:")
                print(f"    python scripts/6_score.py --resume-run {run_id}")
                print()
                results = poll_and_collect_batch(
                    api_key=api_key, batch_id=batch_id,
                    poll_interval_s=int(scoring["dispatch"]["poll_interval_s"]),
                    timeout_s=int(scoring["dispatch"]["timeout_s"]),
                )
        else:
            results = []
        elapsed = time.time() - started

    # ────────────── Step 9 — parse + write ──────────────
    # D48 — record how the feed was assembled (audit-only, schema unchanged).
    audit_gates = dict(gates)
    audit_gates["selection_source"] = selection_source
    audit_gates["selection_count"] = len(explicit_ticker_list) if explicit_ticker_list else len(feed_rows)
    if selection_html_path:
        audit_gates["selection_html_path"] = str(selection_html_path)
        audit_gates["selection_pool_size"] = len(all_input_tickers or [])

    # D51 — per-result commit so a crash mid-loop never nukes already-written
    # results. Use an explicit connection with autocommit-style flow rather
    # than a single `with` block (which rolls back the entire batch on any
    # exception). Each result lands in its own transaction.
    scores_conn = sqlite3.connect(llm_scores_db)
    scores_conn.row_factory = sqlite3.Row
    try:
        init_llm_scores_schema(scores_conn)
        if run_id is None:                                 # first-time path (not resume)
            run_id = open_run(
                scores_conn,
                quarter=quarter, prompt_version=scoring["prompt_version"],
                model=scoring["model"], mode=mode,
                feed_size=len(feed_rows),
                tier_a_count=counts[Tier.A], tier_b_count=counts[Tier.B], tier_c_count=counts[Tier.C],
                gate_config=audit_gates, batch_id=batch_id,
            )
            scores_conn.commit()                           # persist run record immediately

        # Index per-ticker entries for hash + tier lookup.
        prep_index = {p["ticker"]: p for p in per_ticker}
        success_count = 0
        rate_limited_tickers: list[str] = []           # D51 — for end-of-run retry summary
        write_failed_tickers: list[str] = []
        token_totals = dict(
            input=0, output=0, cache_read=0, cache_create=0, search_calls=0, usd=0.0,
        )

        for res in results:
            prep = prep_index.get(res.ticker)
            # Track tokens + cost regardless of success — failed responses still bill.
            token_totals["input"] += res.input_tokens
            token_totals["output"] += res.output_tokens
            token_totals["cache_read"] += res.cache_read_tokens
            token_totals["cache_create"] += res.cache_creation_tokens
            token_totals["search_calls"] += res.web_search_calls
            res_usd = _usd_cost_per_call(
                pricing=scoring["pricing"],
                input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                cache_read_tokens=res.cache_read_tokens,
                cache_creation_tokens=res.cache_creation_tokens,
                web_search_calls=res.web_search_calls,
                batch_mode=(mode == "batch"),
            )
            token_totals["usd"] += res_usd

            try:
                if not res.success:
                    detail = res.error_detail or ""
                    if res.stop_reason:
                        detail = f"[stop_reason={res.stop_reason}] {detail}"
                    if "RateLimitError" in detail or "rate_limit" in detail.lower():
                        rate_limited_tickers.append(res.ticker)
                    write_error_row(
                        scores_conn, run_id=run_id, ticker=res.ticker, quarter=quarter,
                        horizon=None, error_kind=res.error_kind,
                        error_detail=detail, raw_text=res.raw_text,
                    )
                    scores_conn.commit()
                    continue
                try:
                    parsed = parse_full_score(res.raw_text)
                except ParseError as e:
                    detail = e.detail
                    if res.stop_reason:
                        detail = f"[stop_reason={res.stop_reason}] {detail}"
                    write_error_row(
                        scores_conn, run_id=run_id, ticker=res.ticker, quarter=quarter,
                        horizon=None, error_kind=e.kind, error_detail=detail,
                        raw_text=res.raw_text,
                    )
                    scores_conn.commit()
                    continue
                # D46 — primary score is anchored on current market price from the M5 pack.
                current_price_usd = float(
                    (pack_lookup.get(res.ticker, {}).get("market_snapshot") or {})
                    .get("last_close_usd") or 0.0
                )
                score = compute_ticker_score(
                    ticker=parsed.ticker,
                    current_price_usd=current_price_usd,
                    entry_price_ranges=parsed.entry_price_ranges,
                    near_term_3mo=parsed.near_term_3mo,
                    long_term_12mo=parsed.long_term_12mo,
                    tie_tolerance=float(scoring["rate"]["tie_tolerance"]),
                )
                write_full_score_rows(
                    scores_conn,
                    parsed=parsed, score=score, quarter=quarter,
                    prompt_version=scoring["prompt_version"], model=scoring["model"],
                    run_id=run_id,
                    raw_text=res.raw_text, response_id=res.response_id,
                    input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                    cache_read_tokens=res.cache_read_tokens,
                    cache_creation_tokens=res.cache_creation_tokens,
                    web_search_calls=res.web_search_calls, usd_cost=res_usd,
                    pack_source_rank_hash=prep["pack_source_rank_hash"] if prep else "",
                    source_tier="C",
                )
                for r in (res.server_tool_results or []):
                    url = r.get("url") or ""
                    if not url:
                        continue
                    upsert_web_search_cache_row(
                        scores_conn, url=url, ticker=res.ticker, quarter=quarter,
                        run_id=run_id, search_query=r.get("query") or "",
                        title=r.get("title") or "",
                        content=r.get("text") or "",
                        domain=_domain_of(url),
                        published_date=r.get("page_age"),
                    )
                scores_conn.commit()                       # D51 — persist this result before moving on
                success_count += 1
            except Exception as e:                         # never lose subsequent results to one bad write
                _LOG.exception("Failed to write result for %s; continuing", res.ticker)
                try: scores_conn.rollback()
                except sqlite3.Error: pass
                write_failed_tickers.append(res.ticker)
                # Best-effort error-row write (separate try so a write failure here
                # doesn't bubble out and stop the loop).
                try:
                    write_error_row(
                        scores_conn, run_id=run_id, ticker=res.ticker, quarter=quarter,
                        horizon=None, error_kind="db_write_error",
                        error_detail=str(e)[:500], raw_text=res.raw_text or "",
                    )
                    scores_conn.commit()
                except Exception:
                    pass

        # ────────────── Step 10 — final rankings + reports ──────────────
        # Per-run rows persist to final_rankings (audit trail).
        ranking_rows = _build_final_rankings_rows(scores_conn, quarter, run_id, pack_lookup)
        write_final_rankings(scores_conn, run_id=run_id, quarter=quarter, rows=ranking_rows)

        # D47 — composite score modifier. Compute + persist baseline
        # (model values, weights=1.0). The HTML re-applies user weights live
        # in JS without touching the DB.
        modifier_cfg = load_modifier_config()
        try:
            mod_summary = apply_modifiers_to_run(
                scores_conn, run_id=run_id, quarter=quarter, cfg=modifier_cfg,
            )
            if mod_summary:
                _LOG.info("Modifier applied to %d ticker(s) in run %d",
                          len(mod_summary), run_id)
        except Exception as e:
            _LOG.warning("apply_modifiers_to_run failed (run=%s): %s", run_id, e)

        # Build the rows that actually feed the rendered HTML/XLSX.
        # ALWAYS use the merged latest-per-ticker view across the whole
        # quarter, never just this run. Reasons:
        #   - --tickers / --ticker dispatches a small subset; the report
        #     should still show every ticker that has been scored in the
        #     quarter, not collapse to the dispatched subset (bug 2026-04-25).
        #   - --selection-from-html should preserve the unselected (Tier-A)
        #     tickers in the rendered view (D49 round-trip semantic).
        #   - Full-feed dispatch refreshes every ticker, so merged view
        #     and per-run view are equivalent — no harm.
        # Per-run rows still go to final_rankings (audit trail above);
        # only the rendering scope changes.
        scores_conn.row_factory = sqlite3.Row
        all_quarter_tickers = sorted({
            row[0] for row in scores_conn.execute(
                "SELECT DISTINCT ticker FROM llm_scores WHERE quarter=?",
                (quarter,),
            ).fetchall()
        })
        # Union with the input HTML's pool when --selection-from-html is in
        # play (so a brand-new ticker the user added to the HTML survives
        # even before its first score lands).
        if selection_html_path and all_input_tickers:
            all_quarter_tickers = sorted(set(all_quarter_tickers)
                                         | set(all_input_tickers))
        # Ensure pack_lookup covers all rendering tickers (loaded only the
        # dispatched subset earlier).
        missing_packs = [t for t in all_quarter_tickers if t not in pack_lookup]
        if missing_packs:
            pack_lookup.update(_load_packs_for_tickers(
                context_packs_db, quarter, missing_packs,
            ))
        rendering_rows = _build_merged_ranking_rows(
            scores_conn, quarter, run_id, pack_lookup,
            tickers=all_quarter_tickers,
        )

        list_price_total = _usd_cost_per_call(
            pricing=scoring["pricing"],
            input_tokens=token_totals["input"], output_tokens=token_totals["output"],
            cache_read_tokens=0, cache_creation_tokens=0,
            web_search_calls=token_totals["search_calls"],
            batch_mode=False,
        )
        close_run(
            scores_conn, run_id=run_id, wall_time_s=elapsed,
            input_tokens_total=token_totals["input"],
            output_tokens_total=token_totals["output"],
            cache_read_tokens_total=token_totals["cache_read"],
            cache_creation_tokens_total=token_totals["cache_create"],
            web_search_calls_total=token_totals["search_calls"],
            usd_cost_total=token_totals["usd"],
            usd_cost_list_price=list_price_total,
        )
        scores_conn.commit()

        # Reload llm_scores rows for the detail-panel response viewer.
        # Match rendering_rows scope: latest-per-ticker across the quarter.
        score_rows_for_viewer = _latest_score_rows(
            scores_conn, quarter, all_quarter_tickers,
        )
    finally:
        scores_conn.close()

    outputs_dir = PROJECT_ROOT / "Outputs"
    rank_html = outputs_dir / f"final_ranking_{quarter}.html"
    rank_xlsx = outputs_dir / f"final_ranking_{quarter}.xlsx"

    # D49 — selection sidecar JSON. The local HTTP server (6_serve_report.py)
    # reads/writes this file; the renderer also seeds it on every run so the
    # server has something to serve on first launch. The merge rule preserves
    # selections for tickers still in the pool, defaults new tickers to
    # selected, and silently drops tickers that are no longer in the pool.
    rank_json = selection_json_path(rank_html)
    new_pool = sorted({(r.get("ticker") or "") for r in rendering_rows
                       if r.get("ticker")})
    # Re-read the on-disk sidecar so we don't clobber a slider tweak the
    # server received between dispatch start and now.
    live_prior = load_selection_json(rank_json)
    sidecar_selection = merge_selection(new_pool=new_pool, prior=live_prior)
    # D50 — preserve the user's slider weights across pipeline runs.
    sidecar_weights = merge_modifier_weights(prior=live_prior)

    # Per-ticker model factors for the JS slider recompute (D50).
    with sqlite3.connect(llm_scores_db) as _scores_conn_for_factors:
        ticker_factors_export = collect_ticker_factors(
            _scores_conn_for_factors,
            quarter=quarter,
            tickers=new_pool,
            cfg=modifier_cfg,
        )

    write_selection_json(
        rank_json,
        quarter=quarter,
        all_tickers=new_pool,
        selected_tickers=sorted(sidecar_selection),
        modifier_weights=sidecar_weights,
    )

    # Merged single HTML: ranking table with arrow-expand LLM-response panels
    # per ticker, leftmost selection checkbox column (D48). The HTML's initial
    # checkbox state mirrors the sidecar JSON we just wrote, so even a
    # file:// load shows the right state (server only kicks in for auto-save).
    render_final_ranking_html(
        rendering_rows, rank_html,
        quarter=quarter, run_id=run_id,
        score_rows=score_rows_for_viewer,
        prior_selection=sidecar_selection,
        modifier_weights=sidecar_weights,
        ticker_factors=ticker_factors_export,
        modifier_cfg=modifier_cfg,
    )
    render_final_ranking_xlsx(rendering_rows, rank_xlsx, quarter=quarter, run_id=run_id)

    print()
    print("-" * 70)
    print(f"Module 6 run complete: run_id={run_id}, quarter={quarter}")
    print(f"  Calls dispatched:     {len(results)}")
    print(f"  Successful parses:    {success_count}")
    print(f"  Errors:               {len(results) - success_count}")
    print(f"  Wall time:            {elapsed:.1f}s")
    print(f"  Tokens (in / out):    {token_totals['input']:,} / {token_totals['output']:,}")
    print(f"  Cache (read / create):{token_totals['cache_read']:,} / {token_totals['cache_create']:,}")
    print(f"  Web searches:         {token_totals['search_calls']}")
    print(f"  USD cost (paid):      ${token_totals['usd']:,.2f}")
    print(f"  USD if list-price:    ${list_price_total:,.2f}")
    print(f"  Reports:              {rank_html}")
    print(f"                        {rank_xlsx}")
    print("-" * 70)
    # D51 — surface failure modes that need a follow-up retry.
    if rate_limited_tickers:
        print()
        print(f"  RATE-LIMITED ({len(rate_limited_tickers)} ticker(s) hit 429 / per-min cap):")
        print(f"    {','.join(rate_limited_tickers)}")
        print(f"  Retry just these (one-at-a-time, sync mode):")
        print(f"    python scripts/6_score.py --tickers {','.join(rate_limited_tickers)} "
              f"--mode sync --sync-concurrency 1 --yes -v")
        print(f"  Or wait until your tier upgrade kicks in and use --mode batch.")
    if write_failed_tickers:
        print()
        print(f"  DB-WRITE FAILED ({len(write_failed_tickers)} ticker(s); responses still in llm_errors):")
        print(f"    {','.join(write_failed_tickers)}")
        print(f"  These were billed but couldn't be written. Investigate llm_errors then retry:")
        print(f"    python scripts/6_score.py --tickers {','.join(write_failed_tickers)} "
              f"--mode sync --yes -v")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
