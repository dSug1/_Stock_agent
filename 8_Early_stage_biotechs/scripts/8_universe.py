"""Module 8 — Phase 1 universe CLI.

Build/refresh the biotech early-detection universe (US + Canada + Module-6 priority-tier seed) into
``data/early_detection.db``. Nothing here spends money (no Claude).

Run (from the component dir):
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --stats
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --dry-run
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_universe.py --no-ca --max-pages 5
"""

from __future__ import annotations

import argparse
import logging
import sys

# Windows consoles default to cp1252, which can't encode the arrows/`$` glyphs we print (and that the
# INFO logs carry). Force UTF-8 with replacement so output never crashes the run on a stray glyph.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — older/odd streams without reconfigure
        pass

from early_detection.config import load_config
from early_detection.store import Store
from early_detection.universe import build_universe


def _print_stats(store: Store) -> None:
    total = store.count_entities()
    universe_tier = store.conn.execute(
        "SELECT COUNT(*) FROM entity WHERE in_existing_universe=1 AND is_live=1").fetchone()[0]
    unknown_cap = store.conn.execute(
        "SELECT COUNT(*) FROM entity WHERE mktcap_unknown=1 AND is_live=1").fetchone()[0]
    by_sector = store.conn.execute(
        "SELECT COALESCE(sector_code_normalized,'(none)') s, COUNT(*) FROM entity WHERE is_live=1 "
        "GROUP BY s ORDER BY 2 DESC").fetchall()
    by_country = store.conn.execute(
        "SELECT COALESCE(jurisdiction,'(none)') c, COUNT(*) FROM entity WHERE is_live=1 "
        "GROUP BY c ORDER BY 2 DESC LIMIT 12").fetchall()
    print(f"\n  entities (live):        {total}")
    print(f"  in_existing_universe:   {universe_tier}  (Module-6 priority tier)")
    print(f"  unknown market cap:     {unknown_cap}")
    print(f"  reconciliation queue:   {store.count_recon()}")
    print("  by sector: " + ", ".join(f"{s}={n}" for s, n in by_sector))
    print("  by country: " + ", ".join(f"{c}={n}" for c, n in by_country))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Module 8 Phase-1 universe builder (US+CA+M6 seed).")
    ap.add_argument("--dry-run", action="store_true", help="build + reconcile but do not write the DB")
    ap.add_argument("--stats", action="store_true", help="print store stats and exit (no build)")
    ap.add_argument("--no-m6", action="store_true", help="skip the Module-6 priority-tier seed")
    ap.add_argument("--no-us", action="store_true", help="skip US EDGAR enumeration")
    ap.add_argument("--no-ca", action="store_true", help="skip Canada FPI enumeration")
    ap.add_argument("--nordic", action="store_true",
                    help="OPT-IN: add Nordic (SE/DK/NO/FI/IS) biotech listings via Wikidata (no market "
                         "cap yet → mktcap_unknown; run a Nordic cap-enrich before scoring)")
    ap.add_argument("--europe", action="store_true",
                    help="OPT-IN: add broad-EU (DE/FR/UK/CH/NL/BE/IT/ES/IE/AT) biotech listings via "
                         "Wikidata (mktcap_unknown; run a cap-enrich before scoring)")
    ap.add_argument("--ca-wikidata", action="store_true",
                    help="OPT-IN: add Canada (TSX/TSXV) biotech listings via Wikidata — complements the "
                         "edgar_canada FPI path (thin coverage; mktcap_unknown)")
    ap.add_argument("--max-pages", type=int, default=30, help="EDGAR pagination cap per SIC (default 30)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    store = Store(cfg.db_path)

    if args.stats and not args.dry_run:
        _print_stats(store)
        store.close()
        return 0

    print(f"Building universe → {cfg.db_path}")
    print(f"  markets={cfg.markets}  floor=${cfg.mktcap_floor_usd:,.0f}  "
          f"SIC={sorted(cfg.sic_allow)}  m6={cfg.m6_store_path.name}")
    if args.dry_run:
        print("  (dry-run — no DB writes)")

    res = build_universe(store, cfg, use_m6=not args.no_m6, use_us=not args.no_us,
                         use_ca=not args.no_ca, use_nordic=args.nordic, use_europe=args.europe,
                         use_ca_wikidata=args.ca_wikidata,
                         max_pages=args.max_pages, dry_run=args.dry_run)

    print(f"\nrun {res.run_id} — funnel:")
    for k, v in res.counts.items():
        print(f"    {k:22} {v}")
    if not args.dry_run:
        _print_stats(store)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
