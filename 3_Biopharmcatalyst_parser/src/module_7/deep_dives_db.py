"""Module 7 — SQLite store for Claude API deep-dive output.

Single file `data/claude_deep_dives.db` holding four tables:

  deep_dives             — one row per (catalyst PK + run_id) carrying:
                             * Claude's raw text (verbatim, per D32)
                             * Validated structured fields
                             * Python-applied modifiers + final expectancy
  deep_dive_runs         — run-level audit (mode, batch_id, tokens, $)
  deep_dive_errors       — per-ticker parse / API failures
  web_search_cache       — Anthropic server_tool_use results, for audit

Per spec §5.12 storage rule #1: machine-generated data lives in SQLite,
never as JSON files on disk. Raw responses go in `deep_dives.raw_text`
(queryable via SQL), nested objects as TEXT json (queryable via
`json_extract`).

Spec: spec/module_7_spec.md §5.7.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

LLM_SCORES_SCHEMA_VERSION = "m7-v1"


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS deep_dives (
    -- PK matches catalyst_snapshots composite PK plus run_id.
    snapshot_date         TEXT    NOT NULL,
    ticker                TEXT    NOT NULL,
    drug                  TEXT    NOT NULL,
    nct_number            TEXT    NOT NULL,
    next_catalyst_type    TEXT    NOT NULL,
    run_id                INTEGER NOT NULL,

    -- Claude raw fields (numeric)
    p_clinical            REAL,
    p_clinical_low        REAL,
    p_clinical_high       REAL,
    expected_move_on_hit_pct   REAL,
    expected_move_on_miss_pct  REAL,
    rnpv_total_usd        REAL,
    rnpv_per_share_usd    REAL,
    lead_indication       TEXT,
    management_track_record_score   REAL,
    acquisition_target_score        REAL,

    -- Claude structured blocks (JSON in TEXT)
    rnpv_by_indication_json    TEXT,
    drug_profile_json          TEXT,
    clinical_evidence_json     TEXT,
    financial_overhang_json    TEXT,
    catalyst_date_sanity_json  TEXT,
    key_risks_json             TEXT,
    thesis_summary             TEXT,
    reasoning_trace            TEXT,

    -- Python-applied modifiers + final expectancy
    insider_score_input            REAL,
    fund_accumulation_score_input  REAL,
    momentum_score_input           REAL,
    m_insider                      REAL,
    m_funds                        REAL,
    m_momentum                     REAL,
    p_final                        REAL,
    e_move_pct                     REAL,
    expectancy_pct                 REAL,
    weeks_to_catalyst_mid          INTEGER,
    expectancy_per_week_pct        REAL,

    -- Metadata
    prompt_version        TEXT NOT NULL,           -- 'm7-v1:<sha7>'
    model                 TEXT NOT NULL,
    response_id           TEXT,
    raw_text              TEXT,                    -- the verbatim Anthropic reply
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cache_read_tokens     INTEGER,
    cache_creation_tokens INTEGER,
    web_search_calls      INTEGER,
    usd_cost              REAL,
    -- Cache identity (D17): drug | stage | next_catalyst_type | catalyst_date.
    -- Two rows with the same (ticker, drug, nct_number, next_catalyst_type) and
    -- the same catalyst_signature represent the SAME catalyst state — so the
    -- second M7 run can skip the API call. Differences in any of the four
    -- component fields invalidate the cache.
    catalyst_signature    TEXT,
    created_at            TEXT NOT NULL,
    PRIMARY KEY (snapshot_date, ticker, drug, nct_number, next_catalyst_type, run_id)
);

CREATE INDEX IF NOT EXISTS idx_dd_ticker  ON deep_dives(ticker, snapshot_date DESC);
CREATE INDEX IF NOT EXISTS idx_dd_run     ON deep_dives(run_id);
CREATE INDEX IF NOT EXISTS idx_dd_expect  ON deep_dives(expectancy_per_week_pct DESC);

CREATE TABLE IF NOT EXISTS deep_dive_runs (
    run_id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date                TEXT,
    prompt_version               TEXT NOT NULL,
    model                        TEXT NOT NULL,
    mode                         TEXT NOT NULL,    -- 'sync' | 'batch'
    feed_size                    INTEGER NOT NULL,
    batch_id                     TEXT,             -- NULL for sync, Anthropic UUID for batch
    gate_config_json             TEXT,             -- selection_source + cost ceiling + caps
    wall_time_s                  REAL,
    input_tokens_total           INTEGER,
    output_tokens_total          INTEGER,
    cache_read_tokens_total      INTEGER,
    cache_creation_tokens_total  INTEGER,
    web_search_calls_total       INTEGER,
    usd_cost_total               REAL,
    usd_cost_list_price          REAL,
    opened_at                    TEXT NOT NULL,
    closed_at                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_dd_runs_batch ON deep_dive_runs(batch_id);

CREATE TABLE IF NOT EXISTS deep_dive_errors (
    error_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL,
    ticker        TEXT NOT NULL,
    snapshot_date TEXT,
    error_kind    TEXT NOT NULL,    -- 'json_parse_fail' | 'schema_violation' | 'api_error' | 'catalyst_already_passed' | 'db_write_error'
    error_detail  TEXT,
    raw_text      TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dd_err_run ON deep_dive_errors(run_id);

CREATE TABLE IF NOT EXISTS web_search_cache (
    url            TEXT PRIMARY KEY,
    ticker         TEXT,
    snapshot_date  TEXT,
    run_id         INTEGER,
    search_query   TEXT,
    title          TEXT,
    content        TEXT,
    domain         TEXT,
    published_date TEXT,
    cached_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dd_ws_ticker ON web_search_cache(ticker, cached_at DESC);
"""


