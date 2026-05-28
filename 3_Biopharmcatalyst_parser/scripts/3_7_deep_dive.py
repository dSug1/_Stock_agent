"""Module 7 — Claude API deep-dive dispatcher.

The ONLY script that actually spends money on Anthropic. Gated by a
mandatory [y/N] prompt before any API call, regardless of TTY state.
Only `--yes` bypasses; setting stdin to non-tty does NOT auto-confirm.

Usage:
    PYTHONPATH=src python scripts/3_7_deep_dive.py                  # batch mode, all hard-pass cache-misses
    PYTHONPATH=src python scripts/3_7_deep_dive.py --mode sync      # parallel async fan-out (calibration)
    PYTHONPATH=src python scripts/3_7_deep_dive.py --tickers TCRX   # one ticker
    PYTHONPATH=src python scripts/3_7_deep_dive.py --force-refresh  # bypass identity cache
    PYTHONPATH=src python scripts/3_7_deep_dive.py --resume-run N   # D51 — recover crashed batch
    PYTHONPATH=src python scripts/3_7_deep_dive.py --yes            # bypass [y/N] gate

Spec: spec/module_7_spec.md §5.8.
Decisions: spec/decisions.md § D16 + D17.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
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
from module_7 import (                                                     # noqa: E402
    EstimateInputs,
    ParseError,
    close_run,
    compute_catalyst_signature,
    compute_drug_signature,
    compute_expectancy,
    db_connect as deep_dives_connect,
    default_config_path,
    dispatch_sync,
    estimate_cost,
    group_candidates_by_drug,
    load_cacheable_prefix,
    load_module_7_config,
    open_run,
    parse_deep_dive,
    partition_drug_groups_by_cache,
    partition_feed_by_cache,
    poll_and_collect_batch,
    submit_batch,
    update_run_batch_id,
    upsert_deep_dive_row,
    upsert_web_search_cache_row,
    weeks_between,
    write_error_row,
)
from module_7.context_pack import (                                        # noqa: E402
    augment_pack_with_drug_siblings,
    build_context_pack,
    fetch_hard_pass_candidates,
)
from module_7.dispatch import (                                            # noqa: E402
    build_user_message,
    build_web_search_tool_def,
    load_allowed_domains,
)
from module_7.live_price import get_live_prices                            # noqa: E402

_LOG = logging.getLogger("3_7_deep_dive")


# ────────────────────── cost helper ────────────────────────


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
    """Compute billed USD for one (or aggregated) Anthropic call.

    Anthropic SDK reports input_tokens, cache_read_input_tokens, and
    cache_creation_input_tokens as THREE DISJOINT buckets — don't subtract.
    """
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


# ─────────────────── per-group payload prep (D23) ──────────


import hashlib as _hashlib


def _drug_short_id(drug: str) -> str:
    """8-char stable hash for safe inclusion in Anthropic custom_id."""
    return _hashlib.sha256(drug.encode("utf-8")).hexdigest()[:8]


def _prepare_per_group(
    drug_groups: list[dict],
    candidate_packs: dict[tuple, dict],
    *,
    config_dir: Path,
    domains_path: str,
    max_uses: int,
) -> list[dict]:
    """D23 — one payload per drug group (was: one per catalyst).

    Each group becomes a single Anthropic request. After the response
    lands, the writeback loop expands it into one deep_dives row per
    group member, all sharing the same Claude output.

    `candidate_packs` keys: (ticker, drug, nct_number, next_catalyst_type)
    → pack dict for that exact catalyst row.

    Returned dicts carry `ticker` set to a unique composite custom_id
    so the batch API and the writeback prep_index can match results back
    to groups even when one ticker has multiple drugs.
    """
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

        # Composite custom_id: stable, unique per (ticker, drug).
        custom_id = f"{anchor['ticker']}__{_drug_short_id(anchor['drug'])}"

        out.append({
            # The dispatcher uses `ticker` as request-id throughout.
            # We overload it with the composite custom_id so the same
            # API works with multiple drugs per ticker.
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
        })
    return out


# ────────────────────────── main ───────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    feed = parser.add_mutually_exclusive_group()
    feed.add_argument("--tickers", default=None,
                      help="Comma-separated tickers (still requires hard_pass=1)")
    parser.add_argument("--mode", default=None, choices=("sync", "batch"),
                        help="Override config dispatch.mode")
    parser.add_argument("--force-refresh", action="store_true",
                        help="Bypass identity cache; dispatch every candidate")
    parser.add_argument("--defined-only", action="store_true",
                        help="Restrict to catalysts with defined timing (HTML 'Defined timing' tab)")
    parser.add_argument("--resume-run", type=int, default=None, metavar="RUN_ID",
                        help="D51 — recover a half-completed batch run")
    parser.add_argument("--yes", action="store_true",
                        help="Single-shot bypass of the mandatory [y/N] gate")
    parser.add_argument("--biotech-db", type=Path, default=BIOTECH_DB)
    parser.add_argument("--fundamentals-db", type=Path, default=FUNDAMENTALS_DB)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = load_module_7_config(args.config)
    config_dir = args.config.parent
    load_dotenv(REPO_ROOT / ".env")
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    print(f"[3_7_deep_dive] config: {args.config}")
    print(f"[3_7_deep_dive] prompt_version: {cfg.prompt_version}")
    print(f"[3_7_deep_dive] model: {cfg.model}")

    # ── Step 0 — D51 resume path ─────────────────────────
    if args.resume_run is not None:
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY not set in environment / .env")
        run_id = int(args.resume_run)
        with deep_dives_connect() as cx:
            row = cx.execute("SELECT * FROM deep_dive_runs WHERE run_id=?",
                             (run_id,)).fetchone()
        if row is None:
            raise SystemExit(f"--resume-run {run_id}: not found in deep_dive_runs")
        if not row["batch_id"]:
            raise SystemExit(f"--resume-run {run_id}: no batch_id (sync run cannot resume)")
        batch_id = row["batch_id"]
        print()
        print("=" * 64)
        print(f"  RESUME mode")
        print(f"    run_id:   {run_id}")
        print(f"    batch_id: {batch_id}")
        print(f"    polling for results (no NEW Anthropic charges)…")
        print("=" * 64)
        started = time.time()
        try:
            results = poll_and_collect_batch(
                api_key=api_key, batch_id=batch_id,
                poll_interval_s=cfg.dispatch.poll_interval_s,
                timeout_s=cfg.dispatch.timeout_s,
            )
        except Exception as e:                                  # noqa: BLE001
            raise SystemExit(
                f"poll_and_collect_batch failed: {e}\n"
                f"If the batch is still processing, rerun this command later."
            )
        # D23 — pull the request_index out of gate_config_json so the
        # writeback can expand each result into one row per group member.
        gate_cfg = json.loads(row["gate_config_json"] or "{}")
        request_index = gate_cfg.get("request_index", {}) or {}
        per_ticker = []
        for r in results:
            entry = request_index.get(r.ticker) or {}
            per_ticker.append({
                "ticker":                    r.ticker,
                "actual_ticker":             entry.get("actual_ticker"),
                "drug":                      entry.get("drug"),
                "snapshot_date":             entry.get("snapshot_date"),
                "anchor_nct_number":         entry.get("anchor_nct_number"),
                "anchor_next_catalyst_type": entry.get("anchor_next_catalyst_type"),
                "drug_signature":            entry.get("drug_signature"),
                "members":                   entry.get("members") or [],
            })
        wall = time.time() - started
        _write_results(
            results=results,
            per_ticker=per_ticker,
            run_id=run_id,
            mode="batch",
            cfg=cfg,
            wall_time_s=wall,
            biotech_db_path=args.biotech_db,
            fundamentals_db_path=args.fundamentals_db,
        )
        return 0

    # ── Step 1 — build feed ─────────────────────────────
    explicit = ([t.strip().upper() for t in args.tickers.split(",") if t.strip()]
                if args.tickers else None)
    candidates = fetch_hard_pass_candidates(args.biotech_db,
                                            explicit_tickers=explicit,
                                            defined_timing_only=args.defined_only)
    if not candidates:
        print("[3_7_deep_dive] no hard-pass candidates; exiting.")
        return 0

    # D25 — refresh live yfinance prices for every candidate ticker so the
    # pack Claude sees carries the LATEST share price (not the M6.5
    # snapshot price). Batched into one yfinance call.
    candidate_tickers = sorted({c["ticker"] for c in candidates})
    print(f"[3_7_deep_dive] refreshing live prices for {len(candidate_tickers)} ticker(s)…")
    live_prices = get_live_prices(candidate_tickers, force=True)
    n_live_ok = sum(1 for lp in live_prices.values() if lp.price_usd)
    print(f"[3_7_deep_dive] live-price coverage: {n_live_ok}/{len(candidate_tickers)}")

    candidates_with_packs: list[tuple[dict, dict]] = []
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
        # D25 — overlay the live price into the pack's market_snapshot. If
        # yfinance failed for this ticker we keep whatever M6.5 cached.
        lp = live_prices.get(cand["ticker"])
        if lp and lp.price_usd:
            pack["market_snapshot"]["last_price_usd"]   = lp.price_usd
            pack["market_snapshot"]["last_price_as_of"] = lp.fetched_at_utc
            pack["market_snapshot"]["price_source"]    = "yfinance-live (D25)"
            basic = pack["market_snapshot"].get("fully_diluted_shares_count") \
                    or pack["market_snapshot"].get("basic_shares_count")
            if basic:
                pack["market_snapshot"]["market_cap_fdsc_usd"] = float(basic) * float(lp.price_usd)
        candidates_with_packs.append((cand, pack))

    # ── Step 2 — D23 group by drug, then drug-level cache filter ─
    candidate_packs: dict[tuple, dict] = {
        (c["ticker"], c["drug"], c["nct_number"], c["next_catalyst_type"]): pack
        for c, pack in candidates_with_packs
    }
    raw_candidates = [{**cand,
                       "catalyst_date_iso": cand.get("catalyst_date_iso")}
                      for cand, _ in candidates_with_packs]
    drug_groups = group_candidates_by_drug(raw_candidates)
    with deep_dives_connect() as dd_conn:
        groups_to_dispatch, groups_skipped = partition_drug_groups_by_cache(
            dd_conn,
            groups=drug_groups,
            current_prompt_version=cfg.prompt_version,
            force_refresh=args.force_refresh,
        )

    n_candidates = len(candidates_with_packs)
    n_groups_total = len(drug_groups)
    n_groups_dispatch = len(groups_to_dispatch)
    n_groups_skipped = len(groups_skipped)
    n_dispatch = n_groups_dispatch         # API call count
    n_rows_to_write = sum(len(g["members"]) for g in groups_to_dispatch)
    print(f"[3_7_deep_dive] candidates: {n_candidates}  "
          f"drug-groups: {n_groups_total}  "
          f"to dispatch (1 API per drug): {n_dispatch}  "
          f"will populate {n_rows_to_write} catalyst row(s);  "
          f"cache-hit skipped: {n_groups_skipped}")
    if n_groups_skipped > 0:
        by_reason: dict[str, int] = {}
        for g in groups_skipped:
            r = g["cache_lookup"].reason
            by_reason[r] = by_reason.get(r, 0) + 1
        for r, n in sorted(by_reason.items()):
            print(f"    skip reason={r}: {n}")
    dedup_saved = n_candidates - n_groups_total
    if dedup_saved > 0:
        print(f"[3_7_deep_dive] D23 drug-dedup saved {dedup_saved} API call(s) "
              f"({n_groups_total} groups across {n_candidates} catalysts)")

    if n_dispatch == 0:
        print("[3_7_deep_dive] nothing to dispatch (all cached); refreshing reports only.")
        return 0

    # ── Step 3 — cost estimate ─────────────────────────
    cached_prefix = load_cacheable_prefix(
        config_dir / Path(cfg.system_prompt_path).name,
        config_dir / Path(cfg.few_shot_examples_path).name,
    )
    # Sample up to 5 anchor packs for token estimation.
    sample_anchor_packs = [
        candidate_packs[(g["anchor"]["ticker"], g["anchor"]["drug"],
                         g["anchor"]["nct_number"], g["anchor"]["next_catalyst_type"])]
        for g in groups_to_dispatch[:5]
    ]
    sample_packs = [json.dumps(p, default=str)
                    for p in sample_anchor_packs] or [json.dumps({})]
    est = estimate_cost(EstimateInputs(
        snapshot_date=",".join(sorted({g["anchor"]["snapshot_date"]
                                        for g in groups_to_dispatch})),
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
    # D21: pick the scenario that matches the dispatch mode actually
    # going to run. Was previously hard-coded to scenarios[2] (cache+batch)
    # which under-reports sync-mode cost by ~2×.
    resolved_mode = args.mode or cfg.dispatch.mode
    if resolved_mode == "batch":
        est_total = est.scenarios[2].total_usd
        mode_label = "cache+batch"
    else:
        est_total = est.scenarios[1].total_usd
        mode_label = f"cache-only (sync@concurrency={cfg.dispatch.sync_concurrency})"
    print()
    print(f"[3_7_deep_dive] estimated cost ({mode_label}): ${est_total:.2f}")
    print(f"[3_7_deep_dive] cost ceiling: ${cfg.cost_ceiling_usd:.2f}")
    if est_total > cfg.cost_ceiling_usd:
        raise SystemExit(
            f"Estimated cost ${est_total:.2f} exceeds cost_ceiling_usd ${cfg.cost_ceiling_usd:.2f}. "
            f"Edit config/module_7.yaml to raise the ceiling, or use --tickers to limit the feed."
        )

    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY not set in environment / .env")

    # ── Step 4 — MANDATORY [y/N] gate ─────────────────
    # Spec rule 21 + memory `claude-api`: always prompt, regardless of
    # TTY. Only --yes bypasses. EOFError aborts.
    if args.yes:
        print(f"[3_7_deep_dive] --yes specified: dispatching {n_dispatch} call(s) "
              f"for ~${est_total:.2f} without confirmation.")
    else:
        try:
            prompt = (
                f"\nProceed to dispatch {n_dispatch} Anthropic call(s) "
                f"for ~${est_total:.2f}? [y/N] "
            )
            answer = input(prompt).strip().lower() or "n"
        except EOFError:
            print("\n[stdin closed] aborting. Re-run with --yes to bypass the gate.")
            answer = "n"
        if answer != "y":
            print("Aborted.")
            return 0

    # ── Step 5 — prepare payloads (D23 per drug group) ──
    per_ticker = _prepare_per_group(
        groups_to_dispatch,
        candidate_packs,
        config_dir=config_dir,
        domains_path=cfg.web_search.domains_path,
        max_uses=cfg.web_search.max_uses,
    )
    mode = args.mode or cfg.dispatch.mode

    # ── Step 6 — open run, dispatch ────────────────────
    started = time.time()
    snapshot_label = ",".join(sorted({p["snapshot_date"] for p in per_ticker}))
    # D23 — minimal request_index so --resume-run can rebuild the
    # writeback context without re-querying biotech.db.
    request_index = {
        p["ticker"]: {
            "actual_ticker":             p["actual_ticker"],
            "drug":                      p["drug"],
            "snapshot_date":             p["snapshot_date"],
            "anchor_nct_number":         p["anchor_nct_number"],
            "anchor_next_catalyst_type": p["anchor_next_catalyst_type"],
            "drug_signature":            p["drug_signature"],
            "members": [
                {
                    "ticker":             m["ticker"],
                    "snapshot_date":      m.get("snapshot_date"),
                    "drug":               m["drug"],
                    "nct_number":         m["nct_number"],
                    "next_catalyst_type": m["next_catalyst_type"],
                    "stage":              m.get("stage"),
                    "catalyst_date_iso":  m.get("catalyst_date_iso"),
                    "insider_score":      m.get("insider_score"),
                    "momentum_score":     m.get("momentum_score"),
                    "fund_accumulation_score": m.get("fund_accumulation_score"),
                }
                for m in p["members"]
            ],
        }
        for p in per_ticker
    }
    gate_config = {
        "tickers_explicit":  explicit or "rolling-view hard-pass",
        "force_refresh":     args.force_refresh,
        "cost_estimate_usd": est_total,
        "n_api_calls":       n_dispatch,
        "n_drug_groups":     n_groups_total,
        "n_catalysts":       n_candidates,
        "n_rows_to_write":   n_rows_to_write,
        "n_skipped_groups":  n_groups_skipped,
        "skip_reasons":      {g["cache_lookup"].reason:
                              sum(1 for x in groups_skipped
                                  if x["cache_lookup"].reason == g["cache_lookup"].reason)
                              for g in groups_skipped},
        "request_index":     request_index,
    }
    batch_id: str | None = None
    run_id: int | None = None

    if mode == "sync":
        # Open run first so per-result writes have a run_id.
        with deep_dives_connect() as dd_conn:
            run_id = open_run(
                dd_conn, snapshot_date=snapshot_label,
                prompt_version=cfg.prompt_version, model=cfg.model, mode="sync",
                feed_size=n_dispatch, gate_config=gate_config, batch_id=None,
            )
            dd_conn.commit()
        print(f"[3_7_deep_dive] dispatching {n_dispatch} via sync (run_id={run_id})...")
        results = dispatch_sync(
            api_key=api_key, model=cfg.model,
            max_output_tokens=cfg.max_output_tokens,
            system_prompt=cached_prefix,
            per_ticker=per_ticker,
            sync_concurrency=cfg.dispatch.sync_concurrency,
        )
    else:
        # D51 split — submit, persist run + batch_id, THEN poll.
        print(f"[3_7_deep_dive] submitting batch ({n_dispatch} request(s))...")
        batch_id = submit_batch(
            api_key=api_key, model=cfg.model,
            max_output_tokens=cfg.max_output_tokens,
            system_prompt=cached_prefix,
            per_ticker=per_ticker,
        )
        with deep_dives_connect() as dd_conn:
            run_id = open_run(
                dd_conn, snapshot_date=snapshot_label,
                prompt_version=cfg.prompt_version, model=cfg.model, mode="batch",
                feed_size=n_dispatch, gate_config=gate_config, batch_id=batch_id,
            )
            dd_conn.commit()
        print(f"  batch_id={batch_id}  run_id={run_id}")
        print(f"  -> if this script crashes during poll, recover with:")
        print(f"     python scripts/3_7_deep_dive.py --resume-run {run_id}")
        results = poll_and_collect_batch(
            api_key=api_key, batch_id=batch_id,
            poll_interval_s=cfg.dispatch.poll_interval_s,
            timeout_s=cfg.dispatch.timeout_s,
        )
    wall = time.time() - started

    # ── Step 7 — write results ─────────────────────────
    _write_results(
        results=results,
        per_ticker=per_ticker,
        run_id=run_id,
        mode=mode,
        cfg=cfg,
        wall_time_s=wall,
        biotech_db_path=args.biotech_db,
        fundamentals_db_path=args.fundamentals_db,
    )
    return 0


# ───────────────── write-back loop (shared with --resume) ──────────


def _write_results(
    *,
    results: list,
    per_ticker: list[dict],
    run_id: int,
    mode: str,
    cfg,
    wall_time_s: float,
    biotech_db_path: Path,
    fundamentals_db_path: Path,
) -> None:
    """Parse + persist each DispatchResult; close the run.

    Per-result commit so a crash in the middle of the loop doesn't
    roll back already-written results (D51 pattern from 2_Funds_parser).
    """
    prep_index = {p["ticker"]: p for p in per_ticker}
    success_count = 0
    rate_limited: list[str] = []
    token_totals = {
        "input": 0, "output": 0, "cache_read": 0, "cache_create": 0,
        "search_calls": 0, "usd": 0.0,
    }

    dd_conn = sqlite3.connect(
        str(PROJECT_ROOT / "data" / "claude_deep_dives.db"), timeout=30,
    )
    dd_conn.row_factory = sqlite3.Row
    try:
        # Ensure schema is current (no-op if already initialised).
        from module_7.deep_dives_db import init_deep_dives_db
        init_deep_dives_db(PROJECT_ROOT / "data" / "claude_deep_dives.db")

        for res in results:
            prep = prep_index.get(res.ticker)

            # Token + cost accounting (failures still bill).
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

            # D23 — one API call, multiple deep_dives rows. The Claude
            # output is shared across members; each member gets its own
            # expectancy because catalyst_date_iso + per-row M6 scores vary.
            members = (prep or {}).get("members") or []
            anchor_nct  = (prep or {}).get("anchor_nct_number")
            anchor_type = (prep or {}).get("anchor_next_catalyst_type")
            anchor_attributed = False    # full cost/tokens go on anchor only

            # D25 — record the price Claude saw + derive $ targets so the
            # renderer can recompute share-price-appreciation intraday.
            anchor_pack = (prep or {}).get("pack") or {}
            price_at_api_time = ((anchor_pack.get("market_snapshot") or {})
                                 .get("last_price_usd"))
            move_on_hit_pct  = parsed.catalyst_outcome["expected_move_on_hit_pct"]
            move_on_miss_pct = parsed.catalyst_outcome["expected_move_on_miss_pct"]
            if price_at_api_time:
                target_on_hit  = price_at_api_time * (1.0 + move_on_hit_pct  / 100.0)
                target_on_miss = price_at_api_time * (1.0 + move_on_miss_pct / 100.0)
            else:
                target_on_hit = target_on_miss = None

            for member in members:
                weeks = weeks_between(
                    member.get("catalyst_date_iso"), None,
                    snapshot_date or "1970-01-01",
                )
                expectancy = compute_expectancy(
                    p_clinical=parsed.catalyst_outcome["p_clinical"],
                    expected_move_on_hit_pct=parsed.catalyst_outcome["expected_move_on_hit_pct"],
                    expected_move_on_miss_pct=parsed.catalyst_outcome["expected_move_on_miss_pct"],
                    insider_score=member.get("insider_score"),
                    fund_accumulation_score=member.get("fund_accumulation_score"),
                    momentum_score=member.get("momentum_score"),
                    weeks_to_catalyst=weeks,
                    modifiers=mods_cfg,
                    clamps=clamps_cfg,
                )

                is_anchor = (member.get("nct_number") == anchor_nct
                             and member.get("next_catalyst_type") == anchor_type)
                # Cost/tokens land on the anchor row only so
                # SUM(usd_cost) over the run still equals the real bill.
                # Copies carry 0 and point at the anchor via anchor_*.
                if is_anchor and not anchor_attributed:
                    row_usd_cost = res_usd
                    row_input_tokens = res.input_tokens
                    row_output_tokens = res.output_tokens
                    row_cache_read = res.cache_read_tokens
                    row_cache_create = res.cache_creation_tokens
                    row_web_search = res.web_search_calls
                    row_anchor_nct = None        # anchor's own row has NULL anchor_*
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
                    "momentum_score_input":          expectancy.momentum_score_input,
                    "m_insider":  expectancy.m_insider,
                    "m_funds":    expectancy.m_funds,
                    "m_momentum": expectancy.m_momentum,
                    "p_final":    expectancy.p_final,
                    "e_move_pct": expectancy.e_move_pct,
                    "expectancy_pct":          expectancy.expectancy_pct,
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
                upsert_deep_dive_row(dd_conn, row)

            # Persist web_search_cache entries — tag with the actual ticker.
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

        # Close run. "list price" = no-cache, no-batch equivalent — i.e.
        # all cache_read + cache_create tokens billed as regular input.
        # Useful to show realized cache savings.
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

    print()
    print("-" * 70)
    print(f"Module 7 run complete: run_id={run_id}")
    print(f"  Calls dispatched:     {len(results)}")
    print(f"  Successful parses:    {success_count}")
    print(f"  Errors:               {len(results) - success_count}")
    print(f"  Wall time:            {wall_time_s:.1f}s")
    print(f"  Tokens (in / out):    {token_totals['input']:,} / {token_totals['output']:,}")
    print(f"  Cache (read / create):{token_totals['cache_read']:,} / {token_totals['cache_create']:,}")
    print(f"  Web searches:         {token_totals['search_calls']}")
    print(f"  USD cost (estimated): ${token_totals['usd']:,.2f}  (calibrated; verify on console.anthropic.com)")
    print(f"  USD if no caching:    ${list_price_total:,.2f}  (cache_read+cache_create billed as regular input)")
    print("-" * 70)
    if rate_limited:
        print()
        print(f"  RATE-LIMITED ({len(rate_limited)} ticker(s)):")
        print(f"    {','.join(rate_limited)}")
        print(f"  Retry with: --tickers {','.join(rate_limited)} --mode sync --yes -v")


if __name__ == "__main__":
    raise SystemExit(main())
