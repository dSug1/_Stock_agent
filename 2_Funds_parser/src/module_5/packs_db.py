"""Module 5 — SQLite primary store for context packs.

Single file (`context_packs.db` at 2_Funds_parser root) holds every pack across
quarters and pack_versions. Store + cache unified; no separate cache DB.

Schema (D23 / D24 / D27 / D28 / D29):
  Filter columns lifted from the blob: archetype, best_horizon, composite_*,
  score_*, sector, industry, market_cap_usd, fund_count, new_positions,
  increased_positions, qoq_fund_count_change. Module 6 filters on these
  natively at SQL level; no JSON parsing needed for gating.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS context_packs (
    ticker                TEXT NOT NULL,
    quarter               TEXT NOT NULL,
    pack_version          TEXT NOT NULL,
    source_rank_hash      TEXT NOT NULL,

    archetype             TEXT,
    best_horizon          TEXT,
    composite_best        REAL,
    composite_3mo         REAL,
    composite_12mo        REAL,
    score_3mo             INTEGER,
    score_12mo            INTEGER,
    match_confidence      REAL,
    sector                TEXT,
    industry              TEXT,
    market_cap_usd        INTEGER,
    fund_count            INTEGER,
    new_positions         INTEGER,
    increased_positions   INTEGER,
    qoq_fund_count_change INTEGER,

    pack_json             TEXT NOT NULL,

    cache_status          TEXT NOT NULL,
    built_at              TEXT NOT NULL,

    PRIMARY KEY (ticker, quarter, pack_version)
);
"""

_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_packs_quarter_score "
    "ON context_packs(quarter, composite_best DESC);",
    "CREATE INDEX IF NOT EXISTS idx_packs_quarter_horizon "
    "ON context_packs(quarter, best_horizon);",
    "CREATE INDEX IF NOT EXISTS idx_packs_quarter_sector "
    "ON context_packs(quarter, sector);",
    "CREATE INDEX IF NOT EXISTS idx_packs_quarter_mcap "
    "ON context_packs(quarter, market_cap_usd);",
    "CREATE INDEX IF NOT EXISTS idx_packs_quarter_newpos "
    "ON context_packs(quarter, new_positions DESC);",
    "CREATE INDEX IF NOT EXISTS idx_packs_quarter_qoq "
    "ON context_packs(quarter, qoq_fund_count_change DESC);",
]


