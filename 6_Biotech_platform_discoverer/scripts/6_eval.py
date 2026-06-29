#!/usr/bin/env python
"""Seed-eval validation harness CLI (spec §13) — the trust gate for the screen.

Scores the labeled seed set (``config/seed_labels.csv``) through the funnel and reports precision /
recall + per-stage survival to ``Outputs/seed_eval.md``. Read-only and free (no API): it evaluates
whatever scores already exist in the store.

    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_eval.py
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_eval.py --threshold 0.65 --persist-labels
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from platform_discoverer import config as cfg
from platform_discoverer import seed_eval
from platform_discoverer.store import Store, now_iso


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Seed validation harness (precision/recall + survival).")
    p.add_argument("--db", default="data/store.db")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--out", default="Outputs/seed_eval.md")
    p.add_argument("--threshold", type=float, default=None,
                   help="composite >= this ⇒ predicted positive (default from config seed_eval)")
    p.add_argument("--persist-labels", action="store_true",
                   help="also write positive/negative labels into the store seed_labels table")
    p.add_argument("--open-browser", action="store_true", help="open the report after writing")
    args = p.parse_args(argv)

    if not Path(args.db).exists():
        print(f"[6_eval] no store at {args.db} — run the pipeline first.", file=sys.stderr)
        return 1

    config = cfg.load_config(args.config)
    with Store.open(args.db, config=config) as store:
        summary = seed_eval.run(store, config, run_id=now_iso(), out_path=args.out,
                                threshold=args.threshold, persist_labels=args.persist_labels)
    print(f"[6_eval] precision={summary['precision']} recall={summary['recall']} "
          f"f1={summary['f1']} (TP={summary['tp']} FP={summary['fp']} FN={summary['fn']})")
    if summary["spec_failure"]:
        print(f"[6_eval] !! SPEC FAILURE: positives deleted at hard cut: "
              f"{summary['stage0_deleted_positives']}")
    print(f"[6_eval] report: {summary['report_path']}")
    if args.open_browser:
        webbrowser.open(Path(summary["report_path"]).resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
