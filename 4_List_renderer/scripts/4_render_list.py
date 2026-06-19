"""4_render_list — generate the list_results_data.js sidecar, then optionally
open Outputs/list_results.html in the default browser.

The template is fixed; only the data source changes (adaptive display):

    --source registry  the enabled source set on the board, from the registry DB
                       (data/list_renderer.db). THIS IS THE DEFAULT.
    --source file      read a JSON payload (default: data/sample_input.json)
    --source biopharm  top-N tickers by composite_score from
                       3_Biopharmcatalyst_parser/data/biotech.db
    --source news      N articles each from FierceBiotech / Le Figaro / CNBC

Run with PYTHONPATH=src (the .bat sets this):

    python scripts/4_render_list.py --open-browser            # registry (default)
    python scripts/4_render_list.py --seed                    # (re)seed sources.yaml first
    python scripts/4_render_list.py --source news --top 3     # single-source override
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

from list_renderer import db as listdb
from list_renderer.render import build_payload, render_sidecar
from list_renderer.pipeline import build_payload_from_registry
from list_renderer.sources import seed_from_config
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
DEFAULT_LIST_DB = ROOT / "data" / "list_renderer.db"
SOURCES_CONFIG = ROOT / "config" / "sources.yaml"

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
        choices=("registry", "file", "biopharm", "news"),
        default="registry",
        help="Data source (default: registry — the board's enabled source set).",
    )
    parser.add_argument(
        "--list-db",
        type=Path,
        default=DEFAULT_LIST_DB,
        help="[registry] Path to the registry DB (default: data/list_renderer.db).",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="[registry] (Re)seed sources from config/sources.yaml before rendering.",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="[registry] Force a re-fetch of network sources (bypass the SWR cache).",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="[registry] Render from cache only; no network (offline).",
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

    if args.source == "registry":
        conn = listdb.connect(args.list_db)
        try:
            n_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            if args.seed or n_sources == 0:
                n = seed_from_config(conn, SOURCES_CONFIG)
                log.info("Seeded %d source(s) from %s", n, SOURCES_CONFIG.name)
            payload = build_payload_from_registry(
                conn,
                user_id=listdb.LOCAL_USER_ID,
                board_id=listdb.DEFAULT_BOARD_ID,
                refresh=args.refresh,
                no_fetch=args.no_fetch,
            )
        finally:
            conn.close()
    elif args.source == "biopharm":
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
