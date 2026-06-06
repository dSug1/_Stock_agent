"""Module 8 — UNIFIED Claude API dispatcher (D40).

The ONLY script that actually spends money on Anthropic in the new D40
pipeline. Combines the formerly-separate M7 (hard-pass) and M8 (rescue)
dispatchers into one billed step:

  • TWO parallel Anthropic batches under the hood (one per prompt):
        batch_hp  → hard-pass feed → m7-v2 prompt prefix
        batch_res → rescue feed     → m8-rescue-v1 prompt prefix
  • ONE combined cost estimate printed (HP subtotal + RES subtotal + total)
  • ONE mandatory [y/N] gate (only `--yes` bypasses)
  • Both batches submit-and-poll on a `ThreadPoolExecutor(max_workers=2)`
    so wall time = max(hp_wall, res_wall) rather than sum.

Each batch keeps its own `deep_dive_runs.run_id` and its own prompt_version
so the existing identity-cache and run-history machinery is unchanged.

Usage (from 3_Biopharmcatalyst_parser/):
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --yes
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --tickers TCRX,SLN
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --classes A,B
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --resume-run N
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --skip-rescue
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_8_claude_dispatch.py --skip-hard-pass

Spec: spec/decisions.md § D40.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib as _hashlib
import json
import logging
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
REPO_ROOT = PROJECT_ROOT.parent
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import DEFAULT_DB_PATH as BIOTECH_DB                       # noqa: E402
from module_6_5.fundamentals_db import DEFAULT_DB_PATH as FUNDAMENTALS_DB   # noqa: E402
from module_7 import (                                                      # noqa: E402
    EstimateInputs,
    ParseError,
    close_run,
    compute_catalyst_signature,
    compute_expectancy,
    db_connect as deep_dives_connect,
    default_config_path as default_m7_config_path,
    dispatch_sync,
    estimate_cost,
    group_candidates_by_drug,
    load_cacheable_prefix,
    load_module_7_config,
    open_run,
    parse_deep_dive,
    partition_drug_groups_by_cache,
    poll_and_collect_batch,
    submit_batch,
    upsert_deep_dive_row,
    upsert_web_search_cache_row,
    weeks_between,
    write_error_row,
)
from module_7.context_pack import (                                         # noqa: E402
    augment_pack_for_rescue,
    augment_pack_with_drug_siblings,
    build_context_pack,
    fetch_hard_pass_candidates,
    fetch_rescue_candidates,
)
from module_7.dispatch import (                                             # noqa: E402
    build_user_message,
    build_web_search_tool_def,
    load_allowed_domains,
)
from module_7.live_price import get_live_prices                             # noqa: E402
from module_8 import (                                                      # noqa: E402
    default_config_path as default_m8_config_path,
    load_module_8_config,
    load_rescue_prefix,
)

_LOG = logging.getLogger("3_8_claude_dispatch")


# ───────────────────── shared helpers (from old M7/M8) ─────────


def _usd_cost_per_call(
    *, pricing: dict, input_tokens: int, output_tokens: int,
    cache_read_tokens: int, cache_creation_tokens: int,
    web_search_calls: int, batch_mode: bool,
) -> float:
    in_per_m = float(pricing["input_per_mtok"])
    out_per_m = float(pricing["output_per_mtok"])
    cr_mult = float(pricing["cache_read_multiplier"])
    cc_mult = float(pricing["cache_creation_multiplier"])
    batch_disc = float(pricing["batch_discount"]) if batch_mode else 1.0
    search_per_1k = float(pricing["web_search_per_1k"])
    calib = float(pricing.get("cost_calibration_factor", 1.0))
    token_cost = (
        (input_tokens / 1_000_000.0) * in_per_m
        + (cache_read_tokens / 1_000_000.0) * in_per_m * cr_mult
        + (cache_creation_tokens / 1_000_000.0) * in_per_m * cc_mult
        + (output_tokens / 1_000_000.0) * out_per_m
    ) * batch_disc
    search_fee = (web_search_calls / 1000.0) * search_per_1k
    return (token_cost + search_fee) * calib


def _domain_of(url: str) -> str:
    if not url:
        return ""
    s = url.split("//", 1)[-1]
    return s.split("/", 1)[0].lower()


def _drug_short_id(drug: str) -> str:
    return _hashlib.sha256(drug.encode("utf-8")).hexdigest()[:8]


# ───────────────────── per-feed payload (dataclass) ────────


@dataclass
class FeedPayload:
    """Everything a single batch needs to dispatch + writeback."""
    kind: str                                 # "hard_pass" | "rescue"
    cfg: object                                # Module7Config | Module8Config
    config_dir: Path
    cached_prefix: str
    candidates: list[dict] = field(default_factory=list)
    candidate_packs: dict = field(default_factory=dict)   # (t,d,n,t) → pack
    drug_groups: list[dict] = field(default_factory=list)
    groups_to_dispatch: list[dict] = field(default_factory=list)
    groups_skipped: list[dict] = field(default_factory=list)
    per_ticker: list[dict] = field(default_factory=list)
    request_index: dict = field(default_factory=dict)
    snapshot_label: str = ""
    est_total: float = 0.0
    n_dispatch: int = 0
    n_groups_total: int = 0
    n_rows_to_write: int = 0


# ───────────── per-group payload (was _prepare_per_group) ───


def _prepare_per_group(
    drug_groups: list[dict],
    candidate_packs: dict,
    *,
    config_dir: Path,
    domains_path: str,
    max_uses: int,
    include_rescue_class: bool,
) -> list[dict]:
    allowed_domains = load_allowed_domains(str(config_dir / Path(domains_path).name))
    tool_def = build_web_search_tool_def(allowed_domains, max_uses)
    out = []
    for g in drug_groups:
        anchor = g["anchor"]
        anchor_pack = candidate_packs[(
            anchor["ticker"], anchor["drug"],
            anchor["nct_number"], anchor["next_catalyst_type"],
        )]
        pack = augment_pack_with_drug_siblings(anchor_pack, g["members"])
        custom_id = f"{anchor['ticker']}__{_drug_short_id(anchor['drug'])}"
        entry = {
            "ticker":             custom_id,
            "actual_ticker":      anchor["ticker"],
            "drug":               anchor["drug"],
            "snapshot_date":      anchor["snapshot_date"],
            "anchor_nct_number":  anchor["nct_number"],
            "anchor_next_catalyst_type": anchor["next_catalyst_type"],
            "members":            g["members"],
            "drug_signature":     g["drug_signature"],
            "catalyst_signature": compute_catalyst_signature(
                drug=anchor["drug"], stage=anchor.get("stage"),
                next_catalyst_type=anchor["next_catalyst_type"],
                catalyst_date_iso=anchor.get("catalyst_date_iso"),
            ),
            "pack":               pack,
            "user_message":       build_user_message(pack),
            "web_search_tool":    tool_def,
        }
        if include_rescue_class:
            entry["rescue_class"] = anchor.get("rescue_class")
        out.append(entry)
    return out


# ────────────────── feed-prep (gates → packs → cache filter → estimate) ─


def _apply_gates(
    candidates: list[dict],
    *, label: str, args,
) -> list[dict]:
    if not args.override_ack_gate:
        from module_7.gate import apply_ticker_gate, load_acknowledged_tickers
        ack = load_acknowledged_tickers()
        if ack:
            kept, dropped = apply_ticker_gate(candidates, ack)
            if dropped:
                dropped_tickers = sorted({d["ticker"] for d in dropped})
                print(f"  [{label}] ack-gate: dropped {len(dropped)} catalyst(s) "
                      f"across {len(dropped_tickers)} reviewed ticker(s) — "
                      f"{', '.join(dropped_tickers[:10])}"
                      + (f", ... +{len(dropped_tickers)-10} more" if len(dropped_tickers) > 10 else ""))
            candidates = kept

    if args.one_drug_per_ticker and candidates:
        from module_7.gate import collapse_to_one_per_ticker
        kept, dropped = collapse_to_one_per_ticker(candidates)
        if dropped:
            print(f"  [{label}] --one-drug-per-ticker: dropped {len(dropped)} "
                  f"extra drug-rows ({len({d['ticker'] for d in dropped})} tickers)")
        candidates = kept

    if not args.override_coverage_gate and candidates:
        from module_8 import apply_coverage_gate, tickers_with_existing_dispatch
        covered = tickers_with_existing_dispatch()
        if covered:
            kept, dropped = apply_coverage_gate(candidates, covered)
            if dropped:
                dropped_tickers = sorted({d["ticker"] for d in dropped})
                print(f"  [{label}] coverage-gate: dropped {len(dropped)} catalyst(s) "
                      f"across {len(dropped_tickers)} ticker(s) with prior deep_dive — "
                      f"{', '.join(dropped_tickers[:10])}"
                      + (f", ... +{len(dropped_tickers)-10} more" if len(dropped_tickers) > 10 else ""))
            candidates = kept

    return candidates


def _prepare_feed(
    *,
    kind: str,                            # "hard_pass" | "rescue"
    label: str,                           # "HP" | "RES"
    cfg, config_dir: Path, cached_prefix: str,
    candidates: list[dict],
    biotech_db: Path, fundamentals_db: Path,
    live_prices: dict,                    # ticker → LivePrice
    force_refresh: bool, mode_label: str,
) -> FeedPayload:
    """Run gates → packs → drug-grouping → cache filter → cost estimate
    for ONE feed. Returns the assembled FeedPayload."""
    payload = FeedPayload(kind=kind, cfg=cfg, config_dir=config_dir,
                          cached_prefix=cached_prefix)
    payload.candidates = candidates
    if not candidates:
        return payload

    # Live-price overlay + rescue augmentation
    candidates_with_packs: list[tuple[dict, dict]] = []
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
        lp = live_prices.get(cand["ticker"])
        if lp and lp.price_usd:
            pack["market_snapshot"]["last_price_usd"] = lp.price_usd
            pack["market_snapshot"]["last_price_as_of"] = lp.fetched_at_utc
            pack["market_snapshot"]["price_source"] = "yfinance-live (D25)"
            basic = pack["market_snapshot"].get("fully_diluted_shares_count") \
                    or pack["market_snapshot"].get("basic_shares_count")
            if basic:
                pack["market_snapshot"]["market_cap_fdsc_usd"] = float(basic) * float(lp.price_usd)
        if kind == "rescue":
            pack = augment_pack_for_rescue(pack, cand.get("rescue_class") or "?")
        candidates_with_packs.append((cand, pack))

    candidate_packs = {
        (c["ticker"], c["drug"], c["nct_number"], c["next_catalyst_type"]): pack
        for c, pack in candidates_with_packs
    }
    raw_candidates = [{**cand,
                       "catalyst_date_iso": cand.get("catalyst_date_iso")}
                      for cand, _ in candidates_with_packs]
    drug_groups = group_candidates_by_drug(raw_candidates)

    if kind == "rescue":
        rescue_by_pk = {(c["ticker"], c["drug"], c["nct_number"], c["next_catalyst_type"]):
                        c.get("rescue_class") for c in raw_candidates}
        for g in drug_groups:
            a = g["anchor"]
            g["anchor"]["rescue_class"] = rescue_by_pk.get(
                (a["ticker"], a["drug"], a["nct_number"], a["next_catalyst_type"]))

    with deep_dives_connect() as dd_conn:
        groups_to_dispatch, groups_skipped = partition_drug_groups_by_cache(
            dd_conn, groups=drug_groups,
            current_prompt_version=cfg.prompt_version,
            force_refresh=force_refresh,
        )

    n_candidates = len(candidates_with_packs)
    n_groups_total = len(drug_groups)
    n_dispatch = len(groups_to_dispatch)
    n_skipped = len(groups_skipped)
    n_rows = sum(len(g["members"]) for g in groups_to_dispatch)
    print(f"  [{label}] candidates: {n_candidates}  "
          f"drug-groups: {n_groups_total}  to dispatch: {n_dispatch}  "
          f"will populate {n_rows} catalyst row(s);  "
          f"cache-hit skipped: {n_skipped}")
    if n_skipped > 0:
        by_reason: dict[str, int] = {}
        for g in groups_skipped:
            r = g["cache_lookup"].reason
            by_reason[r] = by_reason.get(r, 0) + 1
        for r, n in sorted(by_reason.items()):
            print(f"    skip reason={r}: {n}")
    dedup_saved = n_candidates - n_groups_total
    if dedup_saved > 0:
        print(f"  [{label}] D23 drug-dedup saved {dedup_saved} API call(s) "
              f"({n_groups_total} groups across {n_candidates} catalysts)")

    payload.candidate_packs = candidate_packs
    payload.drug_groups = drug_groups
    payload.groups_to_dispatch = groups_to_dispatch
    payload.groups_skipped = groups_skipped
    payload.n_dispatch = n_dispatch
    payload.n_groups_total = n_groups_total
    payload.n_rows_to_write = n_rows
    payload.snapshot_label = ",".join(sorted({g["anchor"]["snapshot_date"]
                                              for g in (groups_to_dispatch or drug_groups)}))

    if n_dispatch == 0:
        return payload

    payload.per_ticker = _prepare_per_group(
        groups_to_dispatch, candidate_packs,
        config_dir=config_dir,
        domains_path=cfg.web_search.domains_path,
        max_uses=cfg.web_search.max_uses,
        include_rescue_class=(kind == "rescue"),
    )

    # request_index for --resume-run recovery.
    payload.request_index = {
        p["ticker"]: {
            "actual_ticker":             p["actual_ticker"],
            "drug":                      p["drug"],
            "snapshot_date":             p["snapshot_date"],
            "anchor_nct_number":         p["anchor_nct_number"],
            "anchor_next_catalyst_type": p["anchor_next_catalyst_type"],
            "drug_signature":            p["drug_signature"],
            **({"rescue_class": p.get("rescue_class")} if kind == "rescue" else {}),
            "members": [
                {
                    "ticker":             m["ticker"],
                    "snapshot_date":      m.get("snapshot_date"),
                    "drug":               m["drug"],
                    "nct_number":         m["nct_number"],
                    "next_catalyst_type": m["next_catalyst_type"],
                    "stage":              m.get("stage"),
                    "catalyst_date_iso":  m.get("catalyst_date_iso"),
                    **({"rescue_class": m.get("rescue_class")} if kind == "rescue" else {}),
                    "insider_score":      m.get("insider_score"),
                    "momentum_score":     m.get("momentum_score"),
                    "fund_accumulation_score": m.get("fund_accumulation_score"),
                }
                for m in p["members"]
            ],
        }
        for p in payload.per_ticker
    }

    # Cost estimate
    sample_anchor_packs = [
        candidate_packs[(g["anchor"]["ticker"], g["anchor"]["drug"],
                         g["anchor"]["nct_number"], g["anchor"]["next_catalyst_type"])]
        for g in groups_to_dispatch[:5]
    ]
    sample_packs = [json.dumps(p, default=str)
                    for p in sample_anchor_packs] or [json.dumps({})]
    est = estimate_cost(EstimateInputs(
        snapshot_date=payload.snapshot_label,
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
    payload.est_total = (est.scenarios[2].total_usd if mode_label == "cache+batch"
                         else est.scenarios[1].total_usd)
    return payload


# ────────────────── per-batch dispatch (runs in a thread) ─────


def _dispatch_one_batch(
    *, payload: FeedPayload, api_key: str, mode: str,
) -> tuple[FeedPayload, list, int, float]:
    """Submit a batch (or sync) + poll. Returns (payload, results, run_id, wall)."""
    cfg = payload.cfg
    gate_config = {
        "dispatch_kind":     payload.kind,
        "cost_estimate_usd": payload.est_total,
        "n_api_calls":       payload.n_dispatch,
        "n_drug_groups":     payload.n_groups_total,
        "n_rows_to_write":   payload.n_rows_to_write,
        "request_index":     payload.request_index,
    }
    started = time.time()
    if mode == "sync":
        with deep_dives_connect() as dd_conn:
            run_id = open_run(
                dd_conn, snapshot_date=payload.snapshot_label,
                prompt_version=cfg.prompt_version, model=cfg.model, mode="sync",
                feed_size=payload.n_dispatch, gate_config=gate_config, batch_id=None,
            )
            dd_conn.commit()
        print(f"  [{payload.kind}] sync dispatching {payload.n_dispatch} request(s) "
              f"(run_id={run_id})…")
        results = dispatch_sync(
            api_key=api_key, model=cfg.model,
            max_output_tokens=cfg.max_output_tokens,
            system_prompt=payload.cached_prefix,
            per_ticker=payload.per_ticker,
            sync_concurrency=cfg.dispatch.sync_concurrency,
        )
    else:
        print(f"  [{payload.kind}] submitting batch ({payload.n_dispatch} request(s))…")
        batch_id = submit_batch(
            api_key=api_key, model=cfg.model,
            max_output_tokens=cfg.max_output_tokens,
            system_prompt=payload.cached_prefix,
            per_ticker=payload.per_ticker,
        )
        with deep_dives_connect() as dd_conn:
            run_id = open_run(
                dd_conn, snapshot_date=payload.snapshot_label,
                prompt_version=cfg.prompt_version, model=cfg.model, mode="batch",
                feed_size=payload.n_dispatch, gate_config=gate_config, batch_id=batch_id,
            )
            dd_conn.commit()
        print(f"  [{payload.kind}] batch_id={batch_id}  run_id={run_id}")
        print(f"  [{payload.kind}] -> recover: python scripts/3_8_claude_dispatch.py --resume-run {run_id}")
        results = poll_and_collect_batch(
            api_key=api_key, batch_id=batch_id,
            poll_interval_s=cfg.dispatch.poll_interval_s,
            timeout_s=cfg.dispatch.timeout_s,
        )
    wall = time.time() - started
    return payload, results, run_id, wall


# ────────────────────── writeback (kind-aware) ────────────


def _write_results(
    *, payload: FeedPayload, results: list, run_id: int, mode: str,
    wall_time_s: float,
) -> tuple[int, int, list[str], dict]:
    """Persist each DispatchResult; close the run. Adds D35 fields when
    payload.kind == 'rescue'. Returns (success_count, error_count,
    rate_limited_tickers, token_totals)."""
    cfg = payload.cfg
    prep_index = {p["ticker"]: p for p in payload.per_ticker}
    success_count = 0
    rate_limited: list[str] = []
    token_totals = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
                    "search_calls": 0, "usd": 0.0}

    dd_conn = sqlite3.connect(
        str(PROJECT_ROOT / "data" / "claude_deep_dives.db"), timeout=30,
    )
    dd_conn.row_factory = sqlite3.Row
    try:
        from module_7.deep_dives_db import init_deep_dives_db
        init_deep_dives_db(PROJECT_ROOT / "data" / "claude_deep_dives.db")

        for res in results:
            prep = prep_index.get(res.ticker)

            token_totals["input"] += res.input_tokens
            token_totals["output"] += res.output_tokens
            token_totals["cache_read"] += res.cache_read_tokens
            token_totals["cache_create"] += res.cache_creation_tokens
            token_totals["search_calls"] += res.web_search_calls
            res_usd = _usd_cost_per_call(
                pricing=cfg.pricing.model_dump(),
                input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                cache_read_tokens=res.cache_read_tokens,
                cache_creation_tokens=res.cache_creation_tokens,
                web_search_calls=res.web_search_calls,
                batch_mode=(mode == "batch"),
            )
            token_totals["usd"] += res_usd

            snapshot_date = (prep or {}).get("snapshot_date")

            if not res.success:
                detail = res.error_detail or ""
                if res.stop_reason:
                    detail = f"[stop_reason={res.stop_reason}] {detail}"
                if "RateLimitError" in detail or "rate_limit" in detail.lower():
                    rate_limited.append(res.ticker)
                write_error_row(
                    dd_conn, run_id=run_id, ticker=res.ticker,
                    snapshot_date=snapshot_date,
                    error_kind=res.error_kind or "api_error",
                    error_detail=detail, raw_text=res.raw_text,
                )
                dd_conn.commit()
                continue

            try:
                parsed = parse_deep_dive(res.raw_text)
            except ParseError as e:
                write_error_row(
                    dd_conn, run_id=run_id, ticker=res.ticker,
                    snapshot_date=snapshot_date,
                    error_kind=e.kind, error_detail=e.detail,
                    raw_text=res.raw_text,
                )
                dd_conn.commit()
                continue

            mods_cfg = {
                "insider":  cfg.modifiers.insider.model_dump(),
                "funds":    cfg.modifiers.funds.model_dump(),
                "momentum": cfg.modifiers.momentum.model_dump(),
            }
            clamps_cfg = cfg.clamps.model_dump()

            members = (prep or {}).get("members") or []
            anchor_nct = (prep or {}).get("anchor_nct_number")
            anchor_type = (prep or {}).get("anchor_next_catalyst_type")
            rescue_class = (prep or {}).get("rescue_class") if payload.kind == "rescue" else None
            anchor_attributed = False

            anchor_pack = (prep or {}).get("pack") or {}
            price_at_api_time = ((anchor_pack.get("market_snapshot") or {})
                                 .get("last_price_usd"))
            move_on_hit_pct = parsed.catalyst_outcome["expected_move_on_hit_pct"]
            move_on_miss_pct = parsed.catalyst_outcome["expected_move_on_miss_pct"]
            if price_at_api_time:
                target_on_hit = price_at_api_time * (1.0 + move_on_hit_pct / 100.0)
                target_on_miss = price_at_api_time * (1.0 + move_on_miss_pct / 100.0)
            else:
                target_on_hit = target_on_miss = None

            for member in members:
                # D35 — for rescue feed, prefer Claude's resolved date.
                if payload.kind == "rescue":
                    effective_date = (parsed.claude_resolved_catalyst_date
                                      or member.get("catalyst_date_iso"))
                else:
                    effective_date = member.get("catalyst_date_iso")
                weeks = weeks_between(
                    effective_date, None,
                    snapshot_date or "1970-01-01",
                )
                expectancy = compute_expectancy(
                    p_clinical=parsed.catalyst_outcome["p_clinical"],
                    expected_move_on_hit_pct=parsed.catalyst_outcome["expected_move_on_hit_pct"],
                    expected_move_on_miss_pct=parsed.catalyst_outcome["expected_move_on_miss_pct"],
                    insider_score=member.get("insider_score"),
                    fund_accumulation_score=member.get("fund_accumulation_score"),
                    weeks_to_catalyst=weeks,
                    modifiers=mods_cfg,
                    clamps=clamps_cfg,
                )

                is_anchor = (member.get("nct_number") == anchor_nct
                             and member.get("next_catalyst_type") == anchor_type)
                if is_anchor and not anchor_attributed:
                    row_usd_cost = res_usd
                    row_input_tokens = res.input_tokens
                    row_output_tokens = res.output_tokens
                    row_cache_read = res.cache_read_tokens
                    row_cache_create = res.cache_creation_tokens
                    row_web_search = res.web_search_calls
                    row_anchor_nct = None
                    row_anchor_type = None
                    anchor_attributed = True
                else:
                    row_usd_cost = 0.0
                    row_input_tokens = 0
                    row_output_tokens = 0
                    row_cache_read = 0
                    row_cache_create = 0
                    row_web_search = 0
                    row_anchor_nct = anchor_nct
                    row_anchor_type = anchor_type

                row = {
                    "snapshot_date":       member.get("snapshot_date") or snapshot_date,
                    "ticker":              member["ticker"],
                    "drug":                member["drug"],
                    "nct_number":          member["nct_number"],
                    "next_catalyst_type":  member["next_catalyst_type"],
                    "run_id":              run_id,
                    "p_clinical":          parsed.catalyst_outcome["p_clinical"],
                    "p_clinical_low":      parsed.catalyst_outcome.get("p_clinical_low"),
                    "p_clinical_high":     parsed.catalyst_outcome.get("p_clinical_high"),
                    "expected_move_on_hit_pct":  parsed.catalyst_outcome["expected_move_on_hit_pct"],
                    "expected_move_on_miss_pct": parsed.catalyst_outcome["expected_move_on_miss_pct"],
                    "rnpv_total_usd":      parsed.rnpv_total_usd,
                    "rnpv_per_share_usd":  parsed.rnpv_per_share_usd,
                    "lead_indication":     parsed.lead_indication,
                    "management_track_record_score": parsed.management_track_record.get("score"),
                    "acquisition_target_score":      parsed.acquisition_target.get("score"),
                    "rnpv_by_indication_json":   json.dumps(parsed.rnpv_by_indication, default=str),
                    "drug_profile_json":         json.dumps(parsed.drug_profile, default=str),
                    "clinical_evidence_json":    json.dumps(parsed.clinical_evidence, default=str),
                    "financial_overhang_json":   json.dumps(parsed.financial_overhang, default=str),
                    "catalyst_date_sanity_json": json.dumps(parsed.catalyst_date_sanity_check, default=str),
                    "key_risks_json":            json.dumps(parsed.key_risks, default=str),
                    "thesis_summary":      parsed.thesis_summary,
                    "reasoning_trace":     parsed.reasoning_trace,
                    "insider_score_input":           expectancy.insider_score_input,
                    "fund_accumulation_score_input": expectancy.fund_accumulation_score_input,
                    "m_insider":  expectancy.m_insider,
                    "m_funds":    expectancy.m_funds,
                    "p_final":    expectancy.p_final,
                    "e_move_pct": expectancy.e_move_pct,
                    "weeks_to_catalyst_mid":   expectancy.weeks_to_catalyst,
                    "expectancy_per_week_pct": expectancy.expectancy_per_week_pct,
                    "prompt_version":  cfg.prompt_version,
                    "model":           cfg.model,
                    "response_id":     res.response_id,
                    "raw_text":        res.raw_text,
                    "input_tokens":          row_input_tokens,
                    "output_tokens":         row_output_tokens,
                    "cache_read_tokens":     row_cache_read,
                    "cache_creation_tokens": row_cache_create,
                    "web_search_calls":      row_web_search,
                    "usd_cost":              row_usd_cost,
                    "catalyst_signature": compute_catalyst_signature(
                        drug=member["drug"], stage=member.get("stage"),
                        next_catalyst_type=member["next_catalyst_type"],
                        catalyst_date_iso=member.get("catalyst_date_iso"),
                    ),
                    "drug_signature":             (prep or {}).get("drug_signature"),
                    "anchor_nct_number":          row_anchor_nct,
                    "anchor_next_catalyst_type":  row_anchor_type,
                    "price_at_api_time_usd":      price_at_api_time,
                    "target_price_on_hit_usd":    target_on_hit,
                    "target_price_on_miss_usd":   target_on_miss,
                }
                if payload.kind == "rescue":
                    row.update({
                        "claude_resolved_catalyst_date": parsed.claude_resolved_catalyst_date,
                        "catalyst_date_source":          parsed.catalyst_date_source,
                        "rescue_class":                   rescue_class,
                    })
                upsert_deep_dive_row(dd_conn, row)

            actual_ticker = (prep or {}).get("actual_ticker") or res.ticker
            for r in (res.server_tool_results or []):
                url = r.get("url") or ""
                if not url:
                    continue
                upsert_web_search_cache_row(
                    dd_conn, url=url, ticker=actual_ticker,
                    snapshot_date=snapshot_date, run_id=run_id,
                    search_query=r.get("query") or "",
                    title=r.get("title") or "",
                    content=r.get("text") or "",
                    domain=_domain_of(url),
                    published_date=r.get("page_age"),
                )

            dd_conn.commit()
            success_count += 1

        list_price_total = _usd_cost_per_call(
            pricing=cfg.pricing.model_dump(),
            input_tokens=(token_totals["input"]
                          + token_totals["cache_read"]
                          + token_totals["cache_create"]),
            output_tokens=token_totals["output"],
            cache_read_tokens=0, cache_creation_tokens=0,
            web_search_calls=token_totals["search_calls"],
            batch_mode=False,
        )
        close_run(
            dd_conn, run_id=run_id, wall_time_s=wall_time_s,
            input_tokens_total=token_totals["input"],
            output_tokens_total=token_totals["output"],
            cache_read_tokens_total=token_totals["cache_read"],
            cache_creation_tokens_total=token_totals["cache_create"],
            web_search_calls_total=token_totals["search_calls"],
            usd_cost_total=token_totals["usd"],
            usd_cost_list_price=list_price_total,
        )
        dd_conn.commit()
    finally:
        dd_conn.close()

    error_count = len(results) - success_count
    return success_count, error_count, rate_limited, token_totals


# ───────────────────── --resume-run path ─────────────────


def _resume_one(run_id: int, *, args, api_key: str, cfg_m7, cfg_m8,
                config_dir_m7: Path, config_dir_m8: Path) -> int:
    """Re-poll an existing batch by run_id; route writeback by prompt_version."""
    with deep_dives_connect() as cx:
        row = cx.execute("SELECT * FROM deep_dive_runs WHERE run_id=?",
                         (run_id,)).fetchone()
    if row is None:
        raise SystemExit(f"--resume-run {run_id}: not found in deep_dive_runs")
    if not row["batch_id"]:
        raise SystemExit(f"--resume-run {run_id}: no batch_id (sync run cannot resume)")
    batch_id = row["batch_id"]

    is_rescue = (row["prompt_version"] == cfg_m8.prompt_version)
    cfg = cfg_m8 if is_rescue else cfg_m7
    config_dir = config_dir_m8 if is_rescue else config_dir_m7
    kind = "rescue" if is_rescue else "hard_pass"

    if is_rescue:
        prefix = load_rescue_prefix(
            rescue_preamble_path=config_dir_m8 / Path(cfg_m8.system_prompt_path).name,
            m7_system_prompt_path=config_dir_m7 / "module_7_system_prompt.md",
            m7_few_shots_path=config_dir_m8 / Path(cfg_m8.few_shot_examples_path).name,
        )
    else:
        prefix = load_cacheable_prefix(
            config_dir_m7 / Path(cfg_m7.system_prompt_path).name,
            config_dir_m7 / Path(cfg_m7.few_shot_examples_path).name,
        )

    print("=" * 64)
    print(f"  RESUME mode  run_id={run_id}  batch_id={batch_id}  kind={kind}")
    print("=" * 64)
    started = time.time()
    try:
        results = poll_and_collect_batch(
            api_key=api_key, batch_id=batch_id,
            poll_interval_s=cfg.dispatch.poll_interval_s,
            timeout_s=cfg.dispatch.timeout_s,
        )
    except Exception as e:                                  # noqa: BLE001
        raise SystemExit(f"poll_and_collect_batch failed: {e}")

    gate_cfg = json.loads(row["gate_config_json"] or "{}")
    request_index = gate_cfg.get("request_index", {}) or {}
    per_ticker = []
    for r in results:
        entry = request_index.get(r.ticker) or {}
        rec = {
            "ticker":                    r.ticker,
            "actual_ticker":             entry.get("actual_ticker"),
            "drug":                      entry.get("drug"),
            "snapshot_date":             entry.get("snapshot_date"),
            "anchor_nct_number":         entry.get("anchor_nct_number"),
            "anchor_next_catalyst_type": entry.get("anchor_next_catalyst_type"),
            "drug_signature":            entry.get("drug_signature"),
            "members":                   entry.get("members") or [],
        }
        if is_rescue:
            rec["rescue_class"] = entry.get("rescue_class")
        per_ticker.append(rec)

    payload = FeedPayload(kind=kind, cfg=cfg, config_dir=config_dir,
                          cached_prefix=prefix)
    payload.per_ticker = per_ticker
    wall = time.time() - started
    success_count, error_count, rate_limited, token_totals = _write_results(
        payload=payload, results=results, run_id=run_id, mode="batch",
        wall_time_s=wall,
    )
    _print_summary(kind=kind, run_id=run_id, results=results,
                   success_count=success_count, wall=wall,
                   token_totals=token_totals, rate_limited=rate_limited)
    return 0


# ──────────────────────── summary printer ─────────────────


def _print_summary(*, kind: str, run_id: int, results: list,
                   success_count: int, wall: float,
                   token_totals: dict, rate_limited: list[str]) -> None:
    print()
    print("-" * 70)
    print(f"  {kind.upper()} batch complete: run_id={run_id}")
    print(f"    Calls dispatched:     {len(results)}")
    print(f"    Successful parses:    {success_count}")
    print(f"    Errors:               {len(results) - success_count}")
    print(f"    Wall time:            {wall:.1f}s")
    print(f"    Tokens (in / out):    {token_totals['input']:,} / {token_totals['output']:,}")
    print(f"    Cache (read / create):{token_totals['cache_read']:,} / {token_totals['cache_create']:,}")
    print(f"    Web searches:         {token_totals['search_calls']}")
    print(f"    USD cost (estimated): ${token_totals['usd']:,.2f}  (calibrated)")
    print("-" * 70)
    if rate_limited:
        print(f"  RATE-LIMITED ({len(rate_limited)} ticker(s)): {','.join(rate_limited)}")


# ──────────────────────────── main ─────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", default=None,
                        help="Comma-separated tickers (applies to both feeds)")
    parser.add_argument("--classes", default=None,
                        help="Comma-separated rescue classes (A,B,C). Rescue feed only.")
    parser.add_argument("--mode", default=None, choices=("sync", "batch"),
                        help="Override config dispatch.mode")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Bypass identity cache; dispatch every candidate")
    parser.add_argument("--defined-only", action="store_true",
                        help="Hard-pass feed: restrict to defined-timing catalysts")
    parser.add_argument("--resume-run", type=int, default=None, metavar="RUN_ID",
                        help="D51 — recover a half-completed batch run "
                             "(routed by prompt_version)")
    parser.add_argument("--skip-hard-pass", action="store_true",
                        help="D40 — skip the hard-pass batch (dispatch rescue only)")
    parser.add_argument("--skip-rescue", action="store_true",
                        help="D40 — skip the rescue batch (dispatch hard-pass only)")
    parser.add_argument("--yes", action="store_true",
                        help="Single-shot bypass of the mandatory [y/N] gate")
    parser.add_argument("--override-ack-gate", action="store_true",
                        help="D38 — bypass the acknowledged-ticker gate (both feeds)")
    parser.add_argument("--one-drug-per-ticker", action="store_true",
                        help="D38 — collapse multi-drug catalysts to one per ticker (both feeds)")
    parser.add_argument("--override-coverage-gate", action="store_true",
                        help="D39 — bypass the coverage gate (both feeds)")
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB)
    parser.add_argument("--fundamentals-db", type=Path, default=FUNDAMENTALS_DB)
    parser.add_argument("--m7-config", type=Path, default=default_m7_config_path())
    parser.add_argument("--m8-config", type=Path, default=default_m8_config_path())
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg_m7 = load_module_7_config(args.m7_config)
    cfg_m8 = load_module_8_config(args.m8_config)
    config_dir_m7 = args.m7_config.parent
    config_dir_m8 = args.m8_config.parent
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    print(f"[3_8_claude_dispatch] m7 prompt_version: {cfg_m7.prompt_version}  "
          f"m8 prompt_version: {cfg_m8.prompt_version}")
    print(f"[3_8_claude_dispatch] model: {cfg_m7.model}")

    # ── Resume path ─────────────────────────
    if args.resume_run is not None:
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY not set in environment / .env")
        return _resume_one(int(args.resume_run), args=args, api_key=api_key,
                           cfg_m7=cfg_m7, cfg_m8=cfg_m8,
                           config_dir_m7=config_dir_m7, config_dir_m8=config_dir_m8)

    # ── Fetch & gate both feeds ─────────────
    explicit = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
                if args.tickers else None)
    classes = ([c.strip().upper() for c in args.classes.split(",") if c.strip()]
               if args.classes else None)

    print()
    print("[3_8_claude_dispatch] === fetching + gating both feeds ===")
    if args.skip_hard_pass:
        print("  [HP] --skip-hard-pass: skipping hard-pass feed entirely.")
        hp_candidates: list[dict] = []
    else:
        hp_candidates = fetch_hard_pass_candidates(
            args.biotech_db, explicit_tickers=explicit,
            defined_timing_only=args.defined_only,
        )
        print(f"  [HP] hard-pass candidates (raw): {len(hp_candidates)}")
        hp_candidates = _apply_gates(hp_candidates, label="HP", args=args)

    if args.skip_rescue:
        print("  [RES] --skip-rescue: skipping rescue feed entirely.")
        res_candidates: list[dict] = []
    else:
        res_candidates = fetch_rescue_candidates(
            args.biotech_db, explicit_tickers=explicit, classes=classes,
        )
        print(f"  [RES] rescue candidates (raw): {len(res_candidates)}")
        if not res_candidates:
            print("  [RES] (run scripts/3_7_compute_eligibility.py first if empty)")
        res_candidates = _apply_gates(res_candidates, label="RES", args=args)

    if not hp_candidates and not res_candidates:
        print("\n[3_8_claude_dispatch] no candidates after gates; exiting.")
        return 0

    # ── Live-price refresh (one combined yfinance call) ──
    all_tickers = sorted({c["ticker"] for c in hp_candidates}
                         | {c["ticker"] for c in res_candidates})
    print(f"\n[3_8_claude_dispatch] refreshing live prices for {len(all_tickers)} ticker(s)…")
    live_prices = get_live_prices(all_tickers, force=True)
    n_live_ok = sum(1 for lp in live_prices.values() if lp.price_usd)
    print(f"[3_8_claude_dispatch] live-price coverage: {n_live_ok}/{len(all_tickers)}")

    # ── Build packs + drug-groups + cache filter + cost estimate ─
    print()
    print("[3_8_claude_dispatch] === building packs + cost estimates ===")
    mode = args.mode or cfg_m7.dispatch.mode
    mode_label = "cache+batch" if mode == "batch" else f"cache-only (sync@{cfg_m7.dispatch.sync_concurrency})"

    hp_prefix = load_cacheable_prefix(
        config_dir_m7 / Path(cfg_m7.system_prompt_path).name,
        config_dir_m7 / Path(cfg_m7.few_shot_examples_path).name,
    )
    res_prefix = load_rescue_prefix(
        rescue_preamble_path=config_dir_m8 / Path(cfg_m8.system_prompt_path).name,
        m7_system_prompt_path=config_dir_m7 / "module_7_system_prompt.md",
        m7_few_shots_path=config_dir_m8 / Path(cfg_m8.few_shot_examples_path).name,
    )

    hp_payload = _prepare_feed(
        kind="hard_pass", label="HP", cfg=cfg_m7,
        config_dir=config_dir_m7, cached_prefix=hp_prefix,
        candidates=hp_candidates,
        biotech_db=args.biotech_db, fundamentals_db=args.fundamentals_db,
        live_prices=live_prices,
        force_refresh=args.force_refresh, mode_label=mode_label,
    )
    res_payload = _prepare_feed(
        kind="rescue", label="RES", cfg=cfg_m8,
        config_dir=config_dir_m8, cached_prefix=res_prefix,
        candidates=res_candidates,
        biotech_db=args.biotech_db, fundamentals_db=args.fundamentals_db,
        live_prices=live_prices,
        force_refresh=args.force_refresh, mode_label=mode_label,
    )

    n_total = hp_payload.n_dispatch + res_payload.n_dispatch
    if n_total == 0:
        print("\n[3_8_claude_dispatch] nothing to dispatch (all cached or empty).")
        return 0

    # ── Combined cost ceiling check ─────────
    total_est = hp_payload.est_total + res_payload.est_total
    combined_ceiling = max(cfg_m7.cost_ceiling_usd, cfg_m8.cost_ceiling_usd)
    print()
    print(f"[3_8_claude_dispatch] === estimated cost ({mode_label}) ===")
    print(f"  HARD-PASS:  {hp_payload.n_dispatch:>3} dispatch(es)  → ${hp_payload.est_total:>10.2f}")
    print(f"  RESCUE:     {res_payload.n_dispatch:>3} dispatch(es)  → ${res_payload.est_total:>10.2f}")
    print(f"  COMBINED:   {n_total:>3} dispatch(es)  → ${total_est:>10.2f}")
    print(f"  cost_ceiling_usd (max of m7/m8 cfg): ${combined_ceiling:.2f}")
    if total_est > combined_ceiling:
        raise SystemExit(
            f"Estimated cost ${total_est:.2f} exceeds combined cost ceiling "
            f"${combined_ceiling:.2f}. Raise cost_ceiling_usd in config/module_7.yaml "
            f"or config/module_8.yaml, or narrow the feed with --tickers / --skip-*."
        )

    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in environment / .env")

    # ── ONE [y/N] gate ──────────────────────
    if args.yes:
        print(f"\n[3_8_claude_dispatch] --yes specified: dispatching {n_total} call(s) "
              f"for ~${total_est:.2f} without confirmation.")
    else:
        try:
            prompt = (
                f"\nProceed to dispatch {n_total} Anthropic call(s) "
                f"(HP={hp_payload.n_dispatch} + RES={res_payload.n_dispatch}) "
                f"for ~${total_est:.2f}? [y/N] "
            )
            answer = input(prompt).strip().lower() or "n"
        except EOFError:
            print("\n[stdin closed] aborting. Re-run with --yes to bypass.")
            answer = "n"
        if answer != "y":
            print("Aborted.")
            return 0

    # ── Parallel dispatch (both batches) ────
    print()
    print(f"[3_8_claude_dispatch] === parallel dispatch (mode={mode}) ===")
    overall_started = time.time()

    active_payloads = [p for p in (hp_payload, res_payload) if p.n_dispatch > 0]
    futures: dict[concurrent.futures.Future, FeedPayload] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(2, len(active_payloads))) as ex:
        for p in active_payloads:
            futures[ex.submit(_dispatch_one_batch,
                              payload=p, api_key=api_key, mode=mode)] = p
        completed = []
        for fut in concurrent.futures.as_completed(futures):
            try:
                completed.append(fut.result())
            except Exception as e:                              # noqa: BLE001
                p = futures[fut]
                print(f"\n[3_8_claude_dispatch] [{p.kind}] dispatch FAILED: {e}")

    overall_wall = time.time() - overall_started
    print()
    print(f"[3_8_claude_dispatch] both batches finished in {overall_wall:.1f}s (max of parallel)")

    # ── Writeback (sequential is fine; sqlite serial anyway) ─
    grand_token_totals = {"input": 0, "output": 0, "cache_read": 0,
                          "cache_create": 0, "search_calls": 0, "usd": 0.0}
    for payload, results, run_id, wall in completed:
        success_count, error_count, rate_limited, token_totals = _write_results(
            payload=payload, results=results, run_id=run_id, mode=mode,
            wall_time_s=wall,
        )
        for k in grand_token_totals:
            grand_token_totals[k] += token_totals[k]
        _print_summary(kind=payload.kind, run_id=run_id, results=results,
                       success_count=success_count, wall=wall,
                       token_totals=token_totals, rate_limited=rate_limited)

    print()
    print("=" * 70)
    print(f"  D40 unified dispatch complete (wall {overall_wall:.1f}s)")
    print(f"  Total USD cost (calibrated): ${grand_token_totals['usd']:,.2f}")
    print(f"  Total tokens (in/out): {grand_token_totals['input']:,} / "
          f"{grand_token_totals['output']:,}")
    print(f"  Verify on console.anthropic.com")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
