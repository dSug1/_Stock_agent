"""Module 7 — pre-flight cost estimator (NO API call, NO spend).

Reads the rolling-view hard-pass feed (or an explicit --tickers list),
builds packs for each candidate, applies the catalyst-identity cache
filter, and reports the estimated Anthropic cost of dispatching the
remaining (cache-miss) feed.

Writes a self-contained HTML report at `Outputs/m7_cost_estimate.html`
the user can review before running the actual `3_7_deep_dive.py`.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_estimate_cost.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_estimate_cost.py --tickers TCRX,RCKT
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_7_estimate_cost.py --force-refresh
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6_5.fundamentals_db import DEFAULT_DB_PATH as FUNDAMENTALS_DB  # noqa: E402
from module_7 import (                                                     # noqa: E402
    EstimateInputs,
    db_connect as deep_dives_connect,
    default_config_path,
    estimate_cost,
    group_candidates_by_drug,
    load_cacheable_prefix,
    load_module_7_config,
    partition_drug_groups_by_cache,
    render_cost_html,
)
from module_7.context_pack import build_context_pack, fetch_hard_pass_candidates  # noqa: E402

# database default biotech.db path
from database.db import DEFAULT_DB_PATH as BIOTECH_DB                       # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    feed = parser.add_mutually_exclusive_group()
    feed.add_argument("--tickers", default=None,
                      help="Comma-separated tickers (still requires hard_pass=1)")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Estimate as if no cache hits — every candidate dispatches")
    parser.add_argument("--defined-only", action="store_true",
                        help="Restrict to catalysts with defined timing (HTML 'Defined timing' tab)")
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB)
    parser.add_argument("--fundamentals-db", type=Path, default=FUNDAMENTALS_DB)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--out", type=Path,
                        default=PROJECT_ROOT / "Outputs" / "m7_cost_estimate.html",
                        help="HTML output path")
    parser.add_argument("--override-ack-gate", action="store_true",
                        help="D38 — bypass the acknowledged-ticker gate when "
                             "estimating; useful to see the un-gated cost.")
    parser.add_argument("--one-drug-per-ticker", action="store_true",
                        help="D38 — mirror the dispatcher's same-named flag so "
                             "the cost preview reflects what would actually run.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    cfg = load_module_7_config(args.config)
    print(f"[3_7_estimate_cost] config: {args.config}")
    print(f"[3_7_estimate_cost] prompt_version: {cfg.prompt_version}")

    explicit = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
                if args.tickers else None)
    candidates = fetch_hard_pass_candidates(args.biotech_db,
                                            explicit_tickers=explicit,
                                            defined_timing_only=args.defined_only)
    if not candidates:
        print("[3_7_estimate_cost] no hard-pass candidates in feed; nothing to estimate.")
        return 0
    print(f"[3_7_estimate_cost] hard-pass candidates: {len(candidates)}")

    # D38 — apply the same pre-dispatch ack-gate so the estimate
    # reflects what would actually dispatch.
    if not args.override_ack_gate:
        from module_7.gate import apply_ticker_gate, load_acknowledged_tickers
        ack = load_acknowledged_tickers()
        if ack:
            kept, dropped = apply_ticker_gate(candidates, ack)
            if dropped:
                dropped_tickers = sorted({d["ticker"] for d in dropped})
                print(f"[3_7_estimate_cost] ack-gate: {len(dropped)} catalyst(s) "
                      f"across {len(dropped_tickers)} ticker(s) would be skipped "
                      f"(use --override-ack-gate to ignore)")
            candidates = kept
            if not candidates:
                print("[3_7_estimate_cost] all candidates gated out; nothing to estimate.")
                return 0

    if args.one_drug_per_ticker:
        from module_7.gate import collapse_to_one_per_ticker
        kept, dropped = collapse_to_one_per_ticker(candidates)
        if dropped:
            print(f"[3_7_estimate_cost] --one-drug-per-ticker: dropped {len(dropped)} "
                  f"extra drug-rows ({len({d['ticker'] for d in dropped})} tickers)")
        candidates = kept

    # Build packs (cheap — pure local SQLite reads).
    packs: list[tuple[dict, dict]] = []      # (candidate, pack)
    for cand in candidates:
        pack = build_context_pack(
            args.biotech_db, args.fundamentals_db,
            snapshot_date=cand["snapshot_date"],
            ticker=cand["ticker"], drug=cand["drug"],
            nct_number=cand["nct_number"],
            next_catalyst_type=cand["next_catalyst_type"],
        )
        if pack is None:
            print(f"  WARN: no catalyst_snapshots row for {cand['ticker']} {cand['drug']}; skipping")
            continue
        packs.append((cand, pack))

    # D23 — group by (ticker, drug), then drug-level cache filter.
    raw_candidates = [{**cand,
                       "catalyst_date_iso": cand.get("catalyst_date_iso")}
                      for cand, _ in packs]
    drug_groups = group_candidates_by_drug(raw_candidates)
    with deep_dives_connect() as dd_conn:
        groups_to_dispatch, groups_skipped = partition_drug_groups_by_cache(
            dd_conn,
            groups=drug_groups,
            current_prompt_version=cfg.prompt_version,
            force_refresh=args.force_refresh,
        )

    n_candidates = len(packs)
    n_groups = len(drug_groups)
    n_dispatch = len(groups_to_dispatch)
    n_skipped = len(groups_skipped)
    n_rows = sum(len(g["members"]) for g in groups_to_dispatch)
    dedup_saved = n_candidates - n_groups
    print(f"[3_7_estimate_cost] candidates: {n_candidates}  "
          f"drug-groups: {n_groups}  "
          f"to dispatch: {n_dispatch}  "
          f"cache-hit skipped: {n_skipped}")
    if dedup_saved > 0:
        print(f"[3_7_estimate_cost] D23 drug-dedup: {dedup_saved} fewer API call(s) "
              f"than catalyst-count would suggest ({n_groups} groups across {n_candidates} catalysts)")
    if args.force_refresh:
        print(f"[3_7_estimate_cost] --force-refresh: all drug-groups dispatch")
    elif n_skipped > 0:
        by_reason: dict[str, int] = {}
        for g in groups_skipped:
            r = g["cache_lookup"].reason
            by_reason[r] = by_reason.get(r, 0) + 1
        for r, n in sorted(by_reason.items()):
            print(f"  cache hit reason={r}: {n}")
    if n_dispatch == 0:
        print("[3_7_estimate_cost] nothing to dispatch; $0 estimated.")
        return 0

    # Build the cost-estimator inputs.
    config_dir = args.config.parent
    cached_prefix = load_cacheable_prefix(
        config_dir / Path(cfg.system_prompt_path).name,
        config_dir / Path(cfg.few_shot_examples_path).name,
    )
    # Sample 5 packs for token estimation.
    sample_packs = [json.dumps(p, default=str)
                    for _, p in packs[:5]] or [json.dumps({})]
    snapshot_label = ",".join(
        sorted({cand["snapshot_date"] for cand, _ in packs})
    )

    est = estimate_cost(EstimateInputs(
        snapshot_date=snapshot_label,
        prompt_version=cfg.prompt_version,
        model=cfg.model,
        cached_prefix_text=cached_prefix,
        pack_jsons_sample=sample_packs,
        n_tickers=n_dispatch,
        max_output_tokens=cfg.max_output_tokens,
        max_uses_web_search=cfg.web_search.max_uses,
        pricing=cfg.pricing.model_dump(),
        sync_concurrency=cfg.dispatch.sync_concurrency,
    ))

    # Render the HTML report.
    gates = {
        "n_hard_pass_candidates": len(candidates),
        "n_drug_groups":          n_groups,
        "n_skipped_cache_hits":   n_skipped,
        "n_to_dispatch_api_calls": n_dispatch,
        "n_rows_to_populate":     n_rows,
        "explicit_tickers":       explicit or "rolling-view hard-pass",
        "force_refresh":          args.force_refresh,
        "cost_ceiling_usd":       cfg.cost_ceiling_usd,
        "prompt_version":         cfg.prompt_version,
        "model":                  cfg.model,
        "max_output_tokens":      cfg.max_output_tokens,
        "max_uses_web_search":    cfg.web_search.max_uses,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    render_cost_html(est, gates, args.out)

    print()
    print("-" * 70)
    print(f"Module 7 — estimated cost for {n_dispatch} dispatch(es):")
    for s in est.scenarios:
        print(f"  {s.name:13s} ${s.total_usd:>10,.2f}  "
              f"(tokens_in={s.input_tokens_total:>10,} cache_read={s.cache_read_tokens_total:>10,})")
    print(f"  cost_ceiling_usd in config: ${cfg.cost_ceiling_usd:.2f}")
    ceil_status = ("WITHIN budget" if est.production_total_usd <= cfg.cost_ceiling_usd
                   else "ABOVE budget - dispatch will refuse")
    print(f"  -> production scenario (cache+batch): ${est.production_total_usd:.2f} - {ceil_status}")
    print(f"  -> HTML report: {args.out}")
    print("-" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
