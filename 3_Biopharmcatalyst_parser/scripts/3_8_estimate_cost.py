"""Module 8 — pre-flight cost estimator for the rescue feed (NO spend).

Mirrors scripts/3_7_estimate_cost.py but reads the rolling-view RESCUE
feed (catalyst_scores.rescued = 1) and uses the M8 prompt prefix +
prompt_version_label so the cache check matches what the dispatcher will
actually see.

Writes a self-contained HTML report at `Outputs/m8_cost_estimate.html`.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py --tickers EVMN,SLN
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_estimate_cost.py --classes A
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
from module_6_5.fundamentals_db import DEFAULT_DB_PATH as FUNDAMENTALS_DB   # noqa: E402
from module_7 import (                                                      # noqa: E402
    EstimateInputs,
    db_connect as deep_dives_connect,
    estimate_cost,
    group_candidates_by_drug,
    partition_drug_groups_by_cache,
    render_cost_html,
)
from module_7.context_pack import (                                         # noqa: E402
    augment_pack_for_rescue,
    build_context_pack,
    fetch_rescue_candidates,
)
from module_8 import default_config_path, load_module_8_config, load_rescue_prefix  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", default=None,
                        help="Comma-separated tickers (still requires rescued=1)")
    parser.add_argument("--classes", default=None,
                        help="Comma-separated rescue classes to include (A,B,C). Default: all.")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Estimate as if no cache hits — every group dispatches")
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB)
    parser.add_argument("--fundamentals-db", type=Path, default=FUNDAMENTALS_DB)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--out", type=Path,
                        default=PROJECT_ROOT / "Outputs" / "m8_cost_estimate.html",
                        help="HTML output path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )

    cfg = load_module_8_config(args.config)
    print(f"[3_8_estimate_cost] config: {args.config}")
    print(f"[3_8_estimate_cost] prompt_version: {cfg.prompt_version}")

    explicit = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
                if args.tickers else None)
    classes = ([c.strip().upper() for c in args.classes.split(",") if c.strip()]
               if args.classes else None)

    candidates = fetch_rescue_candidates(args.biotech_db,
                                         explicit_tickers=explicit,
                                         classes=classes)
    if not candidates:
        print("[3_8_estimate_cost] no rescue candidates in feed; nothing to estimate.")
        print("  → run scripts/3_8_compute_rescue.py first to populate rescued/rescue_class.")
        return 0
    print(f"[3_8_estimate_cost] rescue candidates: {len(candidates)}")
    by_class: dict[str, int] = {}
    for c in candidates:
        k = c.get("rescue_class") or "?"
        by_class[k] = by_class.get(k, 0) + 1
    for k in sorted(by_class):
        print(f"    rescue_class={k}: {by_class[k]}")

    # Build packs (with rescue augmentation).
    packs: list[tuple[dict, dict]] = []
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
        pack = augment_pack_for_rescue(pack, cand.get("rescue_class") or "?")
        packs.append((cand, pack))

    # D23 drug-grouping + cache filter (same machinery as M7).
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
    print(f"[3_8_estimate_cost] candidates: {n_candidates}  "
          f"drug-groups: {n_groups}  to dispatch: {n_dispatch}  "
          f"cache-hit skipped: {n_skipped}")
    if dedup_saved > 0:
        print(f"[3_8_estimate_cost] D23 drug-dedup: {dedup_saved} fewer API call(s) "
              f"({n_groups} groups across {n_candidates} catalysts)")
    if args.force_refresh:
        print(f"[3_8_estimate_cost] --force-refresh: all drug-groups dispatch")
    elif n_skipped > 0:
        by_reason: dict[str, int] = {}
        for g in groups_skipped:
            r = g["cache_lookup"].reason
            by_reason[r] = by_reason.get(r, 0) + 1
        for r, n in sorted(by_reason.items()):
            print(f"  cache hit reason={r}: {n}")
    if n_dispatch == 0:
        print("[3_8_estimate_cost] nothing to dispatch; $0 estimated.")
        return 0

    config_dir = args.config.parent
    cached_prefix = load_rescue_prefix(
        rescue_preamble_path=config_dir / Path(cfg.system_prompt_path).name,
        m7_system_prompt_path=config_dir / "module_7_system_prompt.md",
        m7_few_shots_path=config_dir / Path(cfg.few_shot_examples_path).name,
    )
    sample_packs = [json.dumps(p, default=str)
                    for _, p in packs[:5]] or [json.dumps({})]
    snapshot_label = ",".join(sorted({cand["snapshot_date"] for cand, _ in packs}))

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

    gates = {
        "n_rescue_candidates":    len(candidates),
        "rescue_class_breakdown": by_class,
        "n_drug_groups":          n_groups,
        "n_skipped_cache_hits":   n_skipped,
        "n_to_dispatch_api_calls": n_dispatch,
        "n_rows_to_populate":     n_rows,
        "explicit_tickers":       explicit or "all rescued",
        "classes_filter":         classes or "A,B,C",
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
    print(f"Module 8 — estimated cost for {n_dispatch} rescue dispatch(es):")
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
