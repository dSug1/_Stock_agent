#!/usr/bin/env python
"""7_Momentum_parser orchestrator.

Daily pipeline:
  Stage 1  fetch daily OHLCV   (network; yfinance — local-only)
  Stage 2  compute signals     (free, offline)
  Stage 3  weekly probability   (free, offline)
  Stage 4  rank + export md     (free)

Usage (from the component dir, with the shared venv active):
  PYTHONPATH=src python scripts/7_momentum.py            # full daily run
  PYTHONPATH=src python scripts/7_momentum.py --stage 2  # one stage
  PYTHONPATH=src python scripts/7_momentum.py --no-fetch  # skip the network, recompute from cache
  PYTHONPATH=src python scripts/7_momentum.py --tickers AAPL,MSFT
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from momentum_parser import stage1_prices, stage2_signals, stage3_probability, stage4_export
from momentum_parser.config import db_path, load_config
from momentum_parser.store import Store
from momentum_parser.universe import UniverseRow, load_universe


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ")


def main(argv=None) -> int:
    # Windows consoles are cp1252 and crash on non-cp1252 unicode in log output (σ, ×, →, ⚠).
    # Force UTF-8 so a stray glyph can never abort a run mid-pipeline.
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="Momentum parser — daily signals + weekly probability")
    ap.add_argument("--stage", type=int, choices=[1, 2, 3, 4],
                    help="run a single stage (default: all)")
    ap.add_argument("--no-fetch", action="store_true", help="skip Stage 1 network fetch; use cache")
    ap.add_argument("--harvest", action="store_true",
                    help="run Stage 2 media/search/catalyst harvest (writes evidence+catalysts)")
    ap.add_argument("--score", action="store_true",
                    help="run Stage 3 tiered Claude scoring (dry estimate unless --dispatch)")
    ap.add_argument("--dispatch", action="store_true", help="actually spend on Claude (Stage 3 gate)")
    ap.add_argument("--resume", action="store_true", help="re-poll open Batch jobs for --run-id")
    ap.add_argument("--force-rescore", action="store_true",
                    help="ignore the config/evidence skip and re-score every name")
    ap.add_argument("--debug", action="store_true",
                    help="Stage 3 debug: bypass the $ run budget + cap names + real-time (keeps 12500 cap)")
    ap.add_argument("--blend", action="store_true",
                    help="run Stage 5 hybrid blend + export (p_claude x p_model -> p_final, ledger)")
    ap.add_argument("--settle", action="store_true",
                    help="settle elapsed ledger predictions + write the forward-validation report")
    ap.add_argument("--discover", action="store_true",
                    help="Stage 0a: weekly Claude universe discovery (dry unless --dispatch)")
    ap.add_argument("--gate", action="store_true",
                    help="Stage 0b: liquidity/vol/price gate over discovered candidates (or --tickers)")
    ap.add_argument("--daily", action="store_true",
                    help="run the WHOLE v0.3 pipeline in order (discover weekly -> gate -> prices -> "
                         "harvest -> score[gated] -> blend -> settle -> render); DRY unless --dispatch")
    ap.add_argument("--tickers", help="comma-separated subset, overrides config/universe.csv")
    ap.add_argument("--run-id", help="reuse an existing run id (default: new UTC-stamped id)")
    ap.add_argument("--out", help="Stage 4 output path (default Outputs/signals.md)")
    args = ap.parse_args(argv)

    cfg = load_config()
    store = Store(db_path(cfg))

    # Full daily pipeline — doesn't need an input universe (discovery produces one).
    if args.daily:
        from momentum_parser import daily, render
        from momentum_parser.clients.anthropic_client import AnthropicScorer
        run_id = args.run_id or _run_id()
        print(f"[7] DAILY run {run_id} · {'DISPATCH (billed)' if args.dispatch else 'DRY (no spend)'}")
        funnel = daily.run(store, cfg, run_id, AnthropicScorer(cfg),
                           dispatch=args.dispatch, fetch=not args.no_fetch)
        rpt = render.render(store, cfg, run_id)
        print(f"[7] daily done · report {rpt} · {funnel}")
        store.close()
        return 0

    # Stage 0a — discovery: doesn't need an input universe (it *produces* one).
    if args.discover:
        from momentum_parser import stage0_discovery
        from momentum_parser.clients.anthropic_client import AnthropicScorer
        run_id = args.run_id or _run_id()
        print(f"[7] Stage 0a: Claude universe discovery · run {run_id}"
              + ("" if args.dispatch else " (DRY — pass --dispatch to spend)"))
        print("   ", stage0_discovery.run(store, cfg, run_id, AnthropicScorer(cfg), dispatch=args.dispatch))
        store.close()
        return 0

    if args.tickers:
        universe = [UniverseRow(t.strip().upper(), "") for t in args.tickers.split(",") if t.strip()]
    elif args.gate:
        # Stage 0b gates the discovered candidates by default.
        universe = [UniverseRow(r["ticker"], r["name"]) for r in store.latest_discovery()]
    else:
        universe = load_universe(cfg, store)        # prefer the gate-pass list, else seed CSV
    tickers = [u.ticker for u in universe]
    if not tickers:
        print("[7] no tickers — run --discover, populate config/universe.csv, or pass --tickers")
        return 1

    if args.gate:
        from momentum_parser import stage0_gate
        print(f"[7] Stage 0b: liquidity/vol/price gate · {len(tickers)} candidates")
        out = stage0_gate.run(store, tickers, cfg)
        print(f"[7] gate: {out['passed']} pass · {out['by_status']}")
        store.close()
        return 0

    if args.harvest:
        from momentum_parser import stage2_harvest
        print(f"[7] Stage 2: media/search/catalyst harvest · {len(tickers)} tickers")
        print("   ", stage2_harvest.run(store, tickers, cfg))
        store.close()
        return 0

    if args.settle:
        from momentum_parser import feedback, validation
        print("[7] §9: settling elapsed ledger predictions...")
        print("   ", validation.settle_pass(store, cfg))
        print("[7] M16: feedback loop — settle catalyst hypotheses + learn signal weights...")
        print("   ", feedback.run(store, cfg))
        path, summ = validation.build_report(store, cfg)
        print(f"[7] validation: {path} · {summ}")
        store.close()
        return 0

    if args.blend:
        from momentum_parser import stage4_export, stage5_blend
        run_id = args.run_id or _run_id()
        print(f"[7] Stage 5: hybrid blend + export · run {run_id}")
        print("   ", stage5_blend.run(store, tickers, cfg, run_id))
        path = stage4_export.run(store, cfg, run_id, out=args.out)
        print(f"[7] signals: {path}")
        store.close()
        return 0

    if args.score:
        from momentum_parser import stage3_score
        from momentum_parser.clients.anthropic_client import AnthropicScorer
        run_id = args.run_id or _run_id()
        if not cfg.get("claude", {}).get("enabled", False) and args.dispatch:
            print("[7] WARNING: claude.enabled is false in config — proceeding because --dispatch was passed.")
        scorer = AnthropicScorer(cfg)
        print(f"[7] Stage 3: tiered Claude scoring · run {run_id}"
              + ("" if args.dispatch else " (DRY — pass --dispatch to spend)"))
        print("   ", stage3_score.run(store, tickers, cfg, run_id, scorer, dispatch=args.dispatch,
                                      force=args.force_rescore, resume=args.resume, debug=args.debug))
        store.close()
        return 0

    run_id = args.run_id or _run_id()
    stages = [args.stage] if args.stage else [1, 2, 3, 4]
    print(f"[7] momentum run {run_id} · {len(tickers)} tickers · stages {stages}")

    if 1 in stages and not args.no_fetch:
        print("[7] Stage 1: fetching daily OHLCV...")
        print("   ", stage1_prices.run(store, universe, cfg))
    if 2 in stages:
        print("[7] Stage 2: computing trade signals...")
        print("   ", stage2_signals.run(store, tickers, cfg))
    if 3 in stages:
        print("[7] Stage 3: weekly price-change probability...")
        print("   ", stage3_probability.run(store, tickers, cfg, run_id))
    if 4 in stages:
        print("[7] Stage 4: ranking + export...")
        path = stage4_export.run(store, cfg, run_id, out=args.out)
        print(f"[7] done. Signals: {path}")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
