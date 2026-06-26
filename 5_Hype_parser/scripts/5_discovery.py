#!/usr/bin/env python
"""Theme-discovery (jury-convergence) CLI — Workstream 2 (decisions D22-D26; spec discovery_spec_v0.2).

Discovers nascent themes from the convergence of independent expert juries — award shortlists,
breakthrough designations, agency calls, specialist-fund new positions — instead of hand-seeding
(D21). Zero Claude; tiny footprint. Pipeline:

    # 1. ingest: pull the build-first machine-readable juries (YC, Nobel) + parse archived snapshot
    #    juries into jury_signals, annotate from the registry, embed locally.
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_discovery.py --ingest --since-year 2016 -v
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_discovery.py --ingest --no-fetch -v   # snapshots only

    # 2. converge: cluster signals, score by jury convergence, promote themes, classify orgs listed/private
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_discovery.py --converge -v

    # 3. inspect
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_discovery.py --list
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_discovery.py --watch      # Track-B listing watch

Network etiquette: the build-first feeds are public APIs/JSON; --no-fetch keeps it fully offline
(parses only what the OD-2 forward archive already captured + recomputes from the DB).
"""

import argparse
import logging
import sys

from hype_parser import db
from hype_parser.discovery import convergence, parsers, resolve, signals
from hype_parser.embed import get_embedder
from hype_parser.registry import load_config

DEFAULT_DB = "data/hype.db"
DISCOVERY_CFG = "config/discovery.yaml"

log = logging.getLogger("5_discovery")


def _snapshot_jury_sources(conn) -> list[str]:
    """Award juries (edge_type=awards) that have an archived snapshot but no clean API parser yet."""
    rows = conn.execute(
        "SELECT DISTINCT s.source_id FROM sources s "
        "JOIN source_snapshots ss ON ss.source_id=s.source_id "
        "WHERE s.edge_type='awards' AND ss.content IS NOT NULL ORDER BY s.source_id").fetchall()
    return [r["source_id"] for r in rows if r["source_id"] not in parsers.API_FETCHERS]


def cmd_ingest(conn, args) -> None:
    total = {"inserted": 0, "skipped": 0}
    if not args.no_fetch:
        for sid, fetcher in parsers.API_FETCHERS.items():
            rows = fetcher(since_year=args.since_year)
            res = signals.upsert_signals(conn, rows)
            total["inserted"] += res["inserted"]
            total["skipped"] += res["skipped"]
            log.info("API jury %-14s parsed=%-5d inserted=%-5d", sid, len(rows), res["inserted"])
    for sid in _snapshot_jury_sources(conn):
        rows = parsers.parse_snapshot_source(conn, sid)
        if rows:
            res = signals.upsert_signals(conn, rows)
            total["inserted"] += res["inserted"]
            total["skipped"] += res["skipped"]
            log.info("snapshot jury %-18s parsed=%-4d inserted=%-4d", sid, len(rows), res["inserted"])
    annotated = signals.annotate_from_registry(conn)
    embedded = signals.embed_pending(conn, get_embedder())
    c = signals.signal_counts(conn)
    print(f"ingest: +{total['inserted']} new signals ({total['skipped']} dup), "
          f"annotated {annotated}, embedded {embedded}")
    print(f"  total signals={c['total']} sources={c['n_sources']} by_position={c['by_position']}")


def cmd_converge(conn, args) -> None:
    cfg = load_config(args.discovery_cfg)
    embedder = get_embedder()
    sigs = signals.load_embedded_signals(conn, embedder.name)
    if not sigs:
        print("no embedded signals — run --ingest first")
        return
    candidates = convergence.build_convergence(sigs, cfg)
    theme_ids = convergence.promote(conn, candidates, embed_model=embedder.name)
    print(f"converge: {len(sigs)} signals -> {len(candidates)} candidate themes "
          f"(tau={cfg['convergence']['tau_converge']}); promoted {len(theme_ids)}")
    index = {} if args.no_fetch else resolve.load_name_index()
    min_conf = cfg["resolve"]["match_min_confidence"]
    roll = {"listed": 0, "private": 0, "unknown": 0}
    for tid in theme_ids:
        if index:
            r = resolve.resolve_theme_orgs(conn, tid, index, min_conf=min_conf)
            for k in roll:
                roll[k] += r[k]
    if index:
        print(f"  org resolution: {roll['listed']} listed (Track A) / {roll['private']} private "
              f"(Track B) / {roll['unknown']} unknown")
    else:
        print("  org resolution skipped (--no-fetch; needs SEC ticker map)")
    for c in candidates[:12]:
        d = c["diag"]
        print(f"  [{c['score']:5.2f}] {c['label'][:42]:42} "
              f"sigs={d['n_signals']:3} lead_juries={d['n_leading_juries']} "
              f"~{c['horizon']['years']}yr  {sorted(d['positions'])}")


def cmd_list(conn) -> None:
    rows = convergence.list_discovered(conn)
    if not rows:
        print("no discovered themes yet — run --ingest then --converge")
        return
    print(f"{len(rows)} discovered themes:")
    for r in rows:
        ta = resolve.track_a_tickers(conn, r["theme_id"])
        print(f"  {r['theme_id']:40} ~{r['horizon_years']}yr ({r['horizon_confidence']}) "
              f"trackA={len(ta)} {r['by_position']}")
        if ta:
            print(f"      tickers: {', '.join(ta[:15])}")


def cmd_watch(conn) -> None:
    rows = resolve.listing_watch_orgs(conn)
    print(f"{len(rows)} private orgs on EDGAR listing-watch (Track B):")
    for r in rows:
        print(f"  {r['org_name'][:48]:48} theme={r['theme_id']:34} src={r['source_id']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hype Parser theme discovery (jury convergence).")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--discovery-cfg", default=DISCOVERY_CFG)
    p.add_argument("--ingest", action="store_true", help="parse juries -> jury_signals + embed")
    p.add_argument("--converge", action="store_true", help="cluster -> score -> promote themes + resolve orgs")
    p.add_argument("--list", action="store_true", help="list discovered themes")
    p.add_argument("--watch", action="store_true", help="list Track-B listing-watch orgs")
    p.add_argument("--no-fetch", action="store_true", help="no network: snapshots/DB only")
    p.add_argument("--since-year", type=int, default=None, help="bound jury history to >= this year")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    conn = db.connect(args.db)
    try:
        did = False
        if args.ingest:
            cmd_ingest(conn, args); did = True
        if args.converge:
            cmd_converge(conn, args); did = True
        if args.watch:
            cmd_watch(conn); did = True
        if args.list or not did:
            cmd_list(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
