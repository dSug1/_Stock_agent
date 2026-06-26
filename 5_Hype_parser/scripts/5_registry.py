#!/usr/bin/env python
"""Hype Parser source registry CLI (M1).

Run from inside 5_Hype_parser/ with PYTHONPATH=src:

    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_registry.py --seed
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_registry.py --list --edge science
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_registry.py --verify arxiv
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_registry.py --freeze --label v1 --notes "pre-backtest"
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_registry.py --versions
"""

import argparse
import logging
import sys

from hype_parser import db, registry

DEFAULT_DB = "data/hype.db"
DEFAULT_CONFIG = "config/sources.yaml"


def _print_sources(rows) -> None:
    if not rows:
        print("(no sources)")
        return
    hdr = f"{'source_id':24} {'edge':12} {'tier':6} {'diff_pos':11} {'signal':15} {'history':12} {'ok':2}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['source_id']:24} {r['edge_type'] or '':12} {r['tier'] or '':6} "
            f"{r['diffusion_position'] or '':11} {r['signal_type'] or '':15} "
            f"{r['history_availability'] or '':12} {'Y' if r['scrapeability_verified'] else '-':2}"
        )
    n_ver = sum(1 for r in rows if r["scrapeability_verified"])
    n_fwd = sum(1 for r in rows if r["history_availability"] == "forward_only")
    print(f"\n{len(rows)} sources | {n_ver} scrape-verified | "
          f"{n_fwd} forward_only (need archiving for the panel era)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hype Parser source registry (M1).")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--seed", nargs="?", const=DEFAULT_CONFIG, metavar="CONFIG",
                   help="seed/refresh sources from a YAML config (default config/sources.yaml)")
    p.add_argument("--update", action="store_true",
                   help="with --seed, update existing rows instead of skipping them")
    p.add_argument("--list", action="store_true", help="list sources (default action)")
    p.add_argument("--edge", help="filter --list by edge_type")
    p.add_argument("--all", action="store_true", help="include disabled sources in --list")
    p.add_argument("--verify", metavar="SOURCE_ID", help="mark scrapeability_verified=1")
    p.add_argument("--unverify", metavar="SOURCE_ID", help="mark scrapeability_verified=0")
    p.add_argument("--freeze", action="store_true",
                   help="snapshot the current registry into an immutable version (PIT)")
    p.add_argument("--label", help="label for --freeze (default v<N+1>)")
    p.add_argument("--notes", help="notes for --freeze")
    p.add_argument("--versions", action="store_true", help="list frozen registry versions")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    conn = db.connect(args.db)
    try:
        acted = False
        if args.seed:
            cfg = registry.load_config(args.seed)
            res = registry.seed_from_config(conn, cfg, update=args.update)
            print(f"seed: inserted={res['inserted']} updated={res['updated']} "
                  f"skipped={res['skipped']}")
            acted = True
        if args.verify:
            registry.set_verified(conn, args.verify, True)
            print(f"verified: {args.verify}")
            acted = True
        if args.unverify:
            registry.set_verified(conn, args.unverify, False)
            print(f"unverified: {args.unverify}")
            acted = True
        if args.freeze:
            v = registry.freeze(conn, label=args.label, notes=args.notes)
            print(f"froze {v['label']}: n_sources={v['n_sources']} "
                  f"hash={v['content_hash'][:12]} at {v['frozen_at']}")
            acted = True
        if args.versions:
            vers = registry.list_versions(conn)
            if not vers:
                print("(no frozen versions)")
            for v in vers:
                print(f"{v['label']:>8}  {v['frozen_at']}  n={v['n_sources']:>3}  "
                      f"{v['content_hash'][:12]}  {v['notes'] or ''}")
            acted = True
        if args.list or not acted:
            _print_sources(registry.list_sources(
                conn, edge_type=args.edge, enabled_only=not args.all))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