def init_packs_db(db_path: Path) -> None:
    """Create the context_packs table + indexes. Enables WAL. Idempotent."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.executescript(_SCHEMA_SQL)
        for stmt in _INDEX_SQL:
            conn.execute(stmt)
        conn.commit()


def probe_pack(
    conn: sqlite3.Connection,
    ticker: str,
    quarter: str,
    pack_version: str,
    source_rank_hash: str,
) -> Optional[dict]:
    """Return the cached pack dict if (ticker, quarter, pack_version) exists AND
    the stored source_rank_hash matches. Otherwise None.

    Hash mismatch short-circuits without decoding the JSON blob.
    """
    row = conn.execute(
        "SELECT source_rank_hash, pack_json FROM context_packs "
        "WHERE ticker = ? AND quarter = ? AND pack_version = ?",
        (ticker, quarter, pack_version),
    ).fetchone()
    if row is None:
        return None
    stored_hash, pack_json = row
    if stored_hash != source_rank_hash:
        return None
    try:
        return json.loads(pack_json)
    except json.JSONDecodeError as e:
        log.warning("Corrupt pack_json for %s/%s: %s — treating as miss", ticker, quarter, e)
        return None


def _lift_filter_columns(pack: dict) -> dict:
    """Extract indexed filter columns from the pack blob for the DB row."""
    verdict = pack.get("archetype_verdict", {}) or {}
    ident = pack.get("identity", {}) or {}
    snap = pack.get("market_snapshot", {}) or {}
    fund = pack.get("fund_accumulation", {}) or {}
    return {
        "archetype": verdict.get("archetype"),
        "best_horizon": verdict.get("best_horizon"),
        "composite_best": verdict.get("composite_best"),
        "composite_3mo": verdict.get("composite_3mo"),
        "composite_12mo": verdict.get("composite_12mo"),
        "score_3mo": verdict.get("score_3mo"),
        "score_12mo": verdict.get("score_12mo"),
        "match_confidence": verdict.get("match_confidence"),
        "sector": ident.get("sector"),
        "industry": ident.get("industry"),
        "market_cap_usd": snap.get("market_cap_usd"),
        "fund_count": fund.get("fund_count"),
        "new_positions": fund.get("new_positions"),
        "increased_positions": fund.get("increased_positions"),
        "qoq_fund_count_change": fund.get("qoq_fund_count_change"),
    }


def upsert_pack(
    conn: sqlite3.Connection,
    pack: dict,
    quarter: str,
    source_rank_hash: str,
    cache_status: str,
    pack_version: str,
    built_at: str,
) -> None:
    """INSERT OR REPLACE one pack row. Commit is caller's responsibility (we
    expect a single transaction wrapping the whole run)."""
    ticker = pack["identity"]["ticker"]
    cols = _lift_filter_columns(pack)
    pack_json = json.dumps(pack, sort_keys=True, separators=(",", ":"))
    conn.execute(
        """
        INSERT OR REPLACE INTO context_packs (
            ticker, quarter, pack_version, source_rank_hash,
            archetype, best_horizon, composite_best, composite_3mo, composite_12mo,
            score_3mo, score_12mo, match_confidence,
            sector, industry, market_cap_usd,
            fund_count, new_positions, increased_positions, qoq_fund_count_change,
            pack_json, cache_status, built_at
        ) VALUES (
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?
        )
        """,
        (
            ticker, quarter, pack_version, source_rank_hash,
            cols["archetype"], cols["best_horizon"], cols["composite_best"],
            cols["composite_3mo"], cols["composite_12mo"],
            cols["score_3mo"], cols["score_12mo"], cols["match_confidence"],
            cols["sector"], cols["industry"], cols["market_cap_usd"],
            cols["fund_count"], cols["new_positions"],
            cols["increased_positions"], cols["qoq_fund_count_change"],
            pack_json, cache_status, built_at,
        ),
    )


def mark_cache_hit(
    conn: sqlite3.Connection,
    ticker: str,
    quarter: str,
    pack_version: str,
) -> None:
    """Refresh `cache_status` to 'cache_hit' for this run. No blob rewrite."""
    conn.execute(
        "UPDATE context_packs SET cache_status = 'cache_hit' "
        "WHERE ticker = ? AND quarter = ? AND pack_version = ?",
        (ticker, quarter, pack_version),
    )


def query_packs_for_quarter(
    conn: sqlite3.Connection,
    quarter: str,
    pack_version: str,
    *,
    composite_best_min: Optional[float] = None,
    best_horizon: Optional[str] = None,
    sector_allowlist: Optional[list[str]] = None,
    industry_allowlist: Optional[list[str]] = None,
    market_cap_max_usd: Optional[int] = None,
    market_cap_min_usd: Optional[int] = None,
    rescue_enabled: bool = False,
    rescue_new_positions_min: int = 1,
    rescue_qoq_fund_count_change_min: int = 1,
    rescue_train_has_left_archetypes: Optional[list[str]] = None,
) -> list[dict]:
    """Return rows (as dicts) from context_packs matching all gates.

    D21 is the main path (`composite_best >= composite_best_min`). D29 is an OR
    rescue leg (active when `rescue_enabled=True AND composite_best_min is not
    None`). D27 (sector/industry) and D28 (market cap) are outer AND filters
    and apply to rescued rows too.

    Used by Module 6 at query time and by the HTML report for previews.
    """
    sql = [
        "SELECT ticker, archetype, best_horizon, composite_best, composite_3mo,",
        "       composite_12mo, score_3mo, score_12mo, sector, industry,",
        "       market_cap_usd, fund_count, new_positions, increased_positions,",
        "       qoq_fund_count_change, pack_json, cache_status, built_at",
        "FROM context_packs",
        "WHERE quarter = ? AND pack_version = ?",
    ]
    params: list[Any] = [quarter, pack_version]

    # D21 main path (+ optional D29 rescue OR leg).
    rescue_active = (
        rescue_enabled
        and composite_best_min is not None
    )
    if composite_best_min is not None and not rescue_active:
        sql.append("AND composite_best >= ?")
        params.append(float(composite_best_min))
    elif rescue_active:
        train_left = rescue_train_has_left_archetypes or []
        placeholders = ",".join("?" for _ in train_left) or "''"
        sql.append(
            "AND (composite_best >= ? "
            f"     OR (archetype NOT IN ({placeholders}) "
            "          AND (new_positions >= ? OR qoq_fund_count_change >= ?)))"
        )
        params.append(float(composite_best_min))
        params.extend(train_left)
        params.append(int(rescue_new_positions_min))
        params.append(int(rescue_qoq_fund_count_change_min))

    if best_horizon is not None:
        sql.append("AND best_horizon = ?")
        params.append(best_horizon)

    if sector_allowlist:
        placeholders = ",".join("?" for _ in sector_allowlist)
        sql.append(f"AND sector IN ({placeholders})")
        params.extend(sector_allowlist)

    if industry_allowlist:
        placeholders = ",".join("?" for _ in industry_allowlist)
        sql.append(f"AND industry IN ({placeholders})")
        params.extend(industry_allowlist)

    if market_cap_max_usd is not None:
        sql.append("AND market_cap_usd <= ?")
        params.append(int(market_cap_max_usd))

    if market_cap_min_usd is not None:
        sql.append("AND market_cap_usd >= ?")
        params.append(int(market_cap_min_usd))

    sql.append("ORDER BY composite_best DESC, new_positions DESC, ticker ASC")

    rows = conn.execute("\n".join(sql), params).fetchall()
    cols = [
        "ticker", "archetype", "best_horizon", "composite_best", "composite_3mo",
        "composite_12mo", "score_3mo", "score_12mo", "sector", "industry",
        "market_cap_usd", "fund_count", "new_positions", "increased_positions",
        "qoq_fund_count_change", "pack_json", "cache_status", "built_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


def count_packs(conn: sqlite3.Connection, quarter: str, pack_version: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM context_packs WHERE quarter = ? AND pack_version = ?",
        (quarter, pack_version),
    ).fetchone()
    return int(row[0]) if row else 0


def db_size_bytes(db_path: Path) -> int:
    try:
        return db_path.stat().st_size
    except FileNotFoundError:
        return 0
