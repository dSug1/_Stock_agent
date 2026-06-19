"""SWR content cache (Phase 2) over the `items` table + `source_state`.

Read-through with serve-stale-on-failure (D8 stale-while-revalidate, adapted to a
one-shot CLI render): a source is re-fetched only when its cache is older than the
TTL; on a fetch failure the last good cache is served. True background refresh
(serve stale instantly, refresh async) lands with the M7 server in Phase 4.

Items store the renderable Highlight as `payload_json` (metadata only, D17) plus
explicit columns for future ranking/dedup (M4/M6).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from .db import now_iso

DEFAULT_TTL_SECONDS = 900  # 15 min for network sources


def item_id(source_id: str, highlight: dict) -> str:
    """Stable global id: hash of (source_id, url|title)."""
    key = highlight.get("title_url") or highlight.get("read_more_url") or highlight.get("title") or ""
    return hashlib.sha1(f"{source_id}\x1f{key}".encode("utf-8")).hexdigest()[:16]


def is_fresh(conn: sqlite3.Connection, source_id: str, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0:
        return False
    row = conn.execute(
        "SELECT last_fetch_at, last_status FROM source_state WHERE source_id=?",
        (source_id,),
    ).fetchone()
    if not row or not row["last_fetch_at"] or row["last_status"] != "ok":
        return False
    try:
        last = datetime.fromisoformat(row["last_fetch_at"])
    except ValueError:
        return False
    age = (datetime.now(timezone.utc) - last).total_seconds()
    return age < ttl_seconds


def get_cached_items(conn: sqlite3.Connection, source_id: str) -> list[dict]:
    """Return cached Highlights for a source (newest first by capture order)."""
    rows = conn.execute(
        "SELECT payload_json FROM items WHERE source_id=? AND payload_json IS NOT NULL "
        "ORDER BY captured_at DESC, rowid ASC",
        (source_id,),
    ).fetchall()
    return [json.loads(r["payload_json"]) for r in rows]


def store_items(conn: sqlite3.Connection, source_id: str, highlights: list[dict]) -> None:
    """Replace the source's cached items with `highlights` (upsert by id)."""
    ts = now_iso()
    # Drop stale rows for this source, then re-insert the fresh set. Simple and
    # correct for feed-style sources where the latest fetch is authoritative.
    conn.execute("DELETE FROM items WHERE source_id=?", (source_id,))
    for h in highlights:
        iid = h.setdefault("id", item_id(source_id, h))
        h.setdefault("source_id", source_id)
        conn.execute(
            """
            INSERT OR REPLACE INTO items
                (id, source_id, item_key, title, url, snippet, published_at_utc,
                 language, captured_at, topics_json, raw_hash, payload_json)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
            """,
            (
                iid, source_id, h.get("title"),
                h.get("title_url") or h.get("read_more_url"),
                h.get("snippet"), h.get("published_at"), h.get("language"),
                ts, json.dumps(h, ensure_ascii=False),
            ),
        )
    conn.commit()


def mark_fetched(conn: sqlite3.Connection, source_id: str, status: str, n_items: int) -> None:
    conn.execute(
        """
        INSERT INTO source_state (source_id, last_fetch_at, last_status, n_items)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            last_fetch_at=excluded.last_fetch_at,
            last_status=excluded.last_status,
            n_items=excluded.n_items
        """,
        (source_id, now_iso(), status, n_items),
    )
    conn.commit()
