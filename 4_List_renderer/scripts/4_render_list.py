"""4_render_list — generate the list_results_data.js sidecar, then optionally
open Outputs/list_results.html in the default browser.

Run with PYTHONPATH=src (the .bat sets this):

    python scripts/4_render_list.py                      # uses data/sample_input.json
    python scripts/4_render_list.py --input my.json
    python scripts/4_render_list.py --open-browser
    python scripts/4_render_list.py --input my.json --open-browser -v

Input JSON shape:
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

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = ROOT / "Outputs"
HTML = OUTPUTS / "list_results.html"
DEFAULT_INPUT = ROOT / "data" / "sample_input.json"

log = logging.getLogger("4_render_list")


def load_input(path: Path) -> tuple[str | None, str | None, list[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("query"), data.get("brand"), data.get("results", [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the list_results sidecar from a JSON payload."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Input JSON (default: data/sample_input.json).",
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
