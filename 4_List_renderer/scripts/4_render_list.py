"""4_render_list — generate the list_results_data.js sidecar, then optionally
open Outputs/list_results.html in the default browser.

The template is fixed; only the data source changes (adaptive display):

    --source file      read a JSON payload (default: data/sample_input.json)
    --source biopharm  top-N tickers by composite_score from
                       3_Biopharmcatalyst_parser/data/biotech.db
    --source news      N articles each from FierceBiotech / Le Figaro / CNBC

Run with PYTHONPATH=src (the .bat sets this):

    python scripts/4_render_list.py --source news --open-browser
    python scripts/4_render_list.py --source news --top 3
    python scripts/4_render_list.py --source biopharm --top 10
    python scripts/4_render_list.py --source file --input my.json

File-source JSON shape:
    { "query": "...", "brand": "...", "results": [ {..result..}, ... ] }
See data/sample_input.json for a worked example and the field list.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import webbrowser
from pathlib import Path

from list_renderer.render import build_payload, render_sidecar
from list_renderer.adapters import (
    build_payload_from_biotech_db,
    build_payload_from_news,
)

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
OUTPUTS = ROOT / "Outputs"
HTML = OUTPUTS / "list_results.html"
DEFAULT_INPUT = ROOT / "data" / "sample_input.json"
DEFAULT_BIOTECH_DB = (
    REPO_ROOT / "3_Biopharmcatalyst_parser" / "data" / "biotech.db"
)

log = logging.getLogger("4_render_list")


def load_input(path: Path) -> tuple[str | None, str | None, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("query"), data.get("brand"), data.get("results", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the list_results sidecar from a chosen data source."
    )
    parser.add_argument(
        "--source",
        choices=("file", "biopharm", "news"),
        default="file",
        help="Data source (default: file).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="[file] Input JSON (default: data/sample_input.json).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_BIOTECH_DB,
        help="[biopharm] Path to biotech.db.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help="Items to show. Default: [biopharm] 10 top tickers, [news] 3 per site.",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open list_results.html after rendering.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    if args.source == "biopharm":
        try:
            payload = build_payload_from_biotech_db(
                args.db, top_n=args.top or 10
            )
        except FileNotFoundError as exc:
            log.error("%s", exc)
            return 1
    elif args.source == "news":
        payload = build_payload_from_news(per_site=args.top or 3)
    else:
        if not args.input.exists():
            log.error("Input not found: %s", args.input)
            return 1
        query, brand, results = load_input(args.input)
        payload = build_payload(query, brand, results)

    target = render_sidecar(payload, OUTPUTS)
    log.info("Wrote %s (%d result(s)).", target, len(payload["results"]))

    if args.open_browser:
        if not HTML.exists():
            log.error("Template missing: %s", HTML)
            return 1
        webbrowser.open(HTML.as_uri())
        log.info("Opened %s", HTML)

    return 0


if __name__ == "__main__":
    sys.exit(main())
