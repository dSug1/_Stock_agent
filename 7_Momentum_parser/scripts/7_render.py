#!/usr/bin/env python
"""Render the latest momentum run to a self-contained HTML report.

  PYTHONPATH=src python scripts/7_render.py [--run-id RUN] [--open-browser]
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from momentum_parser.config import db_path, load_config
from momentum_parser.render import render
from momentum_parser.store import Store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", help="run to render (default: latest)")
    ap.add_argument("--open-browser", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config()
    store = Store(db_path(cfg))
    path = render(store, cfg, args.run_id)
    store.close()
    print(f"[7] report -> {path}")
    if args.open_browser:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
