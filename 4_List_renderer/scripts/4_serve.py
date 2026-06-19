"""4_serve — start the Curator local board server (M7, Phase 4).

Serves the stable template + the /api/* contract on 127.0.0.1 and opens the
browser. Unlike `4_render_list.py` (one-shot sidecar write), this stays running
and serves a live board: the page fetches GET /api/board, and like/hide/dwell/
click signals POST back to /api/interact and persist to data/list_renderer.db.

Run with PYTHONPATH=src (run_4_List_server.bat sets this):

    python scripts/4_serve.py                 # serve on 127.0.0.1:8765, open browser
    python scripts/4_serve.py --port 9000
    python scripts/4_serve.py --seed          # (re)seed sources.yaml first
    python scripts/4_serve.py --no-open       # don't auto-open the browser
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from list_renderer import db as listdb
from list_renderer.server import serve

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIST_DB = ROOT / "data" / "list_renderer.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the Curator board (M7).")
    parser.add_argument("--list-db", type=Path, default=DEFAULT_LIST_DB,
                        help="Registry DB (default: data/list_renderer.db).")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind host (default: 127.0.0.1 — localhost only).")
    parser.add_argument("--port", type=int, default=8765, help="Bind port.")
    parser.add_argument("--seed", action="store_true",
                        help="(Re)seed sources from config/sources.yaml before serving.")
    parser.add_argument("--no-open", action="store_true",
                        help="Do not auto-open the browser.")
    parser.add_argument("--refresh-interval", type=int, default=0, metavar="SECONDS",
                        help="Background SWR refresh cadence (0 = off; e.g. 900 = every 15 min).")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    serve(
        args.list_db,
        host=args.host,
        port=args.port,
        user_id=listdb.LOCAL_USER_ID,
        board_id=listdb.DEFAULT_BOARD_ID,
        seed=args.seed,
        open_browser=not args.no_open,
        refresh_interval=args.refresh_interval,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
