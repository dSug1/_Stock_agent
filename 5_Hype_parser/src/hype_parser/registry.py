"""Source registry CRUD + freeze (M1).

The registry is the moat *and* the bias (spec 6.7): freeze and version it before any
backtest window, timestamp every add_date, and treat scrapeability as claimed-not-verified
until checked. ``freeze`` snapshots the current registry into an immutable
``registry_versions`` row so a run can pin to a point-in-time registry (Protocol 1).
"""

import hashlib
import json
import logging
import sqlite3

from .db import now_iso, today_iso

log = logging.getLogger(__name__)

# Columns a config row may set; the rest (timestamps) are managed here.
_CONFIG_FIELDS = (
    "name", "edge_type", "tier", "access_method", "diffusion_position",
    "signal_type", "history_availability", "jury_credibility", "cadence",
    "rate_limits", "url", "scrapeability_verified", "enabled", "notes", "add_date",
)

_DIFFUSION_POSITIONS = {"leading", "bridge", "denominator"}
_SIGNAL_TYPES = {"threshold_event", "volume"}
_HISTORY = {"queryable", "forward_only"}


def load_config(path: str) -> dict:
    import yaml  # local import so the package imports without PyYAML present
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _validate(s: dict) -> None:
    sid = s.get("source_id")
    if not sid:
        raise ValueError("source row missing 'source_id'")
    if not s.get("edge_type"):
        raise ValueError(f"{sid}: missing required 'edge_type'")
    for field, allowed in (
        ("diffusion_position", _DIFFUSION_POSITIONS),
        ("signal_type", _SIGNAL_TYPES),
        ("history_availability", _HISTORY),
    ):
        val = s.get(field)
        if val is not None and val not in allowed:
            raise ValueError(f"{sid}: {field}={val!r} not in {sorted(allowed)}")


def _row_values(s: dict) -> dict:
    now = now_iso()
    return {
        "source_id": s["source_id"],
        "name": s.get("name") or s["source_id"],
        "edge_type": s["edge_type"],
        "tier": s.get("tier"),
        "access_method": s.get("access_method"),
        "diffusion_position": s.get("diffusion_position"),
        "signal_type": s.get("signal_type"),
        "history_availability": s.get("history_availability"),
        "jury_credibility": s.get("jury_credibility"),
        "cadence": s.get("cadence"),
        "rate_limits": s.get("rate_limits"),
        "url": s.get("url"),
        "scrapeability_verified": int(s.get("scrapeability_verified", 0)),
        "enabled": int(s.get("enabled", 1)),
        "notes": s.get("notes"),
        "add_date": s.get("add_date") or today_iso(),
        "created_at": now,
        "updated_at": now,
    }


def _insert(conn: sqlite3.Connection, s: dict) -> None:
    vals = _row_values(s)
    cols = ", ".join(vals)
    ph = ", ".join("?" for _ in vals)
    conn.execute(f"INSERT INTO sources ({cols}) VALUES ({ph})", tuple(vals.values()))


def _update(conn: sqlite3.Connection, s: dict) -> None:
    sets = {f: s[f] for f in _CONFIG_FIELDS if f in s}
    if "scrapeability_verified" in sets:
        sets["scrapeability_verified"] = int(sets["scrapeability_verified"])
    if "enabled" in sets:
        sets["enabled"] = int(sets["enabled"])
    sets["updated_at"] = now_iso()
    assignment = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(
        f"UPDATE sources SET {assignment} WHERE source_id = ?",
        (*sets.values(), s["source_id"]),
    )


def add_source(conn: sqlite3.Connection, source: dict, *, update: bool = False) -> str:
    """Insert one source (or update if it exists and update=True). Returns the status."""
    _validate(source)
    sid = source["source_id"]
    exists = conn.execute(
        "SELECT 1 FROM sources WHERE source_id = ?", (sid,)
    ).fetchone()
    if exists:
        if not update:
            return "skipped"
        _update(conn, source)
        conn.commit()
        return "updated"
    _insert(conn, source)
    conn.commit()
    return "inserted"


def seed_from_config(conn: sqlite3.Connection, config, *, update: bool = False) -> dict:
    """Seed sources from a parsed config (dict with 'sources' or a bare list).

    Idempotent on ``source_id``: existing rows are skipped unless ``update=True``.
    """
    sources = config["sources"] if isinstance(config, dict) else config
    counts = {"inserted": 0, "updated": 0, "skipped": 0}
    for s in sources:
        _validate(s)
    for s in sources:
        status = add_source(conn, s, update=update)
        counts[status] += 1
    conn.commit()
    return counts


def list_sources(conn, *, edge_type=None, enabled_only=True):
    sql = "SELECT * FROM sources"
    where, params = [], []
    if enabled_only:
        where.append("enabled = 1")
    if edge_type:
        where.append("edge_type = ?")
        params.append(edge_type)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY source_id"
    return conn.execute(sql, params).fetchall()


def set_verified(conn, source_id: str, value: bool = True) -> None:
    cur = conn.execute(
        "UPDATE sources SET scrapeability_verified = ?, updated_at = ? WHERE source_id = ?",
        (int(bool(value)), now_iso(), source_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise KeyError(f"unknown source_id: {source_id}")


def freeze(conn, *, label=None, notes=None, enabled_only=True):
    """Snapshot the current registry into an immutable, content-hashed version row."""
    rows = list_sources(conn, enabled_only=enabled_only)
    manifest = [dict(r) for r in rows]
    manifest_json = json.dumps(manifest, sort_keys=True, ensure_ascii=False)
    content_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    n_existing = conn.execute("SELECT COUNT(*) FROM registry_versions").fetchone()[0]
    if label is None:
        label = f"v{n_existing + 1}"
    conn.execute(
        "INSERT INTO registry_versions "
        "(label, frozen_at, n_sources, content_hash, manifest_json, notes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (label, now_iso(), len(manifest), content_hash, manifest_json, notes),
    )
    conn.commit()
    return conn.execute(
        "SELECT * FROM registry_versions WHERE label = ?", (label,)
    ).fetchone()


def list_versions(conn):
    return conn.execute(
        "SELECT * FROM registry_versions ORDER BY version_id"
    ).fetchall()


def get_version(conn, label_or_id):
    """Fetch a frozen version by label or numeric version_id. Manifest is parsed."""
    if isinstance(label_or_id, int) or str(label_or_id).isdigit():
        row = conn.execute(
            "SELECT * FROM registry_versions WHERE version_id = ?",
            (int(label_or_id),),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM registry_versions WHERE label = ?", (label_or_id,)
        ).fetchone()
    if row is None:
        raise KeyError(f"unknown registry version: {label_or_id}")
    out = dict(row)
    out["manifest"] = json.loads(out["manifest_json"])
    return out
