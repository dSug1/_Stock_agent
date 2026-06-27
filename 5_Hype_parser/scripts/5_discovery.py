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
import threading
import webbrowser
from datetime import date
from pathlib import Path

from hype_parser import db, render_discovery
from hype_parser.discovery import (assess, calendar as cal, convergence, diffusion_bridge, funds,
                                   nascency, parsers, resolve, signals)
from hype_parser.embed import get_embedder
from hype_parser.registry import load_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = "data/hype.db"
DISCOVERY_CFG = "config/discovery.yaml"
DIFFUSION_CFG = "config/diffusion.yaml"
SPECIALIST_FUNDS_CFG = "config/specialist_funds.yaml"
CALENDAR_CFG = "config/discovery_calendar.yaml"
FUNDS_DB = "../2_Funds_parser/2_fundparser.db"   # reuse 2_Funds' 13F holdings (D3/D25)
REPORT_OUT = "_intermediate_outputs/discovery_report.html"

log = logging.getLogger("5_discovery")


def _snapshot_jury_sources(conn) -> list[str]:
    """Award juries (edge_type=awards) that have an archived snapshot but no clean API parser yet."""
    rows = conn.execute(
        "SELECT DISTINCT s.source_id FROM sources s "
        "JOIN source_snapshots ss ON ss.source_id=s.source_id "
        "WHERE s.edge_type='awards' AND ss.content IS NOT NULL ORDER BY s.source_id").fetchall()
    return [r["source_id"] for r in rows if r["source_id"] not in parsers.API_FETCHERS]


def cmd_ingest(conn, args, *, due=None) -> None:
    """Ingest jury signals. ``due`` (a set of source_ids) limits the fetch to calendar-due sources
    (the weekly run); None = all parseable sources."""
    total = {"inserted": 0, "skipped": 0}
    if not args.no_fetch:
        for sid, fetcher in parsers.API_FETCHERS.items():
            if due is not None and sid not in due:
                continue
            rows = fetcher(since_year=args.since_year)
            res = signals.upsert_signals(conn, rows)
            total["inserted"] += res["inserted"]
            total["skipped"] += res["skipped"]
            log.info("API jury %-14s parsed=%-5d inserted=%-5d", sid, len(rows), res["inserted"])
    for sid in _snapshot_jury_sources(conn):
        if due is not None and sid not in due:
            continue
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


def _diffusion_params(path):
    cfg = load_config(path)
    return {**cfg["membership"], **cfg["diffusion"]}


def cmd_assess(conn, args) -> None:
    """§9-step-5: fuse jury-timeline + corpus-diffusion per discovered theme and compare to the
    hand-seeded baseline. Needs the radar to have measured themes (5_radar.py --include-discovered)."""
    cfg = load_config(args.discovery_cfg)
    params = _diffusion_params(args.diffusion_cfg)
    res = assess.compare(conn, cfg, params, current_year=args.current_year)
    s = res["summary"]
    print(f"assessment: {s['n_discovered']} discovered themes "
          f"({s['n_discovered_measured']} measured on the diffusion engine), "
          f"{s['n_seed_measured']} seed themes measured")
    print(f"  median b_spec -- discovered: {s['median_discovered_beta_spec']}  "
          f"seed baseline: {s['median_seed_beta_spec']}")
    print("\n  DISCOVERED (fused jury-timeline x corpus):")
    print(f"    {'comb':>5} {'jury':>5} {'bjury':>6} {'bspec':>6} {'p_main':>6} gate  theme")
    for r in res["discovered"]:
        c = r["corpus"]
        bs = f"{c['beta_spec']:6.2f}" if c else "   n/a"
        pm = f"{c['p_main']:6.2f}" if c else "   n/a"
        gate = ("Y" if c["nascency_gate"] else "n") if c else "-"
        print(f"    {r['combined_score']:5.2f} {r['rank_score']:5.2f} {r['beta_jury']:6.2f} "
              f"{bs} {pm}  {gate}   {r['label'][:42]}")
    print("\n  HAND-SEEDED baseline (corpus):")
    print(f"    {'bspec':>6} {'p_main':>6} gate  theme")
    for r in res["seed_baseline"]:
        c = r["corpus"]
        print(f"    {c['beta_spec']:6.2f} {c['p_main']:6.2f}  {'Y' if c['nascency_gate'] else 'n'}   "
              f"{r['label'][:42]}")


