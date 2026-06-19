"""4_add_interest — declare an area of interest; discover + register sources (M5).

Classifies the input (site URL / ticker / topic), then registers a concrete
source on the board (D7):
  - a **site** -> its declared RSS feed (free, no Claude); if none is declared,
    the interest is stored 'pending' and you resolve it with 4_resolve_source.py.
  - a **topic / ticker** -> a free Google News RSS search source.

Run with PYTHONPATH=src:

    python scripts/4_add_interest.py "semiconductors"        # topic
    python scripts/4_add_interest.py "NVDA"                  # ticker
    python scripts/4_add_interest.py "https://www.theverge.com"   # site
    python scripts/4_add_interest.py --kind topic "AI safety" # force the kind
    python scripts/4_add_interest.py --list                   # show declared interests
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from list_renderer import db as listdb
from list_renderer import interests as interests_mod

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIST_DB = ROOT / "data" / "list_renderer.db"

log = logging.getLogger("4_add_interest")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add an area of interest (M5).")
    parser.add_argument("value", nargs="?", help="The interest: a site URL, ticker, or topic.")
    parser.add_argument("--kind", choices=("site", "ticker", "topic", "query"),
                        help="Force the interest kind (else auto-classified).")
    parser.add_argument("--list", action="store_true", help="List declared interests and exit.")
    parser.add_argument("--list-db", type=Path, default=DEFAULT_LIST_DB)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s")

    conn = listdb.connect(args.list_db)
    try:
        if args.list:
            rows = interests_mod.list_interests(conn, user_id=listdb.LOCAL_USER_ID)
            if not rows:
                log.info("No interests declared yet.")
            for r in rows:
                log.info("  [%s] %-7s %-30s (%s)", r["id"], r["kind"], r["value"], r["status"])
            return 0
        if not args.value:
            parser.error("provide an interest value, or use --list")
        report = interests_mod.add_interest(
            conn, args.value, kind=args.kind,
            user_id=listdb.LOCAL_USER_ID, board_id=listdb.DEFAULT_BOARD_ID,
        )
    finally:
        conn.close()

    log.info("%s [%s]: %s", report.get("kind", "?"), report.get("status", "?"), report.get("note", ""))
    if report.get("source_id"):
        log.info("  source: %s  ->  %s", report["source_id"], report.get("feed_url", ""))
        log.info("  Re-render/serve the board to see its items.")
    return 0 if report.get("status") != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
