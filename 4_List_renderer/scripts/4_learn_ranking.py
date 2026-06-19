"""4_learn_ranking — re-fit the interest model from the interaction log (M6).

A free, offline batch step (no network, no Claude): reads the append-only
`interactions` table, attributes each signal to the item's source/topics via the
`context_json` snapshot (D19), and writes per-source / per-topic affinities to
`ranking_state`. The next render/board picks them up automatically (D5).

Run on a schedule or after a session of reading. Run with PYTHONPATH=src:

    python scripts/4_learn_ranking.py            # fit the local user, print a report
    python scripts/4_learn_ranking.py --dry-run  # show what would change, write nothing
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from list_renderer import db as listdb
from list_renderer import ranking

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIST_DB = ROOT / "data" / "list_renderer.db"

log = logging.getLogger("4_learn_ranking")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-fit ranking weights from interactions (M6).")
    parser.add_argument("--list-db", type=Path, default=DEFAULT_LIST_DB,
                        help="Registry DB (default: data/list_renderer.db).")
    parser.add_argument("--user", default=listdb.LOCAL_USER_ID,
                        help="User id to fit (default: local).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report only; do not write ranking_state.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s")

    conn = listdb.connect(args.list_db)
    try:
        if args.dry_run:
            # Fit in a transaction we roll back, so nothing is persisted.
            report = ranking.fit_affinities(conn, args.user)
            conn.rollback()
        else:
            report = ranking.fit_affinities(conn, args.user)
    finally:
        conn.close()

    if not report.get("fitted"):
        log.info("Not fitted: %s (%d/%d interactions). Cold-start ranking stands.",
                 report.get("reason"), report.get("n_interactions", 0), report.get("min", 0))
        return 0

    log.info("Fitted from %d interaction(s): %d source(s), %d topic(s)%s.",
             report["n_interactions"], report["n_sources"], report["n_topics"],
             " [dry-run, not written]" if args.dry_run else "")
    for sid, aff in sorted(report["source_affinity"].items(), key=lambda kv: -kv[1]):
        log.info("  source %-20s affinity %+.3f", sid, aff)
    for t, aff in sorted(report["topic_affinity"].items(), key=lambda kv: -kv[1]):
        log.info("  topic  %-20s affinity %+.3f", t, aff)
    return 0


if __name__ == "__main__":
    sys.exit(main())
