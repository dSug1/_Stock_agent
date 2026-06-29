#!/usr/bin/env python
"""Run-summary / observability CLI (spec §15) — a per-run Markdown digest of the funnel.

Read-only and free: reconstructs the run from the audit log + run_meta + scores and writes
``Outputs/run_<id>_summary.md`` (+ a stable ``Outputs/run_summary.md``). No network, no Claude.

    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_summary.py
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from platform_discoverer import config as cfg
from platform_discoverer import observability
from platform_discoverer.store import Store, now_iso


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run summary (funnel + shortlist + movers + seeds + cost).")
    p.add_argument("--db", default="data/store.db")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--out", default="Outputs")
    p.add_argument("--open-browser", action="store_true", help="open the summary after writing")
    args = p.parse_args(argv)

    if not Path(args.db).exists():
        print(f"[6_summary] no store at {args.db} — run the pipeline first.", file=sys.stderr)
        return 1

    config = cfg.load_config(args.config)
    with Store.open(args.db, config=config) as store:
        out = observability.write_run_summary(store, args.out, config, run_id=now_iso())
    print(f"[6_summary] wrote {out}")
    if args.open_browser:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
