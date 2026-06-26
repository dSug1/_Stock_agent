"""OD-2 forward archive — timestamped raw snapshots of forward_only sources.

39 of the 70 registry sources have no historical feed (award category taxonomies, FDA
BTD/Fast-Track real-time, WG-charter/conference-agenda diffs, job-posting velocity, VC RSS).
Their panel-era history is **unrecoverable** unless captured going forward (decisions OD-2).
This module snapshots them weekly: fetch the registered URL, store the raw body **only when
its content hash changes** (bounds growth — taxonomies move ~annually), and always write a
light cadence row. Fail-open per source; inject ``http_get`` for testing.

Scope note (v0.1): the archive snapshots the source's *registered URL* as-is. Per-source
fetch specs (the exact taxonomy/list page, pagination) are refined later, alongside
``scrapeability_verified``. Sources with no URL are recorded as ``skipped_no_url`` so the gap
is visible.
"""

import hashlib
import logging
import sqlite3
import urllib.request

from .db import now_iso

log = logging.getLogger(__name__)

USER_AGENT = "HypeParser/0.1 (research; local)"


def _default_http_get(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        body = resp.read()
    return status, body


def _latest_hash(conn: sqlite3.Connection, source_id: str):
    row = conn.execute(
        "SELECT content_hash FROM source_snapshots "
        "WHERE source_id = ? AND content_hash IS NOT NULL "
        "ORDER BY snapshot_id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    return row["content_hash"] if row else None


def snapshot_source(conn: sqlite3.Connection, source: dict, *, http_get=None) -> dict:
    """Snapshot one source. Stores raw content only when its hash changed from the last
    stored snapshot; otherwise writes a metadata-only cadence row. Never raises."""
    http_get = http_get or _default_http_get
    sid = source["source_id"]
    url = source.get("url")
    fetched_at = now_iso()

    if not url:
        conn.execute(
            "INSERT INTO source_snapshots (source_id, fetched_at, url, error) "
            "VALUES (?, ?, ?, ?)",
            (sid, fetched_at, url, "no url registered"),
        )
        conn.commit()
        return {"source_id": sid, "status": "skipped_no_url", "changed": False}

    try:
        status, body = http_get(url)
    except Exception as exc:  # fail-open: log the failure, keep going
        conn.execute(
            "INSERT INTO source_snapshots (source_id, fetched_at, url, error) "
            "VALUES (?, ?, ?, ?)",
            (sid, fetched_at, url, f"{type(exc).__name__}: {exc}"),
        )
        conn.commit()
        log.warning("snapshot %s failed: %s", sid, exc)
        return {"source_id": sid, "status": "error", "changed": False, "error": str(exc)}

    if isinstance(body, str):
        body = body.encode("utf-8", "replace")
    content_hash = hashlib.sha256(body).hexdigest()
    changed = content_hash != _latest_hash(conn, sid)
    content = body.decode("utf-8", "replace") if changed else None
    conn.execute(
        "INSERT INTO source_snapshots "
        "(source_id, fetched_at, url, http_status, content_hash, bytes, content, changed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, fetched_at, url, status, content_hash, len(body), content, int(changed)),
    )
    conn.commit()
    return {"source_id": sid, "status": "ok", "http_status": status,
            "changed": changed, "bytes": len(body)}


def forward_only_sources(conn, *, source_ids=None):
    rows = conn.execute(
        "SELECT * FROM sources WHERE enabled = 1 AND history_availability = 'forward_only' "
        "ORDER BY source_id"
    ).fetchall()
    if source_ids:
        wanted = set(source_ids)
        rows = [r for r in rows if r["source_id"] in wanted]
    return rows


def archive_forward_only(conn, *, http_get=None, limit=None, source_ids=None) -> dict:
    """Snapshot every enabled forward_only source. Fail-open; returns a summary."""
    rows = forward_only_sources(conn, source_ids=source_ids)
    if limit:
        rows = rows[:limit]
    summary = {"attempted": 0, "ok": 0, "changed": 0, "errors": 0,
               "skipped_no_url": 0, "results": []}
    for r in rows:
        res = snapshot_source(conn, dict(r), http_get=http_get)
        summary["attempted"] += 1
        if res["status"] == "ok":
            summary["ok"] += 1
            if res["changed"]:
                summary["changed"] += 1
        elif res["status"] == "error":
            summary["errors"] += 1
        else:
            summary["skipped_no_url"] += 1
        summary["results"].append(res)
    return summary


def most_recent_snapshot(conn):
    """ISO timestamp of the newest snapshot across all sources, or None."""
    row = conn.execute("SELECT MAX(fetched_at) AS m FROM source_snapshots").fetchone()
    return row["m"] if row and row["m"] else None


def snapshot_stats(conn):
    """Per-source archive coverage: snapshot count, content revisions, last fetch."""
    return conn.execute(
        "SELECT source_id, COUNT(*) AS n_snapshots, "
        "       SUM(changed) AS n_revisions, "
        "       SUM(error IS NOT NULL) AS n_errors, "
        "       MAX(fetched_at) AS last_fetch "
        "FROM source_snapshots GROUP BY source_id ORDER BY source_id"
    ).fetchall()
