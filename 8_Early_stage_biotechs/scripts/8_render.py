#!/usr/bin/env python
"""Render the scored candidates to a self-contained HTML digest — WITHOUT re-running the pipeline.

    PYTHONPATH=src ..\\.venv\\Scripts\\python.exe scripts\\8_render.py [--no-open]

Reads ``data/early_detection.db`` as-is and writes ``Outputs/digest.html`` + ``Outputs/digest_data.js``.
Decoupled from the pipeline on purpose (repo reversibility principle): re-render the latest results any
time without re-fetching, re-extracting, or re-scoring. No network, no Claude, no writes to the store.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from early_detection import render
from early_detection.config import COMPONENT_ROOT, load_config
from early_detection.store import Store

DEFAULT_OUT = COMPONENT_ROOT / "Outputs" / "digest.html"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Render the scored candidates to HTML (read-only).")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--open-browser", action="store_true", default=True,
                   help="open the digest in a browser (default on)")
    p.add_argument("--no-open", dest="open_browser", action="store_false",
                   help="write the digest but do not open a browser")
    args = p.parse_args(argv)

    cfg = load_config()
    if not Path(cfg.db_path).exists():
        print(f"[8_render] no store at {cfg.db_path} — run the pipeline first.", file=sys.stderr)
        return 1

    store = Store(cfg.db_path)
    out = render.write_report(store, cfg, args.out)
    n = store.count_scores(cfg.scoring_prompt_version)
    store.close()
    print(f"[8_render] wrote {out}  ({n} scored candidates)")
    if args.open_browser:
        webbrowser.open(Path(out).resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
