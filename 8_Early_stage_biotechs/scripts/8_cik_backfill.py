"""One-off: backfill SEC CIKs for active entities that entered WITHOUT one.

Some names arrive via the fund13f / Wikidata providers without a CIK, so the capital-markets signal (which
gates on CIK) can't reach them — even when they ARE genuine SEC registrants (e.g. Assertio, KalVista). This
resolves each no-CIK active entity's ticker → CIK via EDGAR ``browse-edgar getcompany`` (the same path
fund13f uses; free, no key), and sets it ONLY when that CIK isn't already held by another stored entity (no
false merge). Genuinely-foreign, non-SEC-filing names simply return None and are skipped.

Idempotent (skips entities that already have a CIK). Fail-soft per ticker. After running, re-run
``8_signals --capital-markets`` (and ``--designations``) to light up the newly-reachable filers.

Run (from the component dir):
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_cik_backfill.py            # all no-CIK active w/ ticker
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_cik_backfill.py --us-only  # restrict to jurisdiction=US
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_cik_backfill.py --dry-run  # resolve + report, no writes
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

from early_detection.clients import _net
from early_detection.config import load_config
from early_detection.providers.fund13f import _lookup_ticker
from early_detection.store import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill SEC CIKs for no-CIK active entities (EDGAR, free).")
    ap.add_argument("--us-only", action="store_true", help="restrict to jurisdiction=US")
    ap.add_argument("--dry-run", action="store_true", help="resolve + report, write nothing")
    ap.add_argument("--limit", type=int, default=None, help="cap tickers this run")
    ap.add_argument("--per-sec", type=float, default=8.0, help="EDGAR request rate (default 8/s)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    store = Store(cfg.db_path)
    limiter = _net.RateLimiter(per_sec=args.per_sec)

    sql = ("SELECT entity_id, ticker_primary, jurisdiction, legal_name FROM entity "
           "WHERE is_live=1 AND below_floor=0 AND above_ceiling=0 AND cik IS NULL "
           "AND ticker_primary IS NOT NULL AND ticker_primary <> ''")
    if args.us_only:
        sql += " AND jurisdiction='US'"
    sql += " ORDER BY ticker_primary"
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    todo = [dict(r) for r in store.conn.execute(sql)]
    print(f"CIK backfill → {cfg.db_path}   ({len(todo)} no-CIK active entities w/ a ticker"
          f"{', US only' if args.us_only else ''}{', DRY-RUN' if args.dry_run else ''})")

    resolved = collided = unresolved = 0
    examples: list[str] = []
    for e in todo:
        tkr = e["ticker_primary"]
        info = _lookup_ticker(tkr, limiter=limiter)     # EDGAR getcompany → (cik:int, sic, name) | None
        if not info:
            unresolved += 1                             # not an SEC registrant (genuinely foreign) → skip
            continue
        cik10 = f"{info[0]:010d}"
        # collision guard: never assign a CIK already held by a DIFFERENT stored entity (would false-merge)
        other = store.conn.execute("SELECT entity_id FROM entity WHERE cik=? AND entity_id<>?",
                                   (cik10, e["entity_id"])).fetchone()
        if other:
            collided += 1
            log_line = f"  ⚠ {tkr}: CIK {cik10} already held by another entity — skipped (possible dup)"
            if args.verbose:
                print(log_line)
            continue
        if not args.dry_run:
            store.conn.execute("UPDATE entity SET cik=? WHERE entity_id=? AND cik IS NULL",
                               (cik10, e["entity_id"]))
            store.conn.commit()
        resolved += 1
        if len(examples) < 10:
            examples.append(f"    {tkr:8} {e['jurisdiction'] or '?':4} → CIK {cik10}  {(e['legal_name'] or '')[:32]}")

    print(f"\n  scanned:    {len(todo)}")
    print(f"  RESOLVED:   {resolved}   {'(dry-run, not written)' if args.dry_run else '(cik set)'}")
    print(f"  collisions: {collided}   (CIK already held by another entity — skipped, no false merge)")
    print(f"  unresolved: {unresolved}   (no SEC CIK → genuinely foreign / non-filer)")
    if examples:
        print("  sample resolved:")
        print("\n".join(examples))
    if resolved and not args.dry_run:
        print("\n  NEXT: re-run the capital-markets (+designations) signal to reach the new CIKs:")
        print("    PYTHONPATH=src ..\\.venv\\Scripts\\python.exe scripts\\8_signals.py --capital-markets")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
