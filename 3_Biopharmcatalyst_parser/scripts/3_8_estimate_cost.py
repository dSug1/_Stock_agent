"""Module 8 — UNIFIED pre-flight cost estimator (D40, NO API call, NO spend).

Combines the formerly-separate M7 (hard-pass) and M8 (rescue) estimators
into one report. The unified Claude dispatcher (scripts/3_8_claude_dispatch.py)
fans out TWO parallel batches under the hood — one per prompt — so this
estimator prints two subtotals and one combined total that mirrors what
the dispatcher will actually bill.

Writes a self-contained HTML report at `Outputs/m8_cost_estimate.html`
the user can review before running the actual dispatcher.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py --tickers TCRX,RCKT
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py --force-refresh
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py --classes A,B
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

from database.db import DEFAULT_DB_PATH as BIOTECH_DB                       # noqa: E402
from module_6_5.fundamentals_db import DEFAULT_DB_PATH as FUNDAMENTALS_DB  # noqa: E402
from module_7 import (                                                     # noqa: E402
    EstimateInputs,
    db_connect as deep_dives_connect,
    default_config_path as default_m7_config_path,
    estimate_cost,
    group_candidates_by_drug,
    load_cacheable_prefix,
    load_module_7_config,
    partition_drug_groups_by_cache,
    render_cost_html,
)
from module_7.context_pack import (                                        # noqa: E402
    augment_pack_for_rescue,
    build_context_pack,
    fetch_hard_pass_candidates,
    fetch_rescue_candidates,
)
from module_8 import (                                                     # noqa: E402
    default_config_path as default_m8_config_path,
    load_module_8_config,
    load_rescue_prefix,
)


# ────────────────── shared gate application ──────────────────


def _apply_gates(
    candidates: list[dict],
    *,
    label: str,
    override_ack_gate: bool,
    one_drug_per_ticker: bool,
    override_coverage_gate: bool,
) -> list[dict]:
    """Apply ack-gate + --one-drug-per-ticker + coverage-gate the same way
    the dispatcher does. Returns the gated candidate list."""
    if not override_ack_gate:
        from module_7.gate import apply_ticker_gate, load_acknowledged_tickers
        ack = load_acknowledged_tickers()
        if ack:
            kept, dropped = apply_ticker_gate(candidates, ack)
            if dropped:
                dropped_tickers = sorted({d["ticker"] for d in dropped})
                print(f"  [{label}] ack-gate: {len(dropped)} catalyst(s) "
                      f"across {len(dropped_tickers)} ticker(s) skipped "
                      f"(use --override-ack-gate to ignore)")
            candidates = kept

    if one_drug_per_ticker and candidates:
        from module_7.gate import collapse_to_one_per_ticker
        kept, dropped = collapse_to_one_per_ticker(candidates)
        if dropped:
            print(f"  [{label}] --one-drug-per-ticker: dropped {len(dropped)} "
                  f"extra drug-rows ({len({d['ticker'] for d in dropped})} tickers)")
        candidates = kept

    if not override_coverage_gate and candidates:
        from module_8 import apply_coverage_gate, tickers_with_existing_dispatch
        covered = tickers_with_existing_dispatch()
        if covered:
            kept, dropped = apply_coverage_gate(candidates, covered)
            if dropped:
                print(f"  [{label}] coverage-gate: {len(dropped)} catalyst(s) "
                      f"across {len({d['ticker'] for d in dropped})} ticker(s) "
                      f"skipped (use --override-coverage-gate to ignore)")
            candidates = kept

    return candidates


# ────────────────── per-feed estimate ───────────────────────


def _estimate_feed(
    *,
    label: str,
    feed_kind: str,                   # "hard_pass" or "rescue"
    candidates: list[dict],
    biotech_db: Path,
    fundamentals_db: Path,
    cfg,                               # Module7Config or Module8Config
    cached_prefix: str,
    force_refresh: bool,
) -> tuple[dict, dict | None]:
    """Build packs → cache filter → cost estimate. Returns (stats, est)
    where est is None when nothing dispatches."""
    if not candidates:
        return ({
            "n_candidates": 0, "n_groups": 0, "n_dispatch": 0,
            "n_rows_to_populate": 0, "n_skipped": 0,
            "skip_reasons": {}, "snapshot_label": "",
            "total_usd": 0.0,
        }, None)

    packs: list[tuple[dict, dict]] = []
    for cand in candidates:
        pack = build_context_pack(
            biotech_db, fundamentals_db,
            snapshot_date=cand["snapshot_date"],
            ticker=cand["ticker"], drug=cand["drug"],
            nct_number=cand["nct_number"],
            next_catalyst_type=cand["next_catalyst_type"],
        )
        if pack is None:
            print(f"  [{label}] WARN: no catalyst_snapshots row for "
                  f"{cand['ticker']} {cand['drug']}; skipping")
            continue
        if feed_kind == "rescue":
            pack = augment_pack_for_rescue(pack, cand.get("rescue_class") or "?")
        packs.append((cand, pack))

    raw_candidates = [{**cand,
                       "catalyst_date_iso": cand.get("catalyst_date_iso")}
                      for cand, _ in packs]
    drug_groups = group_candidates_by_drug(raw_candidates)
    with deep_dives_connect() as dd_conn:
        groups_to_dispatch, groups_skipped = partition_drug_groups_by_cache(
            dd_conn, groups=drug_groups,
            current_prompt_version=cfg.prompt_version,
            force_refresh=force_refresh,
        )

    n_candidates = len(packs)
    n_groups = len(drug_groups)
    n_dispatch = len(groups_to_dispatch)
    n_skipped = len(groups_skipped)
    n_rows = sum(len(g["members"]) for g in groups_to_dispatch)
    skip_reasons: dict[str, int] = {}
    for g in groups_skipped:
        r = g["cache_lookup"].reason
        skip_reasons[r] = skip_reasons.get(r, 0) + 1
    snapshot_label = ",".join(sorted({cand["snapshot_date"] for cand, _ in packs}))

    print(f"  [{label}] candidates: {n_candidates}  "
          f"drug-groups: {n_groups}  to dispatch: {n_dispatch}  "
          f"cache-hit skipped: {n_skipped}")
    if n_candidates - n_groups > 0:
        print(f"  [{label}] D23 drug-dedup saved {n_candidates - n_groups} API call(s)")
    if n_skipped > 0:
        for r in sorted(skip_reasons):
            print(f"    skip reason={r}: {skip_reasons[r]}")

    stats = {
        "n_candidates": n_candidates, "n_groups": n_groups,
        "n_dispatch": n_dispatch, "n_rows_to_populate": n_rows,
        "n_skipped": n_skipped, "skip_reasons": skip_reasons,
        "snapshot_label": snapshot_label,
    }

    if n_dispatch == 0:
        stats["total_usd"] = 0.0
        return stats, None

    sample_packs = [json.dumps(p, default=str)
                    for _, p in packs[:5]] or [json.dumps({})]
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
    # Production scenario is cache+batch (scenarios[2]).
    stats["total_usd"] = est.scenarios[2].total_usd
    return stats, est


# ────────────────────────── main ───────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", default=None,
                        help="Comma-separated tickers (applies to both feeds)")
    parser.add_argument("--classes", default=None,
                        help="Comma-separated rescue classes to include (A,B,C). "
                             "Default: all. Applies to the rescue feed only.")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Estimate as if no cache hits — every group dispatches")
    parser.add_argument("--defined-only", action="store_true",
                        help="Hard-pass feed: restrict to catalysts with defined timing")
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB)
    parser.add_argument("--fundamentals-db", type=Path, default=FUNDAMENTALS_DB)
    parser.add_argument("--m7-config", type=Path, default=default_m7_config_path(),
                        help="Path to config/module_7.yaml")
    parser.add_argument("--m8-config", type=Path, default=default_m8_config_path(),
                        help="Path to config/module_8.yaml")
    parser.add_argument("--out", type=Path,
                        default=PROJECT_ROOT / "Outputs" / "m8_cost_estimate.html",
                        help="HTML output path (single combined report)")
    parser.add_argument("--override-ack-gate", action="store_true",
                        help="D38 — bypass the acknowledged-ticker gate (both feeds)")
    parser.add_argument("--one-drug-per-ticker", action="store_true",
                        help="D38 — mirror the dispatcher's same-named flag (both feeds)")
    parser.add_argument("--override-coverage-gate", action="store_true",
                        help="D39 — mirror the dispatcher's flag (both feeds)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    cfg_m7 = load_module_7_config(args.m7_config)
    cfg_m8 = load_module_8_config(args.m8_config)
    config_dir_m7 = args.m7_config.parent
    config_dir_m8 = args.m8_config.parent

    print(f"[3_8_estimate_cost] m7-config: {args.m7_config}  prompt_version: {cfg_m7.prompt_version}")
    print(f"[3_8_estimate_cost] m8-config: {args.m8_config}  prompt_version: {cfg_m8.prompt_version}")
    print(f"[3_8_estimate_cost] model: {cfg_m7.model}")
    print()

    explicit = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
                if args.tickers else None)
    classes = ([c.strip().upper() for c in args.classes.split(",") if c.strip()]
               if args.classes else None)

    # ── HARD-PASS feed ──────────────────────
    print("[3_8_estimate_cost] === HARD-PASS feed (m7-v2 prompt) ===")
    hp_candidates = fetch_hard_pass_candidates(args.biotech_db,
                                               explicit_tickers=explicit,
                                               defined_timing_only=args.defined_only)
    print(f"  hard-pass candidates (raw): {len(hp_candidates)}")
    hp_candidates = _apply_gates(
        hp_candidates, label="HP",
        override_ack_gate=args.override_ack_gate,
        one_drug_per_ticker=args.one_drug_per_ticker,
        override_coverage_gate=args.override_coverage_gate,
    )
    hp_prefix = load_cacheable_prefix(
        config_dir_m7 / Path(cfg_m7.system_prompt_path).name,
        config_dir_m7 / Path(cfg_m7.few_shot_examples_path).name,
    )
    hp_stats, hp_est = _estimate_feed(
        label="HP", feed_kind="hard_pass",
        candidates=hp_candidates,
        biotech_db=args.biotech_db, fundamentals_db=args.fundamentals_db,
        cfg=cfg_m7, cached_prefix=hp_prefix,
        force_refresh=args.force_refresh,
    )
    print()

    # ── RESCUE feed ─────────────────────────
    print("[3_8_estimate_cost] === RESCUE feed (m8-rescue-v1 prompt) ===")
    res_candidates = fetch_rescue_candidates(args.biotech_db,
                                             explicit_tickers=explicit,
                                             classes=classes)
    print(f"  rescue candidates (raw): {len(res_candidates)}")
    if not res_candidates:
        print("  → run scripts/3_7_compute_eligibility.py first to populate "
              "rescued/rescue_class.")
    res_candidates = _apply_gates(
        res_candidates, label="RES",
        override_ack_gate=args.override_ack_gate,
        one_drug_per_ticker=args.one_drug_per_ticker,
        override_coverage_gate=args.override_coverage_gate,
    )
    by_class: dict[str, int] = {}
    for c in res_candidates:
        k = c.get("rescue_class") or "?"
        by_class[k] = by_class.get(k, 0) + 1
    for k in sorted(by_class):
        print(f"    rescue_class={k}: {by_class[k]}")
    res_prefix = load_rescue_prefix(
        rescue_preamble_path=config_dir_m8 / Path(cfg_m8.system_prompt_path).name,
        m7_system_prompt_path=config_dir_m7 / "module_7_system_prompt.md",
        m7_few_shots_path=config_dir_m8 / Path(cfg_m8.few_shot_examples_path).name,
    )
    res_stats, res_est = _estimate_feed(
        label="RES", feed_kind="rescue",
        candidates=res_candidates,
        biotech_db=args.biotech_db, fundamentals_db=args.fundamentals_db,
        cfg=cfg_m8, cached_prefix=res_prefix,
        force_refresh=args.force_refresh,
    )
    print()

    # ── HTML report (use HP estimate when present, else RES) ──
    primary_est = hp_est or res_est
    if primary_est is not None:
        gates = {
            "n_hard_pass_candidates":   hp_stats["n_candidates"],
            "n_rescue_candidates":      res_stats["n_candidates"],
            "rescue_class_breakdown":   by_class,
            "n_hard_pass_drug_groups":  hp_stats["n_groups"],
            "n_rescue_drug_groups":     res_stats["n_groups"],
            "n_hard_pass_to_dispatch":  hp_stats["n_dispatch"],
            "n_rescue_to_dispatch":     res_stats["n_dispatch"],
            "n_skipped_cache_hits":     hp_stats["n_skipped"] + res_stats["n_skipped"],
            "n_to_dispatch_api_calls":  hp_stats["n_dispatch"] + res_stats["n_dispatch"],
            "n_rows_to_populate":       hp_stats["n_rows_to_populate"] + res_stats["n_rows_to_populate"],
            "explicit_tickers":         explicit or "all (rolling-view)",
            "classes_filter":           classes or "A,B,C",
            "force_refresh":            args.force_refresh,
            "cost_ceiling_usd":         max(cfg_m7.cost_ceiling_usd, cfg_m8.cost_ceiling_usd),
            "prompt_version":           f"{cfg_m7.prompt_version} + {cfg_m8.prompt_version}",
            "model":                    cfg_m7.model,
            "max_output_tokens":        cfg_m7.max_output_tokens,
            "max_uses_web_search":      cfg_m7.web_search.max_uses,
            "hard_pass_subtotal_usd":   hp_stats["total_usd"],
            "rescue_subtotal_usd":      res_stats["total_usd"],
            "combined_total_usd":       hp_stats["total_usd"] + res_stats["total_usd"],
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        render_cost_html(primary_est, gates, args.out)

    # ── Console summary ─────────────────────
    n_total_dispatch = hp_stats["n_dispatch"] + res_stats["n_dispatch"]
    combined = hp_stats["total_usd"] + res_stats["total_usd"]
    ceiling = max(cfg_m7.cost_ceiling_usd, cfg_m8.cost_ceiling_usd)

    print("-" * 70)
    print(f"Module 8 — UNIFIED cost estimate (D40):")
    print(f"  HARD-PASS:  {hp_stats['n_dispatch']:>3} dispatch(es)  → ${hp_stats['total_usd']:>10.2f}")
    print(f"  RESCUE:     {res_stats['n_dispatch']:>3} dispatch(es)  → ${res_stats['total_usd']:>10.2f}")
    print(f"  COMBINED:   {n_total_dispatch:>3} dispatch(es)  → ${combined:>10.2f}")
    print(f"  cost_ceiling_usd (max of m7/m8 cfg): ${ceiling:.2f}")
    status = "WITHIN budget" if combined <= ceiling else "ABOVE budget — dispatch will refuse"
    print(f"  → {status}")
    if primary_est is not None:
        print(f"  → HTML report: {args.out}")
    print("-" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
