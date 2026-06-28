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
from platform_discoverer import stage0a, stage0b
from platform_discoverer.listings import SeedCSVProvider, yfinance_enricher
from platform_discoverer.store import Store, now_iso

log = logging.getLogger("6_screen")


def _providers(config: dict):
    seeds = (config.get("stage0a_nets", {}) or {}).get("seed_lists", []) or []
    return [SeedCSVProvider(seeds)]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Acrivon-pattern screener orchestrator")
    p.add_argument("--db", default="data/store.db")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--stage", default="0", choices=["0", "0a", "0b"],
                   help="which stage(s) to run (more added in later milestones)")
    p.add_argument("--enrich-yf", action="store_true",
                   help="enrich market cap/liveness via yfinance (LOCAL prototype only)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    config = cfg.load_config(args.config)
    run_id = now_iso()

    with Store.open(args.db, config=config) as store:
        if args.stage in ("0", "0a"):
            records = [r for prov in _providers(config) for r in prov.fetch(config["run"]["regions"])]
            summary = stage0a.run(store, records, config, run_id=run_id)
            print(f"stage0a: {summary}")
        if args.stage in ("0", "0b"):
            enricher = yfinance_enricher() if args.enrich_yf else None
            summary = stage0b.run(store, config, run_id=run_id, enricher=enricher)
            print(f"stage0b: {summary}")
        print(f"companies in store: {store.count_companies()}")
        rq = store.review_queue_dump()
        if rq:
            print(f"review_queue: {len(rq)} entries")

    return 0


if __name__ == "__main__":
    sys.exit(main())
