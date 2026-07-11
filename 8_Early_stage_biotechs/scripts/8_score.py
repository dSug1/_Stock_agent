"""Module 8 — stack-convergence scoring CLI (spec §5.4). The capstone Claude call + the §7 digest.

Rules-based pre-filter (§5.5) → only genuinely-cornered candidates reach the expensive full-model call.
Mandatory [y/N] cost gate + max_usd_per_run guard. Batch default, per-candidate persist, batch_id →
`data/score_batch_id.txt` for `--resume`. `--digest` (re)writes the ranked Markdown digest.

Run (from the component dir; needs ANTHROPIC_API_KEY in repo-root .env):
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_score.py --realtime          # score cleared candidates
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_score.py --digest            # (re)write digest only
    PYTHONPATH=src ..\.venv\Scripts\python.exe scripts\8_score.py --resume
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
from early_detection.config import COMPONENT_ROOT, DATA_DIR, load_config
from early_detection.scoring import estimate_usd, score_candidates, write_digest, write_watchlist
from early_detection.store import Store

_BATCH_ID_FILE = DATA_DIR / "score_batch_id.txt"
_DIGEST = COMPONENT_ROOT / "Outputs" / "digest.md"
_WATCHLIST = COMPONENT_ROOT / "Outputs" / "watchlist.csv"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Module 8 stack-convergence scoring + digest (gated spend).")
    ap.add_argument("--limit", type=int, default=None, help="cap the number of candidates this run")
    ap.add_argument("--realtime", action="store_true",
                    help="realtime fan-out instead of Batch API. NOT recommended — Batch is the project "
                         "default (50%% cheaper); realtime only for a tiny in-session run.")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="re-score even entities already scored at this prompt")
    ap.add_argument("--resume", action="store_true", help="re-attach the last submitted batch and collect")
    ap.add_argument("--digest", action="store_true",
                    help="(re)write the ranked digest + watchlist export and exit (no spend)")
    ap.add_argument("--stats", action="store_true", help="print scoring stats and exit")
    ap.add_argument("--yes", action="store_true", help="skip the [y/N] gate")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    store = Store(cfg.db_path)

    if args.stats:
        print(f"  scored (prompt {cfg.scoring_prompt_version}): {store.count_scores(cfg.scoring_prompt_version)}")
        store.close(); return 0

    if args.digest:
        n = write_digest(store, cfg, _DIGEST)
        w = write_watchlist(store, cfg, _WATCHLIST)
        print(f"Wrote digest ({n}) → {_DIGEST}")
        print(f"Wrote watchlist ({w} deep-dive/surveil) → {_WATCHLIST}")
        store.close(); return 0

    resume_id = None
    if args.resume:
        if not _BATCH_ID_FILE.is_file():
            print("No saved batch id to resume."); store.close(); return 1
        resume_id = _BATCH_ID_FILE.read_text(encoding="utf-8").strip()
        print(f"Resuming batch {resume_id}")

    n = len(store.scoring_candidates(cfg.scoring_prompt_version,
                                     min_independent=cfg.prefilter_min_independent,
                                     limit=args.limit, force=args.force,
                                     clinical_min_phase=cfg.prefilter_clinical_min_phase))
    if not resume_id:
        print(f"Stack-convergence scoring → {cfg.db_path}")
        print(f"  model={cfg.scoring_model}  prompt={cfg.scoring_prompt_version}  "
              f"mode={'realtime' if args.realtime else 'batch'}")
        print(f"  candidates cleared pre-filter (>= {cfg.prefilter_min_independent} independent citations "
              f"+ capital signal): {n}")
        print(f"  estimated cost: ${estimate_usd(cfg, n, use_batch=not args.realtime):,.2f}  "
              f"(token-only, no web_search; cap ${cfg.max_usd_per_run:,.2f}/run)")
        if n == 0:
            print("  nothing to score. (Run the founder + literature signals first.)")
            store.close(); return 0
        if not args.yes and input("  Dispatch to Claude? [y/N] ").strip().lower() != "y":
            print("  aborted."); store.close(); return 0

    client = AnthropicClient(max_usd=cfg.max_usd_per_run)

    def _save(bid: str) -> None:
        _BATCH_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BATCH_ID_FILE.write_text(bid, encoding="utf-8")
        print(f"  batch submitted: {bid} (saved for --resume)")

    res = score_candidates(store, cfg, client=client, limit=args.limit, use_batch=not args.realtime,
                           concurrency=args.concurrency, force=args.force, resume_batch_id=resume_id,
                           on_batch_id=_save)
    print(f"\n  candidates: {res.candidates}")
    print(f"  scored:     {res.scored}")
    print(f"  flags:      {res.flags}")
    print(f"  spent (actual):     ${res.spent_usd:,.2f}")

    nrows = write_digest(store, cfg, _DIGEST)
    wrows = write_watchlist(store, cfg, _WATCHLIST)
    print(f"\n  digest ({nrows}) → {_DIGEST}")
    print(f"  watchlist ({wrows} deep-dive/surveil) → {_WATCHLIST}  (§2.4 two-way export)")
    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
