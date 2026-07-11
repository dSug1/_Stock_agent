"""Module 8 — founder-lineage extraction CLI (spec §5.1). THE FIRST CLAUDE SPEND in this module.

Cheap Haiku tier + basic web_search + Batch API (50%). Mandatory [y/N] cost gate before dispatch and a
hard `max_usd_per_run` guard. Persists per entity (crash-safe); Batch mode writes the batch_id to
`data/extract_batch_id.txt` BEFORE polling so `--resume` can re-attach after a crash.

Run (from the component dir; needs ANTHROPIC_API_KEY in repo-root .env):
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_extract.py --limit 3 --realtime   # tiny smoke
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_extract.py --limit 200            # batch sweep
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_extract.py --resume               # re-attach batch
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_extract.py --stats
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

from early_detection.clients.anthropic_client import AnthropicClient
from early_detection.config import DATA_DIR, load_config
from early_detection.extraction import estimate_usd, extract_founders
from early_detection.store import Store

_BATCH_ID_FILE = DATA_DIR / "extract_batch_id.txt"


def _print_stats(store: Store) -> None:
    extracted = store.conn.execute(
        "SELECT COUNT(*) FROM entity WHERE founder_prompt_version IS NOT NULL").fetchone()[0]
    with_f = store.conn.execute(
        "SELECT COUNT(DISTINCT entity_id) FROM founder").fetchone()[0]
    print(f"\n  entities extracted:     {extracted}")
    print(f"  entities with founders: {with_f}")
    print(f"  total founders:         {store.count_founders()}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Module 8 founder-lineage extraction (Claude, gated spend).")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of entities this run")
    ap.add_argument("--realtime", action="store_true", help="use realtime fan-out instead of Batch API")
    ap.add_argument("--concurrency", type=int, default=6, help="realtime workers (default 6)")
    ap.add_argument("--cold-first", action="store_true",
                    help="prioritize cold-discovery (non-M6) names — the under-recognized end")
    ap.add_argument("--resume", action="store_true", help="re-attach the last submitted batch and collect")
    ap.add_argument("--stats", action="store_true", help="print extraction stats and exit")
    ap.add_argument("--yes", action="store_true", help="skip the [y/N] gate (for automation)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    cfg = load_config()
    store = Store(cfg.db_path)

    if args.stats:
        _print_stats(store)
        store.close()
        return 0

    resume_id = None
    if args.resume:
        if not _BATCH_ID_FILE.is_file():
            print("No saved batch id to resume."); store.close(); return 1
        resume_id = _BATCH_ID_FILE.read_text(encoding="utf-8").strip()
        print(f"Resuming batch {resume_id}")

    n = len(store.entities_for_extraction(cfg.extraction_prompt_version, limit=args.limit,
                                          cold_first=args.cold_first))
    if not resume_id:
        est = estimate_usd(cfg, n)
        print(f"Founder extraction → {cfg.db_path}")
        print(f"  model={cfg.extraction_model}  prompt={cfg.extraction_prompt_version}  "
              f"mode={'realtime' if args.realtime else 'batch'}")
        print(f"  entities to extract: {n}")
        print(f"  estimated cost:      ${est:,.2f}  (cap ${cfg.max_usd_per_run:,.2f}/run)")
        if n == 0:
            print("  nothing to do."); store.close(); return 0
        if not args.yes:
            resp = input("  Dispatch to Claude? [y/N] ").strip().lower()
            if resp != "y":
                print("  aborted."); store.close(); return 0

    client = AnthropicClient(max_usd=cfg.max_usd_per_run)

    def _save_batch_id(bid: str) -> None:
        _BATCH_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BATCH_ID_FILE.write_text(bid, encoding="utf-8")   # persist BEFORE the long poll (crash-resume)
        print(f"  batch submitted: {bid} (saved for --resume)")

    res = extract_founders(store, cfg, client=client, limit=args.limit,
                           use_batch=not args.realtime, concurrency=args.concurrency,
                           cold_first=args.cold_first, resume_batch_id=resume_id,
                           on_batch_id=_save_batch_id)

    print(f"\n  attempted:      {res.attempted}")
    print(f"  extracted:      {res.extracted}")
    print(f"  with founders:  {res.with_founders}")
    print(f"  total founders: {res.total_founders}")
    print(f"  spent (calibrated): ${res.spent_usd:,.2f}  ({client.web_searches} web searches)")
    _print_stats(store)
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
