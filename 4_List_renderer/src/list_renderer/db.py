"""SQLite persistence for 4_List_renderer (local single-user, v1).

One DB at `data/list_renderer.db`, evolved by an ordered list of additive
migrations (repo pattern). The schema is tiered per spec §5 / §13:

  Tier A (shared / user-independent): sources, recipes, items, fetch_log, llm_tasks
  Tier B (per-user):                  users, boards, subscriptions, interests,
                                      interactions, ranking_state
  Auth scaffold (D27; hosted, empty in v1): auth_identities, sessions, login_audit

v1 decisions baked in: every Tier-B row carries `user_id` (D16); board-scoped rows
carry `board_id` (D18); items store metadata only (D17); interactions reserve a
`context_json` feature snapshot (D19); the `llm_tasks` ledger carries
`task_type` + `billing_context`/`payer` (D24); per-item `language` (D20/NN-3).

Tables beyond Phase 1's needs are created now so later phases (resolver, ranking,
interactions) are additive, not migrations of live data.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# The single local user id used in single-user mode (D16: user_id from day 1).
LOCAL_USER_ID = "local"
# The single default board in v1 (D18: board_id carried, one board for now).
DEFAULT_BOARD_ID = "default"


def now_iso() -> str:
    """UTC ISO-8601 timestamp (NN-3: dates normalized to UTC)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Ordered, append-only migrations. NEVER edit a shipped migration; add a new one.
