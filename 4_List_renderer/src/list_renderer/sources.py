"""Source registry (M1) — CRUD over the Tier-A `sources` catalog and the
Tier-B `subscriptions` that put sources on a user's board.

v1 single-user: everything is scoped to the `local` user + `default` board, but
the API already takes `user_id` / `board_id` (D16/D18) so the hosted multi-user
port is additive.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import yaml

from .db import DEFAULT_BOARD_ID, LOCAL_USER_ID, now_iso


def seed_from_config(
    conn: sqlite3.Connection,
    config_path: Path | str,
    *,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> int:
    """Upsert sources from a YAML file and subscribe the board to them.
    Idempotent. Returns the number of sources seeded."""
    data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    entries = data.get("sources", [])
    for entry in entries:
        add_source(
            conn,
            source_id=entry["id"],
            kind=entry["kind"],
            adapter=entry["adapter"],
            name=entry.get("name", entry["id"]),
            label=entry.get("label"),
            config=entry.get("config") or {},
            origin="seed",
            on_board=bool(entry.get("on_board", True)),
            user_id=user_id,
            board_id=board_id,
        )
    conn.commit()
    return len(entries)


def add_source(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    kind: str,
    adapter: str,
    name: str,
    label: str | None = None,
    config: dict | None = None,
    origin: str = "user",
    on_board: bool = True,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> None:
    """Register a source (Tier A) and subscribe the board to it (Tier B).
    Upsert on `source_id`; preserves `created_at`."""
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO sources (id, kind, adapter, name, label, config_json,
                             origin, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            kind=excluded.kind, adapter=excluded.adapter, name=excluded.name,
            label=excluded.label, config_json=excluded.config_json,
            updated_at=excluded.updated_at
        """,
        (source_id, kind, adapter, name, label,
         json.dumps(config or {}), origin, ts, ts),
    )
    conn.execute(
        """
        INSERT INTO subscriptions (user_id, board_id, source_id, position,
                                   enabled, created_at)
        VALUES (?, ?, ?,
                COALESCE((SELECT MAX(position) + 1 FROM subscriptions
                          WHERE user_id=? AND board_id=?), 0),
                ?, ?)
        ON CONFLICT(user_id, board_id, source_id) DO UPDATE SET
            enabled=excluded.enabled
        """,
        (user_id, board_id, source_id, user_id, board_id,
         1 if on_board else 0, ts),
    )
    conn.commit()


def set_on_board(
    conn: sqlite3.Connection,
    source_id: str,
    enabled: bool,
    *,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> None:
    conn.execute(
        "UPDATE subscriptions SET enabled=? WHERE user_id=? AND board_id=? "
        "AND source_id=?",
        (1 if enabled else 0, user_id, board_id, source_id),
    )
    conn.commit()


def list_enabled_sources(
    conn: sqlite3.Connection,
    *,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> list[dict]:
    """Sources on the board (subscription enabled AND source enabled),
    ordered by position. Each dict includes a parsed `config`."""
    rows = conn.execute(
        """
        SELECT s.id, s.kind, s.adapter, s.name, s.label, s.config_json,
               s.recipe_id, sub.position
        FROM subscriptions sub
        JOIN sources s ON s.id = sub.source_id
        WHERE sub.user_id=? AND sub.board_id=? AND sub.enabled=1 AND s.enabled=1
        ORDER BY sub.position, s.name
        """,
        (user_id, board_id),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["config"] = json.loads(d.pop("config_json") or "{}")
        out.append(d)
    return out
