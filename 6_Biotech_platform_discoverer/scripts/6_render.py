#!/usr/bin/env python
"""Render the current screener store to a self-contained HTML report — WITHOUT re-running the run.

    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/6_render.py [--open-browser]

Reads ``data/store.db`` as-is and writes ``Outputs/screener_report.html``. This is decoupled from
the pipeline on purpose (repo reversibility principle): you can re-render the latest results any time
without re-harvesting or re-scoring. No network, no Claude, no writes to the store.
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from platform_discoverer import config as cfg
from platform_discoverer import render
from platform_discoverer.store import Store

DEFAULT_OUT = "Outputs/screener_report.html"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Render the screener store to HTML (read-only).")
    p.add_argument("--db", default="data/store.db")
    p.add_argument("--config", default="config/config.yaml")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--open-browser", action="store_true", default=True,
                   help="open the report in a browser (default on)")
    p.add_argument("--no-open", dest="open_browser", action="store_false",
                   help="write the report but do not open a browser")
    args = p.parse_args(argv)

    if not Path(args.db).exists():
        print(f"[6_render] no store at {args.db} — run the pipeline first "
              f"(scripts/6_screen.py).", file=sys.stderr)
        return 1

    config = cfg.load_config(args.config) if Path(args.config).exists() else None
    with Store.open(args.db, config=config) as store:
        out = render.write_report(store, args.out, config)
        n = store.count_companies()
    print(f"[6_render] wrote {out}  ({n} companies)")
    if args.open_browser:
        webbrowser.open(out.resolve().as_uri())
    return 0


# NOTE: --open-browser is the default; pass --no-open to suppress.


if __name__ == "__main__":
    sys.exit(main())
