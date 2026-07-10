"""Module 8 — signal ingestion CLI (Phase 2).

Currently runs the capital-markets signal (SEC EDGAR §3.5): recent 13D/G, Form-4, 8-K, and
registration/shelf filings for the active universe → `signal` rows. Zero-LLM, no spend. Idempotent and
resumable (per-entity commit; re-runs upsert by stable signal_id).

Run (from the component dir):
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --capital-markets --limit 50
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --capital-markets
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_signals.py --stats
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
from early_detection.signals.capital_markets import ingest_capital_markets
from early_detection.store import Store


def _print_stats(store: Store) -> None:
    total = store.count_signals()
    cap = store.count_signals("capital_markets")
    with_sig = store.conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM signal WHERE entity_id IS NOT NULL").fetchone()[0]
    by_form = store.conn.execute(
        "SELECT json_extract(raw_payload_json,'$.form') f, COUNT(*) FROM signal "
        "WHERE signal_type='capital_markets' GROUP BY f ORDER BY 2 DESC").fetchall()
    print(f"\n  signals total:          {total}")
    print(f"  capital_markets:        {cap}")
    print(f"  entities with a signal: {with_sig}")
    if by_form:
        print("  by form: " + ", ".join(f"{f}={n}" for f, n in by_form))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Module 8 Phase-2 signal ingestion.")
    ap.add_argument("--capital-markets", action="store_true", help="ingest EDGAR capital-markets signals")
    ap.add_argument("--stats", action="store_true", help="print signal stats and exit")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of entities this run")
    ap.add_argument("--concurrency", type=int, default=6, help="EDGAR fetch workers (default 6)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    store = Store(cfg.db_path)

    if args.stats and not args.capital_markets:
        _print_stats(store)
        store.close()
        return 0

    if args.capital_markets:
        print(f"Ingesting capital-markets signals → {cfg.db_path}  "
              f"(lookback {cfg.signal_lookback_days}d, forms={list(cfg.material_forms)})")
        res = ingest_capital_markets(store, cfg, limit=args.limit, concurrency=args.concurrency)
        print(f"\n  entities scanned:   {res.entities}")
        print(f"  with ≥1 filing:     {res.with_filings}")
        print(f"  signals written:    {res.signals}")
        print(f"  by form:            {res.by_form}")
        _print_stats(store)
        store.close()
        return 0

    ap.print_help()
    store.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
