#!/usr/bin/env python
"""Orchestrator CLI for the Acrivon-pattern screener (spec §2).

Each stage is independently invocable against the store, enabling partial re-runs.

    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_screen.py --stage 0      # 0a + 0b
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_screen.py --stage 0a
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_screen.py --stage 0b --enrich-yf -v

``--enrich-yf`` turns on yfinance market-cap/liveness enrichment (LOCAL prototype only; yfinance is
ToS-limited for public deploy — swap for a licensed provider before hosting). Without it, companies
lacking a cap are KEPT and flagged ``mktcap_unknown`` (never dropped).
"""

import argparse
import logging
import sys

from platform_discoverer import config as cfg
from platform_discoverer import stage0a, stage0b, stage1, stage2, stage4, stage5, tiering
from platform_discoverer.directory import ListingDirectoryProvider
from platform_discoverer.listings import SeedCSVProvider, yfinance_enricher
from platform_discoverer.store import Store, now_iso
from platform_discoverer.taxonomy import TaxonomyTagger

log = logging.getLogger("6_screen")


def _providers(config: dict, *, universe: bool):
    """Stage-0a providers. Seed CSV is always one net; --universe adds the automated directory net."""
    seeds = (config.get("stage0a_nets", {}) or {}).get("seed_lists", []) or []
    providers = [SeedCSVProvider(seeds)]
    if universe:
        providers.append(ListingDirectoryProvider(config))   # SEC US + Wikidata EU/Nordic + yfinance
    return providers


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Acrivon-pattern screener orchestrator")
    p.add_argument("--db", default="data/store.db")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--stage", default="0", choices=["0", "0a", "0b", "1", "2", "4", "5"],
                   help="which stage(s) to run (more added in later milestones)")
    p.add_argument("--out", default="Outputs/shortlist.md",
                   help="Stage 5: path for the exported house-format shortlist Markdown")
    p.add_argument("--taxonomy", default="config/taxonomy.yaml")
    p.add_argument("--dispatch", action="store_true",
                   help="Stage 4: actually call the Claude API (costs money). Default = cost estimate "
                        "only. A [y/N] gate confirms after showing the estimate.")
    p.add_argument("--yes", action="store_true", help="skip the Stage-4 dispatch confirmation gate")
    p.add_argument("--tickers", default=None,
                   help="restrict Stage 2/4 to a comma-separated ticker list (e.g. ACRV,IDYA,RXRX)")
    p.add_argument("--no-batch", action="store_true",
                   help="Stage 4: score the Sonnet pass in real time instead of the Batch API "
                        "(faster for small runs; no waiting on a batch)")
    p.add_argument("--force-rescore", action="store_true",
                   help="Stage 4: re-score even tickers scored within the rescore-TTL (the 'reset'). "
                        "By default a ticker scored in the last year is skipped.")
    p.add_argument("--tiers", default=None,
                   help="Stage 4: which market-cap×age tiers to score, e.g. '1,2' or 'all' "
                        "(1=small&young, 2=large&young, 3=large&old, 4=small&old, 0=untiered). "
                        "Omit for an interactive prompt; with --yes and no --tiers, defaults to all.")
    p.add_argument("--resume", default=None, metavar="RUN_ID",
                   help="Stage 4 crash-recovery: re-attach the batch submitted under this run_id "
                        "(from run_meta) and collect/persist its results without re-dispatching.")
    p.add_argument("--limit", type=int, default=None,
                   help="cap companies processed (Stage 2 harvest) — useful for a bounded first run")
    p.add_argument("--include-excluded", action="store_true",
                   help="Stage 2: harvest stage1_excluded companies too (recall-safe; re-tag after)")
    p.add_argument("--no-incremental", action="store_true",
                   help="Stage 2: force re-harvest, ignoring the freshness TTL (e.g. to refresh "
                        "evidence after adding the pedigree source / FDA-designation extraction)")
    p.add_argument("--universe", action="store_true",
                   help="enumerate the FULL listed universe (SEC US + Wikidata EU/Nordic) as a net, "
                        "not just the seed CSV (network; LOCAL-only yfinance enrich)")
    p.add_argument("--enrich-yf", action="store_true",
                   help="enrich market cap/liveness via yfinance at Stage 0b (LOCAL prototype only)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    config = cfg.load_config(args.config)
    run_id = now_iso()
    tickers = [t.strip() for t in args.tickers.split(",")] if args.tickers else None

    with Store.open(args.db, config=config) as store:
        if args.stage in ("0", "0a"):
            regions = config["run"]["regions"]
            records = [r for prov in _providers(config, universe=args.universe)
                       for r in prov.fetch(regions)]
            summary = stage0a.run(store, records, config, run_id=run_id)
            print(f"stage0a: {summary}")
        if args.stage in ("0", "0b"):
            enricher = yfinance_enricher(config) if args.enrich_yf else None
            max_enrich = int((config.get("stage0b", {}) or {}).get("max_enrich", 0))
            summary = stage0b.run(store, config, run_id=run_id, enricher=enricher,
                                  max_enrich=max_enrich)
            print(f"stage0b: {summary}")
        if args.stage == "1":
            tagger = TaxonomyTagger(cfg.load_taxonomy(args.taxonomy))
            summary = stage1.run(store, tagger, config, run_id=run_id)
            print(f"stage1: {summary}")
        if args.stage == "2":
            summary = stage2.run(store, config, run_id=run_id, limit=args.limit,
                                 include_excluded=args.include_excluded or None,
                                 incremental=not args.no_incremental, tickers=tickers)
            print(f"stage2: {summary}")
        if args.stage == "4" and args.resume:
            summary = stage4.resume_stage4(store, config, run_id=args.resume)
            print(f"stage4 resume: {summary}")
        elif args.stage == "4":
            # IPO-date × market-cap TIER GATE — applied UPSTREAM of the Claude call (spec §5.6).
            # Resolve which tiers to score: explicit --tiers, else interactive prompt, else (with
            # --yes) all tiers.
            breakdown = stage4.due_tier_breakdown(store, config, tickers, force=args.force_rescore)
            if args.tiers is not None:
                sel_tiers = tiering.parse_tier_selection(args.tiers)
            elif args.yes:
                sel_tiers = set(tiering.ALL_TIERS)
            else:
                print("\nDue candidates by tier (market cap × years-since-IPO):")
                for t in tiering.ALL_TIERS:
                    print(f"  [{t}] {tiering.tier_label(t):<28} {breakdown.get(t, 0)}")
                resp = input("Which tiers to run the Claude scorer on? (e.g. 1,2 / all): ")
                sel_tiers = tiering.parse_tier_selection(resp)
            print(f"selected tiers: {sorted(sel_tiers)}")

            est = stage4.run(store, config, dispatch=False, tickers=tickers,
                             force=args.force_rescore, tiers=sel_tiers)
            print(f"stage4 cost estimate: {est}")
            if not args.dispatch:
                print("(estimate only — re-run with --dispatch to score via the Claude API)")
            elif est["candidates"] == 0:
                print("no DUE candidates in the selected tiers — nothing to score.")
            else:
                ok = args.yes
                if not ok:
                    resp = input(f"\nDispatch Claude scoring of {est['candidates']} companies "
                                 f"in tiers {sorted(sel_tiers)} "
                                 f"(~${est['est_total_usd']}, cap ${est['max_usd_per_run']})? [y/N]: ")
                    ok = resp.strip().lower() == "y"
                if ok:
                    summary = stage4.run(store, config, dispatch=True, run_id=run_id,
                                        tickers=tickers, use_batch=not args.no_batch,
                                        force=args.force_rescore, tiers=sel_tiers)
                    print(f"stage4: {summary}")
                else:
                    print("aborted — no API calls made.")
        if args.stage == "5":
            summary = stage5.run(store, config, run_id=run_id, out_path=args.out)
            print(f"stage5: {summary}")
            print(f"shortlist: {summary['shortlist_path']}")
        print(f"companies in store: {store.count_companies()}")
        rq = store.review_queue_dump()
        if rq:
            print(f"review_queue: {len(rq)} entries")

    return 0


if __name__ == "__main__":
    sys.exit(main())