def _today(args) -> date:
    return date.fromisoformat(args.as_of) if args.as_of else date.today()


def _ingestable(conn) -> set:
    """Sources discovery can actually parse: the build-first APIs + archived snapshot juries."""
    return set(parsers.API_FETCHERS) | set(_snapshot_jury_sources(conn))


def cmd_due(conn, args) -> None:
    """Show which jury sources the weekly run would fetch on a given date (spec §5a release calendar)."""
    calendar = cal.load_calendar(args.calendar_cfg)
    today = _today(args)
    ingestable = _ingestable(conn)
    due = cal.due_jury_sources(conn, calendar, today=today, restrict_to=ingestable)
    funds_due = cal.funds_due(calendar, today=today)
    print(f"as of {today}: {len(due)} of {len(ingestable)} ingestable jury sources DUE")
    for sid in due:
        cy = cal.captured_year(conn, sid)
        print(f"  {sid:24} (captured_year={cy})")
    print(f"  specialist-fund 13F cross-ref DUE: {funds_due} (post-13F-deadline window)")


def cmd_weekly(conn, args) -> None:
    """The weekly run: fetch only calendar-due juries, then converge -> rank -> report (spec §5a)."""
    calendar = cal.load_calendar(args.calendar_cfg)
    today = _today(args)
    due = set(cal.due_jury_sources(conn, calendar, today=today, restrict_to=_ingestable(conn)))
    print(f"weekly run ({today}): {len(due)} due jury sources -> {sorted(due)}")
    cmd_ingest(conn, args, due=due)
    cmd_converge(conn, args)
    cmd_rank(conn, args)
    cmd_report(conn, args)
    if cal.funds_due(calendar, today=today):
        print("(13F window open -> running specialist-fund cross-ref)")
        cmd_funds(conn, args)


def cmd_report(conn, args) -> None:
    """Render the discovery HTML diagnostic: ranked themes (convergence + nascency + corpus + Track
    A/B + smart-money) vs the hand-seeded baseline."""
    cfg = load_config(args.discovery_cfg)
    params = _diffusion_params(args.diffusion_cfg)
    new_buys = None
    try:
        sf = funds.load_specialist_funds(args.specialist_funds_cfg)
        new_buys = funds.new_buys_from_2funds(args.funds_db, sf, quarter=args.quarter)
    except Exception as exc:                                  # report works without smart-money
        log.info("smart-money skipped (%s)", exc)
    report = assess.build_report(conn, cfg, params, current_year=args.current_year, new_buys=new_buys)
    out_path = ROOT / REPORT_OUT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    render_discovery.render(report, out_path=str(out_path))
    print(f"rendered {len(report['themes'])} discovered themes -> {out_path}")
    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(out_path.as_uri())).start()


def cmd_diffusion_queries(conn, args) -> None:
    """Assign zero-Claude diffusion queries to discovered themes so the radar can measure their
    corpus β_spec/p_main (spec §4 step 4). Then run: 5_radar.py --include-discovered."""
    n = diffusion_bridge.assign_diffusion_queries(conn, overwrite=args.overwrite_queries)
    print(f"assigned diffusion queries to {n} discovered themes "
          f"({'overwrote existing' if args.overwrite_queries else 'skipped already-set'})")
    for t in diffusion_bridge.load_discovered_radar_themes(conn):
        print(f"  {t['id']:42} arxiv_query={t['arxiv_query']}")
    if n:
        print("\nnext: PYTHONPATH=src ../.venv/Scripts/python.exe scripts/5_radar.py --include-discovered -v")


def cmd_rank(conn, args) -> None:
    """Rank discovered themes by the jury-timeline nascency gate (convergence × acceleration × recency)."""
    cfg = load_config(args.discovery_cfg)
    ranked = nascency.rank_discovered(conn, cfg, current_year=args.current_year)
    if not ranked:
        print("no discovered themes to rank — run --ingest then --converge")
        return
    if args.persist_horizon:
        n = nascency.persist_refined_horizon(conn, ranked)
        print(f"persisted timeline-refined horizon for {n} themes")
    print(f"{len(ranked)} discovered themes ranked (nascency gate):")
    print(f"  {'rank':>5}  {'conv':>4} {'b_jury':>6} {'recency':>7} {'~runway':>7}  theme")
    for r in ranked:
        print(f"  {r['rank_score']:5.2f}  {r['convergence_score']:4.1f} {r['beta_jury']:6.2f} "
              f"{r['recency']:7.2f} {r['refined_horizon_years']:6.1f}y  {r['label'][:48]}")


