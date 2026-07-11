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
from early_detection.signals.clinical import ingest_clinical
from early_detection.signals.designations import ingest_designations
from early_detection.signals.independence import refine_independence
from early_detection.signals.literature import ingest_literature
from early_detection.signals.ownership import ingest_ownership
from early_detection.store import Store


def _print_stats(store: Store) -> None:
    total = store.count_signals()
    cap = store.count_signals("capital_markets")
    own = store.count_signals("ownership_crossing")
    lit = store.count_signals("literature")
    clin = store.count_signals("clinical_trial")
    des = store.count_signals("regulatory_designation")
    with_sig = store.conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM signal WHERE entity_id IS NOT NULL").fetchone()[0]
    by_form = store.conn.execute(
        "SELECT json_extract(raw_payload_json,'$.form') f, COUNT(*) FROM signal "
        "WHERE signal_type='capital_markets' GROUP BY f ORDER BY 2 DESC").fetchall()
    print(f"\n  signals total:          {total}")
    print(f"  capital_markets:        {cap}")
    print(f"  ownership_crossing:     {own}")
    print(f"  literature:             {lit}")
    print(f"  clinical_trial:         {clin}")
    print(f"  regulatory_designation: {des}")
    print(f"  entities with a signal: {with_sig}")
    if by_form:
        print("  by form: " + ", ".join(f"{f}={n}" for f, n in by_form))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Module 8 Phase-2 signal ingestion.")
    ap.add_argument("--capital-markets", action="store_true", help="ingest EDGAR capital-markets signals")
    ap.add_argument("--ownership", action="store_true",
                    help="ingest specialist-fund 5%%+ ownership crossings (EDGAR full-text)")
    ap.add_argument("--literature", action="store_true",
                    help="ingest founder publications + independent-citation signals (OpenAlex)")
    ap.add_argument("--independence", action="store_true",
                    help="§5.2 refinement: reclassify citations via co-authorship graph (self/collaborator/"
                         "same-institution/industry/independent) + independence_score (OpenAlex)")
    ap.add_argument("--clinical", action="store_true",
                    help="§3.2 clinical-stage signal: company trials via ClinicalTrials.gov v2 (free, no key)")
    ap.add_argument("--designations", action="store_true",
                    help="§3.4 FDA/regulatory designations (Breakthrough/Fast-Track/Orphan/RMAT) via EDGAR full-text")
    ap.add_argument("--tickers", default=None,
                    help="comma-separated tickers to pin to the front of the work-list (with --clinical)")
    ap.add_argument("--stats", action="store_true", help="print signal stats and exit")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of entities/founders this run")
    ap.add_argument("--concurrency", type=int, default=6, help="EDGAR fetch workers (default 6)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    store = Store(cfg.db_path)

    if args.stats and not (args.capital_markets or args.ownership or args.literature
                           or args.independence or args.clinical or args.designations):
        _print_stats(store)
        store.close()
        return 0

    if args.clinical:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
        print(f"Ingesting clinical-trial signals → {cfg.db_path}  (ClinicalTrials.gov v2, free, no key)")
        r = ingest_clinical(store, cfg, limit=args.limit, tickers=tickers)
        print(f"\n  entities scanned:   {r.entities}")
        print(f"  with ≥1 own trial:  {r.with_trials}")
        print(f"  signals written:    {r.signals}  (as lead sponsor: {r.lead})")
        print(f"  by health:          {r.by_health}  (active/completed = positive; stalled/unknown = not)")
        print(f"  by phase_rank:      {dict(sorted(r.by_phase.items()))}")
        _print_stats(store)
        store.close()
        return 0

    if args.designations:
        print(f"Ingesting regulatory-designation signals → {cfg.db_path}  "
              f"({len(cfg.designation_phrases)} phrases, forms={cfg.designation_forms}, EDGAR full-text)")
        r = ingest_designations(store, cfg)
        print(f"\n  phrases queried:    {r.phrases}")
        print(f"  filings seen:       {r.filings_seen}")
        print(f"  matched to universe:{r.matched}")
        print(f"  signals written:    {r.signals}")
        print(f"  by type:            {r.by_type}")
        _print_stats(store)
        store.close()
        return 0

    if args.independence:
        print(f"§5.2 independence refinement (co-authorship graph) → {cfg.db_path}")
        r = refine_independence(store, cfg, limit=args.limit)
        print(f"\n  founders refined:   {r.refined}")
        print(f"  citations classified: {r.citations}")
        print(f"  by relationship:    {r.by_relationship}")
        _print_stats(store)
        store.close()
        return 0

    if args.literature:
        print(f"Ingesting literature/citation signals → {cfg.db_path}  (OpenAlex, founder-keyed)")
        r = ingest_literature(store, cfg, limit=args.limit)
        print(f"\n  founders scanned:      {r.founders}")
        print(f"  authors resolved:      {r.authors_resolved}")
        print(f"  publications:          {r.publications}")
        print(f"  citations:             {r.citations}")
        print(f"  independent citations: {r.independent_citations}")
        print(f"  by independence:       {r.by_independence}")
        _print_stats(store)
        store.close()
        return 0

    if args.ownership:
        print(f"Ingesting ownership-crossing signals → {cfg.db_path}  "
              f"({len(cfg.specialist_funds)} funds, lookback {cfg.signal_lookback_days}d, EDGAR full-text)")
        r = ingest_ownership(store, cfg)
        print(f"\n  funds queried:      {r.funds}")
        print(f"  filings seen:       {r.filings_seen}")
        print(f"  matched to universe:{r.matched}")
        print(f"  signals written:    {r.signals}")
        if r.by_fund:
            print("  by fund: " + ", ".join(f"{k}={v}" for k, v in sorted(r.by_fund.items(), key=lambda x: -x[1])))
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