# Each is (id, sql). `_apply_migrations` runs the ones past `user_version`.
_MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        # --- Tier B: identity / account (D27 — forward-compatible from v1) ---
        # v1 has exactly one row: the 'local' user (kind='local', no auth).
        # Columns are nullable so the local user is a degenerate account; going
        # multi-user only populates them — no migration (all data is user_id-scoped).
        """
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            kind          TEXT NOT NULL DEFAULT 'local',   -- local | account
            email         TEXT UNIQUE,
            display_name  TEXT,
            role          TEXT NOT NULL DEFAULT 'user',     -- user | admin (gates D13 review)
            status        TEXT NOT NULL DEFAULT 'active',   -- active | suspended | deleted
            plan          TEXT NOT NULL DEFAULT 'free',     -- free | byok | pro ... (D24)
            byok_key_ref  TEXT,                             -- ref to encrypted BYOK key (D24)
            created_at    TEXT NOT NULL,
            updated_at    TEXT
        );
        CREATE TABLE IF NOT EXISTS boards (
            id          TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            name        TEXT NOT NULL,
            config_json TEXT,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (user_id, id)
        );

        -- --- Tier A: source catalog ---
        CREATE TABLE IF NOT EXISTS sources (
            id          TEXT PRIMARY KEY,
            kind        TEXT NOT NULL,          -- rss | web | sqlite | http_api | file | biopharm ...
            adapter     TEXT NOT NULL,          -- which adapter handles it (file|biopharm|news|...)
            name        TEXT NOT NULL,
            label       TEXT,
            config_json TEXT,                   -- per-source params + auth + politeness state
            recipe_id   TEXT,                   -- FK -> recipes.id (resolver, later phase)
            origin      TEXT NOT NULL DEFAULT 'seed',  -- seed | user | discovered
            language    TEXT,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        -- --- Tier B: which sources a user follows, per board (D18) ---
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id     TEXT NOT NULL,
            board_id    TEXT NOT NULL,
            source_id   TEXT NOT NULL,
            position    INTEGER NOT NULL DEFAULT 0,
            enabled     INTEGER NOT NULL DEFAULT 1,
            created_at  TEXT NOT NULL,
            PRIMARY KEY (user_id, board_id, source_id)
        );
        """,
    ),
    (
        2,
        # --- Tier A: Claude-resolved recipes + content cache + ledgers ---
        # Created now so later phases are additive (resolver=M3, fetch cache=M2).
        """
        CREATE TABLE IF NOT EXISTS recipes (
            id                TEXT PRIMARY KEY,
            source_signature  TEXT NOT NULL,
            kind              TEXT NOT NULL,
            fetch_spec_json   TEXT,
            extract_spec_json TEXT,
            prompt_version    TEXT,
            resolved_by       TEXT,
            confidence        REAL,
            status            TEXT NOT NULL DEFAULT 'active',  -- active|needs_revalidation|failed
            created_at        TEXT NOT NULL,
            last_validated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_recipes_sig ON recipes (source_signature);

        CREATE TABLE IF NOT EXISTS items (
            id               TEXT PRIMARY KEY,   -- global hash (source_id + url)
            source_id        TEXT NOT NULL,
            item_key         TEXT,               -- source-local id
            title            TEXT,
            url              TEXT,
            snippet          TEXT,               -- metadata only (D17: no full text)
            published_at_utc TEXT,
            language         TEXT,
            captured_at      TEXT NOT NULL,
            topics_json      TEXT,
            raw_hash         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_items_source ON items (source_id);

        -- Source fetch health/telemetry (NN-4)
        CREATE TABLE IF NOT EXISTS fetch_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id   TEXT,
            started_at  TEXT NOT NULL,
            status      TEXT,
            http_status INTEGER,
            n_items     INTEGER,
            bytes       INTEGER,
            error       TEXT
        );

        -- Single ledger for every Claude call routed through run_llm_task (D24)
        CREATE TABLE IF NOT EXISTS llm_tasks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type       TEXT NOT NULL,      -- resolve_recipe | summarize_item | ...
            ref_id          TEXT,               -- what it acted on (source_id / item id)
            billing_context TEXT NOT NULL DEFAULT 'self',
            payer           TEXT NOT NULL DEFAULT 'self',
            user_id         TEXT,
            usd             REAL,
            input_tokens    INTEGER,
            output_tokens   INTEGER,
            cache_read      INTEGER,
            status          TEXT,
            created_at      TEXT NOT NULL
        );
        """,
    ),
    (
        3,
        # --- Tier B: interests, interactions, learned weights ---
        # Created now; consumed by M5/M6/M7 in later phases.
        """
        CREATE TABLE IF NOT EXISTS interests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT NOT NULL,
            kind        TEXT NOT NULL,          -- topic | site | query | ticker
            value       TEXT NOT NULL,
            weight      REAL NOT NULL DEFAULT 1.0,
            status      TEXT NOT NULL DEFAULT 'active',
            created_at  TEXT NOT NULL
        );

        -- Append-only learning log; context_json = verbatim feature snapshot (D19)
        CREATE TABLE IF NOT EXISTS interactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      TEXT NOT NULL,
            board_id     TEXT NOT NULL,
            item_id      TEXT NOT NULL,
            action       TEXT NOT NULL,         -- impression|open|read_more|like|hide|dwell|scroll_past
            value        REAL,
            dwell_ms     INTEGER,
            context_json TEXT,
            created_at   TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions (user_id);

        CREATE TABLE IF NOT EXISTS ranking_state (
            user_id     TEXT NOT NULL,
            feature     TEXT NOT NULL,
            weight      REAL NOT NULL,
            updated_at  TEXT NOT NULL,
            PRIMARY KEY (user_id, feature)
        );

        CREATE TABLE IF NOT EXISTS render_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT,
            board_id    TEXT,
            started_at  TEXT NOT NULL,
            n_sources   INTEGER,
            n_items     INTEGER,
            mode        TEXT,
            notes       TEXT
        );
        """,
    ),
    (
        4,
        # --- Auth scaffold (D27) — hosted multi-user; EMPTY in local v1 ---
        # Created now so the localhost->cloud scale-up is additive (no migration).
        # localhost is single-user + trusted: no sign-in, no sessions in v1.
        """
        -- How a user authenticates; one user -> many linked identities.
        CREATE TABLE IF NOT EXISTS auth_identities (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id          TEXT NOT NULL,
            provider         TEXT NOT NULL,     -- password | google | github | apple | magic_link
            provider_subject TEXT NOT NULL,     -- OAuth sub, or email for password/magic_link
            password_hash    TEXT,              -- argon2id; only when provider='password'
            created_at       TEXT NOT NULL,
            last_used_at     TEXT,
            UNIQUE (provider, provider_subject)
        );

        -- Active logins. Store only a HASH of the token, never the raw token.
        CREATE TABLE IF NOT EXISTS sessions (
            id           TEXT PRIMARY KEY,       -- session/token id
            user_id      TEXT NOT NULL,
            token_hash   TEXT NOT NULL,
            ip_hash      TEXT,                   -- IP is PII -> hashed/truncated (D14/D27)
            user_agent   TEXT,
            created_at   TEXT NOT NULL,
            last_seen_at TEXT,
            expires_at   TEXT,
            revoked      INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);

        -- Security/audit log (incl. failed attempts for unknown users).
        CREATE TABLE IF NOT EXISTS login_audit (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT,                   -- nullable: unknown-user failed attempts
            event       TEXT NOT NULL,          -- login_success | login_fail | logout | token_refresh | ...
            ip_hash     TEXT,
            user_agent  TEXT,
            detail      TEXT,
            created_at  TEXT NOT NULL
        );
        """,
    ),
]

SCHEMA_VERSION = _MIGRATIONS[-1][0]


def connect(db_path: Path | str, *, read_only: bool = False) -> sqlite3.Connection:
    """Open the DB. On a writable open, apply pending migrations + ensure the
    local user and default board exist."""
    db_path = Path(db_path)
    if read_only:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    _apply_migrations(conn)
    _ensure_local_identity(conn)
    return conn


def _apply_migrations(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version;").fetchone()[0]
    for version, sql in _MIGRATIONS:
        if version > current:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {version};")
    conn.commit()


def _ensure_local_identity(conn: sqlite3.Connection) -> None:
    """Single-user mode: guarantee the local user + default board rows (D16/D18)."""
    ts = now_iso()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, kind, created_at) VALUES (?, 'local', ?)",
        (LOCAL_USER_ID, ts),
    )
    conn.execute(
        "INSERT OR IGNORE INTO boards (id, user_id, name, config_json, created_at) "
        "VALUES (?, ?, 'My board', NULL, ?)",
        (DEFAULT_BOARD_ID, LOCAL_USER_ID, ts),
    )
    conn.commit()