# Additive migrations — append (table, col, decl) tuples here.
_ADDITIVE_MIGRATIONS: list[tuple[str, str, str]] = [
    # D17 — catalyst-identity cache. Computed at write time so cache lookup
    # on a subsequent run only needs an equality check.
    ("deep_dives", "catalyst_signature", "TEXT"),
]


def _apply_additive_migrations(conn: sqlite3.Connection) -> None:
    for table, col, decl in _ADDITIVE_MIGRATIONS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.OperationalError:
            continue
        if col not in cols:
            log.info("claude_deep_dives.db: ADD COLUMN %s.%s %s", table, col, decl)
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "claude_deep_dives.db"


def init_deep_dives_db(db_path: Path | str | None = None) -> Path:
    """Create tables + indexes + WAL. Idempotent."""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=10) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.execute("PRAGMA journal_mode=WAL")
        _apply_additive_migrations(conn)
        conn.commit()
    return path


@contextmanager
def db_connect(db_path: Path | str | None = None):
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    init_deep_dives_db(path)
    cx = sqlite3.connect(path, timeout=30)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_or_null(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False, default=str)


# ─── run lifecycle ───────────────────────────────────────────────────────────


def open_run(
    conn: sqlite3.Connection,
    *,
    snapshot_date: Optional[str],
    prompt_version: str,
    model: str,
    mode: str,
    feed_size: int,
    gate_config: dict,
    batch_id: Optional[str] = None,
) -> int:
    """Open a deep_dive_runs row; return new run_id. Caller must commit."""
    cur = conn.execute(
        """
        INSERT INTO deep_dive_runs(
            snapshot_date, prompt_version, model, mode, feed_size,
            batch_id, gate_config_json, opened_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (snapshot_date, prompt_version, model, mode, feed_size,
         batch_id, _json_or_null(gate_config), _now_iso()),
    )
    return int(cur.lastrowid)


def update_run_batch_id(
    conn: sqlite3.Connection, *, run_id: int, batch_id: str
) -> None:
    """D51 crash-recovery: persist batch_id immediately after submit, before poll."""
    conn.execute(
        "UPDATE deep_dive_runs SET batch_id = ? WHERE run_id = ?",
        (batch_id, run_id),
    )


def close_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    wall_time_s: float,
    input_tokens_total: int,
    output_tokens_total: int,
    cache_read_tokens_total: int,
    cache_creation_tokens_total: int,
    web_search_calls_total: int,
    usd_cost_total: float,
    usd_cost_list_price: float,
) -> None:
    conn.execute(
        """
        UPDATE deep_dive_runs SET
            wall_time_s = ?,
            input_tokens_total = ?, output_tokens_total = ?,
            cache_read_tokens_total = ?, cache_creation_tokens_total = ?,
            web_search_calls_total = ?,
            usd_cost_total = ?, usd_cost_list_price = ?,
            closed_at = ?
        WHERE run_id = ?
        """,
        (wall_time_s,
         input_tokens_total, output_tokens_total,
         cache_read_tokens_total, cache_creation_tokens_total,
         web_search_calls_total,
         usd_cost_total, usd_cost_list_price,
         _now_iso(), run_id),
    )


# ─── deep_dives upsert ───────────────────────────────────────────────────────


_DEEP_DIVE_COLS = (
    "snapshot_date", "ticker", "drug", "nct_number", "next_catalyst_type",
    "run_id",
    "p_clinical", "p_clinical_low", "p_clinical_high",
    "expected_move_on_hit_pct", "expected_move_on_miss_pct",
    "rnpv_total_usd", "rnpv_per_share_usd", "lead_indication",
    "management_track_record_score", "acquisition_target_score",
    "rnpv_by_indication_json", "drug_profile_json", "clinical_evidence_json",
    "financial_overhang_json", "catalyst_date_sanity_json", "key_risks_json",
    "thesis_summary", "reasoning_trace",
    "insider_score_input", "fund_accumulation_score_input", "momentum_score_input",
    "m_insider", "m_funds", "m_momentum",
    "p_final", "e_move_pct", "expectancy_pct",
    "weeks_to_catalyst_mid", "expectancy_per_week_pct",
    "prompt_version", "model", "response_id",
    "raw_text",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens",
    "web_search_calls", "usd_cost",
    "catalyst_signature",                       # D17 — cache key
    "created_at",
)


def upsert_deep_dive_row(conn: sqlite3.Connection, row: dict) -> None:
    """Insert/replace one deep_dives row.

    Caller is responsible for serialising any nested objects into the
    ``*_json`` keys with json.dumps before calling.
    """
    payload = {k: row.get(k) for k in _DEEP_DIVE_COLS}
    # setdefault won't fire because dict.get returns None as the value; we
    # need an explicit truthy check.
    if not payload.get("created_at"):
        payload["created_at"] = _now_iso()
    placeholders = ",".join(f":{c}" for c in _DEEP_DIVE_COLS)
    conn.execute(
        f"""
        INSERT OR REPLACE INTO deep_dives({",".join(_DEEP_DIVE_COLS)})
        VALUES({placeholders})
        """,
        payload,
    )


# ─── errors ──────────────────────────────────────────────────────────────────


def write_error_row(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    ticker: str,
    snapshot_date: Optional[str],
    error_kind: str,
    error_detail: str,
    raw_text: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO deep_dive_errors(run_id, ticker, snapshot_date,
                                     error_kind, error_detail, raw_text,
                                     created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, ticker, snapshot_date, error_kind,
         error_detail[:2000] if error_detail else None,
         raw_text, _now_iso()),
    )


# ─── web_search_cache ────────────────────────────────────────────────────────


def upsert_web_search_cache_row(
    conn: sqlite3.Connection,
    *,
    url: str,
    ticker: Optional[str],
    snapshot_date: Optional[str],
    run_id: Optional[int],
    search_query: Optional[str],
    title: Optional[str],
    content: Optional[str],
    domain: Optional[str],
    published_date: Optional[str],
) -> None:
    conn.execute(
        """
        INSERT INTO web_search_cache(url, ticker, snapshot_date, run_id,
                                     search_query, title, content, domain,
                                     published_date, cached_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            ticker         = COALESCE(excluded.ticker, web_search_cache.ticker),
            snapshot_date  = COALESCE(excluded.snapshot_date, web_search_cache.snapshot_date),
            run_id         = COALESCE(excluded.run_id, web_search_cache.run_id),
            search_query   = COALESCE(excluded.search_query, web_search_cache.search_query),
            title          = COALESCE(excluded.title, web_search_cache.title),
            content        = COALESCE(excluded.content, web_search_cache.content),
            domain         = COALESCE(excluded.domain, web_search_cache.domain),
            published_date = COALESCE(excluded.published_date, web_search_cache.published_date),
            cached_at      = excluded.cached_at
        """,
        (url, ticker, snapshot_date, run_id, search_query, title, content,
         domain, published_date, _now_iso()),
    )


# ─── read helpers ────────────────────────────────────────────────────────────


def latest_deep_dive_per_catalyst(
    conn: sqlite3.Connection, snapshot_date: str
) -> dict[tuple, dict]:
    """Return {(snapshot_date, ticker, drug, nct, next_catalyst_type): row}
    keyed to the latest run_id. Used by the renderer LEFT-JOIN.
    """
    rows = conn.execute(
        """
        SELECT d.* FROM deep_dives d
        JOIN (
            SELECT snapshot_date, ticker, drug, nct_number, next_catalyst_type,
                   MAX(run_id) AS max_run
            FROM deep_dives
            WHERE snapshot_date = ?
            GROUP BY snapshot_date, ticker, drug, nct_number, next_catalyst_type
        ) latest ON
            latest.snapshot_date = d.snapshot_date AND
            latest.ticker = d.ticker AND
            latest.drug = d.drug AND
            latest.nct_number = d.nct_number AND
            latest.next_catalyst_type = d.next_catalyst_type AND
            latest.max_run = d.run_id
        """,
        (snapshot_date,),
    ).fetchall()
    out: dict[tuple, dict] = {}
    for r in rows:
        key = (r["snapshot_date"], r["ticker"], r["drug"],
               r["nct_number"], r["next_catalyst_type"])
        out[key] = dict(r)
    return out
