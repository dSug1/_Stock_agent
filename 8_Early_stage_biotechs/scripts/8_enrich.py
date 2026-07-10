"""Module 8 — market-cap enrichment CLI.

Fills USD market caps for unknown-cap entities (yfinance, LOCAL-ONLY ToS) so the $10M floor gates the
active universe. Persists each result immediately; safe to re-run and to interrupt.

Run (from the component dir):
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py --limit 50   # smoke a batch
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py               # full pass
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_enrich.py --recompute-floor
"""

from __future__ import annotations

import argparse
import logging
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from early_detection.config import load_config
from early_detection.enrich import enrich_caps, enrich_lei
from early_detection.store import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Module 8 enrichment: market caps (yfinance) or LEIs (GLEIF).")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of entities enriched this run")
    ap.add_argument("--per-sec", type=float, default=3.0, help="request rate (caps 3/s, LEI 5/s default)")
    ap.add_argument("--lei", action="store_true",
                    help="backfill LEIs via GLEIF (high-precision) instead of market caps")
    ap.add_argument("--concurrency", type=int, default=8,
                    help="GLEIF fetch workers for --lei (default 8 ≈ 12/s; 429-retry is the safety net)")
    ap.add_argument("--recompute-floor", action="store_true",
                    help="only recompute below_floor from existing caps (after a floor change), no fetch")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    store = Store(cfg.db_path)

    if args.lei:
        print(f"Backfilling LEIs via GLEIF → {cfg.db_path}  (high-precision, concurrency={args.concurrency})")
        lr = enrich_lei(store, cfg, limit=args.limit, concurrency=args.concurrency)
        print(f"\n  attempted:  {lr.attempted}")
        print(f"  filled:     {lr.filled}")
        print(f"  misses:     {lr.misses}  (no confident match)")
        print(f"  collisions: {lr.collisions}  (queued for review, not set)")
        with_lei = store.conn.execute(
            "SELECT COUNT(*) FROM entity WHERE is_live=1 AND lei IS NOT NULL AND lei!=''").fetchone()[0]
        print(f"\n  entities with an LEI: {with_lei}")
        store.close()
        return 0

    if args.recompute_floor:
        n = store.recompute_floors(cfg.mktcap_floor_usd)
        print(f"recomputed below_floor at ${cfg.mktcap_floor_usd:,.0f}: {n} entities below floor")
        store.close()
        return 0

    print(f"Enriching market caps → {cfg.db_path}  (floor ${cfg.mktcap_floor_usd:,.0f}, yfinance local-only)")
    res = enrich_caps(store, cfg, limit=args.limit, per_sec=args.per_sec)
    print(f"\n  attempted:   {res.attempted}")
    print(f"  filled:      {res.filled}")
    print(f"  below floor: {res.below_floor}")
    print(f"  misses:      {res.misses}  (kept + still mktcap_unknown)")
    print(f"  by currency: {res.by_ccy}")

    active = store.conn.execute(
        "SELECT COUNT(*) FROM entity WHERE is_live=1 AND below_floor=0 AND mktcap_unknown=0").fetchone()[0]
    unknown = store.conn.execute(
        "SELECT COUNT(*) FROM entity WHERE is_live=1 AND mktcap_unknown=1").fetchone()[0]
    print(f"\n  active universe (known cap, above floor): {active}")
    print(f"  still unknown cap:                        {unknown}")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
