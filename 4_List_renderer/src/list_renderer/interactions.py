"""Interaction capture (M7) — the append-only learning log.

Every user signal (impression, open, read_more, like, hide, dwell, scroll_past)
is appended to the `interactions` table, never overwritten (D6 audit trail). Each
row carries a verbatim `context_json` feature/score snapshot at event time (D19),
so the ranking model (M6, Phase 5) stays trainable even after content/recipes
churn.

Two derived views used by the board today:
  - `hidden_item_ids` — items the user has hidden are filtered out of the board
    (an immediate, deterministic override; the learned-ranking version is D21).
  - `seen_item_ids` — items already impressed/opened, so the board can dim them
    (D22 seen/unread state; the ranking de-prioritization is Phase 5).
"""

from __future__ import annotations

import json
import sqlite3

from .db import DEFAULT_BOARD_ID, LOCAL_USER_ID, now_iso

# Canonical action vocabulary (spec §5 interactions.action).
ACTIONS = frozenset(
    {"impression", "open", "read_more", "like", "hide", "dwell", "scroll_past", "unhide"}
)


def record_interaction(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    action: str,
    value: float | None = None,
    dwell_ms: int | None = None,
    context: dict | None = None,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
    commit: bool = True,
) -> None:
    """Append one interaction. `context` is stored verbatim as the D19 snapshot."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action!r}")
    conn.execute(
        """
        INSERT INTO interactions
            (user_id, board_id, item_id, action, value, dwell_ms,
             context_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id, board_id, str(item_id), action,
            value, dwell_ms,
            json.dumps(context, ensure_ascii=False) if context is not None else None,
            now_iso(),
        ),
    )
    if commit:
        conn.commit()


def record_batch(
    conn: sqlite3.Connection,
    events: list[dict],
    *,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> int:
    """Append a batch of client-buffered events. Unknown actions are skipped
    (fail-open: one bad event never drops the whole batch). Returns n written."""
    n = 0
    for ev in events:
        action = ev.get("action")
        item_id = ev.get("item_id") or ev.get("id")
        if not item_id or action not in ACTIONS:
            continue
        dwell = ev.get("dwell_ms")
        record_interaction(
            conn,
            item_id=item_id,
            action=action,
            value=ev.get("value"),
            dwell_ms=int(dwell) if dwell is not None else None,
            context=ev.get("context"),
            user_id=user_id,
            board_id=board_id,
            commit=False,
        )
        n += 1
    conn.commit()
    return n


def hidden_item_ids(
    conn: sqlite3.Connection,
    *,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> set[str]:
    """Items currently hidden: a `hide` with no later `unhide` (append-only, so we
    compare the latest of each per item)."""
    rows = conn.execute(
        """
        SELECT item_id, action FROM interactions
        WHERE user_id=? AND board_id=? AND action IN ('hide', 'unhide')
        ORDER BY id
        """,
        (user_id, board_id),
    ).fetchall()
    state: dict[str, str] = {}
    for r in rows:
        state[r["item_id"]] = r["action"]
    return {iid for iid, act in state.items() if act == "hide"}


def seen_item_ids(
    conn: sqlite3.Connection,
    *,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> set[str]:
    """Items the user has already seen (impression/open/read_more) — D22."""
    rows = conn.execute(
        """
        SELECT DISTINCT item_id FROM interactions
        WHERE user_id=? AND board_id=?
          AND action IN ('impression', 'open', 'read_more')
        """,
        (user_id, board_id),
    ).fetchall()
    return {r["item_id"] for r in rows}


def liked_item_ids(
    conn: sqlite3.Connection,
    *,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> set[str]:
    """Items currently liked: a `like` with no later `hide` (latest wins)."""
    rows = conn.execute(
        """
        SELECT item_id, action FROM interactions
        WHERE user_id=? AND board_id=? AND action IN ('like', 'hide', 'unhide')
        ORDER BY id
        """,
        (user_id, board_id),
    ).fetchall()
    state: dict[str, str] = {}
    for r in rows:
        state[r["item_id"]] = r["action"]
    return {iid for iid, act in state.items() if act == "like"}
