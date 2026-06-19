"""Generic `sqlite` adapter (Phase 2) — read ANY SQLite DB by config.

Runs a user-supplied query and maps result columns onto Highlights. Generalizes
the bespoke `biopharm` adapter to any local/remote SQLite source.

Config (source.config_json):
    db        : str (required)  path to the .db (relative paths resolved vs project root)
    query     : str (required)  SELECT ... (use LIMIT in the query if desired)
    columns   : dict (required) maps Highlight field -> result column name:
                { title, url?, snippet?, site_name?, breadcrumb? }
    brand     : str = "SQLite"
    max_items : int = 50  (applied after the query as a safety cap)

Read-only open; the query is trusted config (single-user, local).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger("4_render_list.sqlite")


def _col(row: sqlite3.Row, columns: dict, field: str):
    name = columns.get(field)
    if not name:
        return None
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def fetch_results(
    db: str | Path,
    query: str,
    columns: dict,
    brand: str = "SQLite",
    max_items: int = 50,
) -> list[dict]:
    db_path = Path(db)
    if not query or not columns or not columns.get("title"):
        raise ValueError("sqlite adapter requires config.db, query and columns.title")
    if not db_path.exists():
        raise FileNotFoundError(f"sqlite source not found: {db_path}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query).fetchmany(max_items)
    finally:
        conn.close()

    results: list[dict] = []
    for r in rows:
        title = _col(r, columns, "title")
        if title is None:
            continue
        link = _col(r, columns, "url") or ""
        snippet = _col(r, columns, "snippet") or ""
        site_name = _col(r, columns, "site_name") or brand
        domain = urlsplit(str(link)).netloc
        results.append({
            "site_name": str(site_name),
            "url_breadcrumb": _col(r, columns, "breadcrumb") or domain or brand,
            "favicon": (f"https://www.google.com/s2/favicons?sz=64&domain={domain}"
                        if domain else None),
            "title": str(title),
            "title_url": str(link) or None,
            "verified": True,
            "snippet": str(snippet),
            "bold_terms": [],
            "read_more_url": str(link) or None,
        })
    log.info("  sqlite %s: %d row(s)", db_path.name, len(results))
    return results