def cmd_funds(conn, args) -> None:
    """Specialist-fund smart-money confirmation on discovered themes' Track-A tickers (spec §4b).
    Crosses 2_Funds' newest-quarter NEW 13F positions against each discovered theme's listed names."""
    cfg = load_config(args.discovery_cfg)
    sf = funds.load_specialist_funds(args.specialist_funds_cfg)
    new_buys = funds.new_buys_from_2funds(args.funds_db, sf, quarter=args.quarter)
    if not new_buys:
        print(f"no new 13F positions read from {args.funds_db} "
              "(check the 2_Funds DB path / that it has holdings)")
        return
    n_new = sum(len(v) for v in new_buys.values())
    print(f"funds: {n_new} new specialist positions across {len(new_buys)} tickers "
          f"(newest quarter in {args.funds_db})")
    themes = convergence.list_discovered(conn)
    any_hit = False
    for t in themes:
        ta = resolve.track_a_tickers(conn, t["theme_id"])
        hits = funds.cross_reference(ta, new_buys, cfg["funds"])
        if hits:
            any_hit = True
            print(f"  {t['theme_id']}:")
            for h in hits:
                print(f"    {h['ticker']:6} smart_money={h['smart_money_score']:.2f} "
                      f"buyers={', '.join(h['buyers'][:4])}")
    if not any_hit:
        print("  no discovered-theme Track-A ticker overlaps a specialist new buy this quarter "
              "(expected until biotech/listed-heavy themes are discovered)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hype Parser theme discovery (jury convergence).")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--discovery-cfg", default=DISCOVERY_CFG)
    p.add_argument("--diffusion-cfg", default=DIFFUSION_CFG)
    p.add_argument("--specialist-funds-cfg", default=SPECIALIST_FUNDS_CFG)
    p.add_argument("--calendar-cfg", default=CALENDAR_CFG)
    p.add_argument("--as-of", default=None, help="reference date YYYY-MM-DD for --due/--weekly (default today)")
    p.add_argument("--funds-db", default=FUNDS_DB, help="2_Funds_parser holdings DB (13F new positions)")
    p.add_argument("--quarter", default=None, help="period_of_report (YYYY-MM-DD) for --funds; default newest")
    p.add_argument("--current-year", type=int, default=None, help="reference year for --rank (default: now)")
    p.add_argument("--persist-horizon", action="store_true", help="--rank: write refined horizon to themes")
    p.add_argument("--weekly", action="store_true",
                   help="weekly run: fetch only calendar-due juries -> converge -> rank -> report (spec 5a)")
    p.add_argument("--due", action="store_true", help="show which jury sources are due to fetch (calendar)")
    p.add_argument("--ingest", action="store_true", help="parse juries -> jury_signals + embed")
    p.add_argument("--converge", action="store_true", help="cluster -> score -> promote themes + resolve orgs")
    p.add_argument("--diffusion-queries", action="store_true",
                   help="assign zero-Claude diffusion queries to discovered themes (then 5_radar --include-discovered)")
    p.add_argument("--overwrite-queries", action="store_true", help="--diffusion-queries: re-derive even if set")
    p.add_argument("--rank", action="store_true", help="rank discovered themes by the nascency gate (spec 3a)")
    p.add_argument("--assess", action="store_true",
                   help="fuse jury-timeline + corpus diffusion; compare vs hand-seeded baseline (spec 9.5)")
    p.add_argument("--report", action="store_true", help="render the discovery HTML diagnostic")
    p.add_argument("--open-browser", action="store_true", help="--report: open the HTML when done")
    p.add_argument("--funds", action="store_true", help="specialist-fund smart-money confirmation (spec 4b)")
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
        if args.weekly:
            cmd_weekly(conn, args); did = True
        if args.due:
            cmd_due(conn, args); did = True
        if args.ingest:
            cmd_ingest(conn, args); did = True
        if args.converge:
            cmd_converge(conn, args); did = True
        if args.diffusion_queries:
            cmd_diffusion_queries(conn, args); did = True
        if args.rank:
            cmd_rank(conn, args); did = True
        if args.assess:
            cmd_assess(conn, args); did = True
        if args.report:
            cmd_report(conn, args); did = True
        if args.funds:
            cmd_funds(conn, args); did = True
        if args.watch:
            cmd_watch(conn); did = True
        if args.list or not did:
            cmd_list(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
