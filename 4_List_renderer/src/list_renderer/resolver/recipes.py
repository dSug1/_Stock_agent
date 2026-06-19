"""Recipe persistence (Tier A `recipes` table).

A recipe is the durable artifact (D4): keyed by source_signature so identical
sources reuse one recipe. `fetch_spec_json` says how to fetch (adapter + params);
`extract_spec_json` says how to extract fields.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from ..db import now_iso


def source_signature(kind: str, url: str) -> str:
    return hashlib.sha1(f"{kind}\x1f{url}".encode("utf-8")).hexdigest()[:16]


def save_recipe(
    conn: sqlite3.Connection,
    *,
    signature: str,
    kind: str,
    fetch_spec: dict,
    extract_spec: dict | None,
    prompt_version: str,
    resolved_by: str,
    confidence: float | None,
) -> str:
    """Upsert a recipe keyed by source_signature. Returns the recipe id."""
    rid = f"rcp_{signature}"
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO recipes (id, source_signature, kind, fetch_spec_json,
            extract_spec_json, prompt_version, resolved_by, confidence, status,
            created_at, last_validated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            kind=excluded.kind, fetch_spec_json=excluded.fetch_spec_json,
            extract_spec_json=excluded.extract_spec_json,
            prompt_version=excluded.prompt_version, resolved_by=excluded.resolved_by,
            confidence=excluded.confidence, status='active',
            last_validated_at=excluded.last_validated_at
        """,
        (rid, signature, kind, json.dumps(fetch_spec),
         json.dumps(extract_spec) if extract_spec else None,
         prompt_version, resolved_by, confidence, ts, ts),
    )
    conn.commit()
    return rid


def _row_to_recipe(row: sqlite3.Row | None) -> dict | None:
    if not row:
        return None
    d = dict(row)
    d["fetch_spec"] = json.loads(d.pop("fetch_spec_json") or "{}")
    d["extract_spec"] = json.loads(d.pop("extract_spec_json") or "null")
    return d


def get_recipe_by_signature(conn: sqlite3.Connection, signature: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM recipes WHERE source_signature=? AND status='active'",
        (signature,),
    ).fetchone()
    return _row_to_recipe(row)


def get_recipe_by_id(conn: sqlite3.Connection, recipe_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM recipes WHERE id=?", (recipe_id,)).fetchone()
    return _row_to_recipe(row)
