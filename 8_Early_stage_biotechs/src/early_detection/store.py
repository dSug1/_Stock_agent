"""SQLite universe store + DAO (Phase-1 §2).

Single durable store at ``data/early_detection.db``. Schema evolves via ordered additive migrations
tracked by ``PRAGMA user_version`` (repo convention — same idiom as ``platform_discoverer/store.py``
and ``hype_parser/db.py``). Schema v1 creates six tables: ``entity`` · ``listing`` · ``signal`` ·
``reconciliation_queue`` · ``audit_log`` · ``run_meta``.

Design posture (inherited repo discipline): parameterized SQL only; missing data is KEPT + flagged,
never silently dropped; possible duplicates that no hard key resolves go to ``reconciliation_queue``
rather than being force-merged (spec §2.3). Decision D1: SQLite, not Postgres.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .models import AuditEntry, Entity, ReconRow, SignalRecord

log = logging.getLogger(__name__)

SCHEMA_VERSION = 7


def now_iso() -> str:
    """UTC timestamp to the second (repo datetime discipline)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_iso() -> str:
    """UTC calendar date."""
    return datetime.now(timezone.utc).date().isoformat()


def _norm_cik(cik: Optional[str]) -> Optional[str]:
    """Zero-pad a CIK to 10 digits (SEC canonical form) so lookups match stored values.

    Duplicated here (rather than imported from ``identity``) to avoid a store↔identity import cycle.
    """
    if not cik:
        return None
    import re
    digits = re.sub(r"\D", "", str(cik))
    return digits.zfill(10) if digits else None


def _dumps(obj: Any) -> Optional[str]:
    """JSON-encode a list/dict column value; ``None`` passes through as SQL NULL."""
    if obj is None:
        return None
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _loads(text: Optional[str], default: Any) -> Any:
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default


# ── Migrations ──────────────────────────────────────────────────────────────────

def _migration_1(conn: sqlite3.Connection) -> None:
    """Schema v1 — the Phase-1 universe tables (phase1 build spec §2)."""
    conn.executescript(
        """
        CREATE TABLE entity (
            entity_id               TEXT PRIMARY KEY,   -- deterministic: lei:/isin:/cik:/tkx: (§4)
            legal_name              TEXT NOT NULL,
            common_name             TEXT,
            ticker_primary          TEXT,
            exchange_primary        TEXT,
            isin                    TEXT,
            lei                     TEXT,
            cik                     TEXT,               -- SEC CIK, zero-padded 10 (the join key M6 lacks)
            jurisdiction            TEXT,
            filer_type              TEXT,               -- domestic | FPI | other
            sector_code_raw         TEXT,               -- source-native (SIC / GICS)
            sector_code_normalized  TEXT,               -- therapeutics|diagnostics|tools_platform|devices|agbio|other
            market_cap_usd          REAL,
            mktcap_unknown          INTEGER NOT NULL DEFAULT 0,
            in_existing_universe    INTEGER NOT NULL DEFAULT 0,  -- §2.4 priority tier (seeded from M6)
            is_live                 INTEGER NOT NULL DEFAULT 1,
            source_provenance       TEXT,               -- JSON array of provider ids
            first_seen              TEXT,
            last_seen               TEXT
        );
        CREATE INDEX ix_entity_cik ON entity(cik);
        CREATE INDEX ix_entity_lei ON entity(lei);
        CREATE INDEX ix_entity_isin ON entity(isin);
        CREATE INDEX ix_entity_ticker ON entity(ticker_primary);

        CREATE TABLE listing (
            listing_id  INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id   TEXT NOT NULL,
            ticker      TEXT,
            exchange    TEXT,
            country     TEXT,
            isin        TEXT,
            mic         TEXT,
            is_primary  INTEGER NOT NULL DEFAULT 0,
            provenance  TEXT,                            -- JSON array
            UNIQUE (entity_id, ticker, exchange)
        );
        CREATE INDEX ix_listing_entity ON listing(entity_id);

        -- Spec §3 shared signals table: created now, unused until Phase 2 (pure insert, no migration).
        CREATE TABLE signal (
            signal_id       TEXT PRIMARY KEY,
            entity_id       TEXT,                        -- nullable: signal may precede entity match
            signal_type     TEXT NOT NULL,
            source          TEXT NOT NULL,
            raw_payload_json TEXT,
            detected_at     TEXT,
            event_date      TEXT,
            language        TEXT
        );
        CREATE INDEX ix_signal_entity ON signal(entity_id);
        CREATE INDEX ix_signal_type ON signal(signal_type);

        -- Unmatched / ambiguous entities for manual review — never force-merge (§2.3).
        CREATE TABLE reconciliation_queue (
            row_id          INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_json  TEXT NOT NULL,
            reason          TEXT NOT NULL,               -- no_key_match|ambiguous_multi_match|conflicting_lei
            added_at        TEXT,
            resolved        INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            run_id      TEXT,
            entity_id   TEXT,
            stage       TEXT NOT NULL,
            action      TEXT NOT NULL,                   -- admitted|merged|flagged|queued|refreshed
            reason      TEXT,
            detail_json TEXT
        );
        CREATE INDEX ix_audit_entity ON audit_log(entity_id);

        CREATE TABLE run_meta (
            run_id       TEXT PRIMARY KEY,
            started      TEXT,
            finished     TEXT,
            market       TEXT,
            counts_json  TEXT,
            config_hash  TEXT
        );
        """
    )


def _migration_2(conn: sqlite3.Connection) -> None:
    """Schema v2 — market-cap enrich columns so the cap floor becomes queryable (not just an audit row).

    ``below_floor`` is the active-universe gate: a known cap below the configured floor sets it to 1
    (the entity stays ``is_live`` — flag, not delete). Unknown cap leaves it 0 (missing ≠ small).
    """
    conn.executescript(
        """
        ALTER TABLE entity ADD COLUMN mktcap_ccy  TEXT;
        ALTER TABLE entity ADD COLUMN below_floor  INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE entity ADD COLUMN ipo_date     TEXT;
        ALTER TABLE entity ADD COLUMN enriched_at  TEXT;
        CREATE INDEX ix_entity_below_floor ON entity(below_floor);
        """
    )


def _migration_3(conn: sqlite3.Connection) -> None:
    """Schema v3 — founder-lineage (spec §5.1 Claude extraction). The `founder` table is the per-person
    join surface the literature signal (§3.1) queries by name; entity carries extraction bookkeeping so
    a prompt-version bump re-opens everyone (identity/prompt-version skip-cache)."""
    conn.executescript(
        """
        CREATE TABLE founder (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id          TEXT NOT NULL,
            name               TEXT NOT NULL,
            role               TEXT,
            institution        TEXT,
            is_company_officer INTEGER NOT NULL DEFAULT 0,
            source             TEXT,               -- e.g. "claude:web_search"
            extracted_at       TEXT,
            UNIQUE (entity_id, name)
        );
        CREATE INDEX ix_founder_entity ON founder(entity_id);
        CREATE INDEX ix_founder_name ON founder(name);

        ALTER TABLE entity ADD COLUMN academic_affiliations   TEXT;   -- JSON array
        ALTER TABLE entity ADD COLUMN founder_extracted_at    TEXT;
        ALTER TABLE entity ADD COLUMN founder_prompt_version  TEXT;
        """
    )


def _migration_4(conn: sqlite3.Connection) -> None:
    """Schema v4 — literature/citation resolution (spec §3.1). Each founder resolves to an OpenAlex
    author + a foundational paper whose independent citations (§5.2) are the module's thesis signal."""
    conn.executescript(
        """
        ALTER TABLE founder ADD COLUMN openalex_author_id  TEXT;
        ALTER TABLE founder ADD COLUMN foundational_work_id TEXT;
        ALTER TABLE founder ADD COLUMN literature_at        TEXT;
        """
    )


def _migration_7(conn: sqlite3.Connection) -> None:
    """Schema v7 — §5.2 independence refinement. ``independence_score`` = recency-decayed count of
    genuinely independent-lab citations (after excluding self / co-author / same-institution / industry)."""
    conn.executescript(
        "ALTER TABLE founder ADD COLUMN independence_score REAL;"
        "ALTER TABLE founder ADD COLUMN independence_at TEXT;")


def _migration_6(conn: sqlite3.Connection) -> None:
    """Schema v6 — upper market-cap ceiling. The thesis is small/micro-cap, but Phase 1 had only a
    floor, so mega-caps (Pfizer/Lilly-sized) sat in the active universe. ``above_ceiling`` flags them
    (recall-safe: flag, not delete). Active universe = below_floor=0 AND above_ceiling=0."""
    conn.executescript("ALTER TABLE entity ADD COLUMN above_ceiling INTEGER NOT NULL DEFAULT 0;")


def _migration_5(conn: sqlite3.Connection) -> None:
    """Schema v5 — the stack-convergence scoring layer (spec §5.4, decision D3). One score row per
    (entity, prompt_version): the Claude conviction call that fuses literature + ownership +
    capital-markets evidence into a routable flag. Skip-cache on prompt_version."""
    conn.executescript(
        """
        CREATE TABLE score (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id        TEXT NOT NULL,
            run_id           TEXT,
            model            TEXT,
            conviction_flag  TEXT,               -- surveil | deep-dive-candidate | deprioritize
            conviction_score REAL,               -- 0-100, for ranking the digest
            json             TEXT,               -- full §5.4 structured output
            prompt_version   TEXT,
            scored_at        TEXT,
            UNIQUE (entity_id, prompt_version)
        );
        CREATE INDEX ix_score_entity ON score(entity_id);
        CREATE INDEX ix_score_flag ON score(conviction_flag);
        """
    )


# Ordered list of migrations; index+1 == target user_version after applying.
_MIGRATIONS = [_migration_1, _migration_2, _migration_3, _migration_4, _migration_5, _migration_6,
               _migration_7]


class Store:
    """DAO over ``data/early_detection.db``. Opens WAL, applies pending migrations on init."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # ── lifecycle ────────────────────────────────────────────────────────────
    def _migrate(self) -> None:
        cur_version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for i in range(cur_version, len(_MIGRATIONS)):
            log.info("applying migration %d → user_version %d", i + 1, i + 1)
            _MIGRATIONS[i](self.conn)
            self.conn.execute(f"PRAGMA user_version = {i + 1}")
            self.conn.commit()

    @property
    def user_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── entity ───────────────────────────────────────────────────────────────
    def upsert_entity(self, e: Entity) -> None:
        """Insert or update an entity by ``entity_id``. Preserves ``first_seen`` on update; refreshes
        ``last_seen``. In-place union of ``source_provenance`` so provider provenance accumulates."""
        existing = self.get_entity(e.entity_id)
        first_seen = existing.first_seen if existing and existing.first_seen else (e.first_seen or now_iso())
        last_seen = e.last_seen or now_iso()

        prov = list(dict.fromkeys((existing.source_provenance if existing else []) + list(e.source_provenance)))
        # Once flagged in the existing universe, stay flagged (M6 seed is authoritative for the tier).
        in_universe = int(e.in_existing_universe or (existing.in_existing_universe if existing else False))

        self.conn.execute(
            """
            INSERT INTO entity (entity_id, legal_name, common_name, ticker_primary, exchange_primary,
                isin, lei, cik, jurisdiction, filer_type, sector_code_raw, sector_code_normalized,
                market_cap_usd, mktcap_ccy, mktcap_unknown, below_floor, ipo_date, in_existing_universe,
                is_live, source_provenance, first_seen, last_seen, enriched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_id) DO UPDATE SET
                legal_name=excluded.legal_name,
                common_name=COALESCE(excluded.common_name, entity.common_name),
                ticker_primary=COALESCE(excluded.ticker_primary, entity.ticker_primary),
                exchange_primary=COALESCE(excluded.exchange_primary, entity.exchange_primary),
                isin=COALESCE(excluded.isin, entity.isin),
                lei=COALESCE(excluded.lei, entity.lei),
                cik=COALESCE(excluded.cik, entity.cik),
                jurisdiction=COALESCE(excluded.jurisdiction, entity.jurisdiction),
                filer_type=COALESCE(excluded.filer_type, entity.filer_type),
                sector_code_raw=COALESCE(excluded.sector_code_raw, entity.sector_code_raw),
                sector_code_normalized=COALESCE(excluded.sector_code_normalized, entity.sector_code_normalized),
                market_cap_usd=COALESCE(excluded.market_cap_usd, entity.market_cap_usd),
                mktcap_ccy=COALESCE(excluded.mktcap_ccy, entity.mktcap_ccy),
                mktcap_unknown=excluded.mktcap_unknown,
                below_floor=excluded.below_floor,
                ipo_date=COALESCE(excluded.ipo_date, entity.ipo_date),
                in_existing_universe=excluded.in_existing_universe,
                is_live=excluded.is_live,
                source_provenance=excluded.source_provenance,
                last_seen=excluded.last_seen,
                enriched_at=COALESCE(excluded.enriched_at, entity.enriched_at)
            """,
            (
                e.entity_id, e.legal_name, e.common_name, e.ticker_primary, e.exchange_primary,
                e.isin, e.lei, e.cik, e.jurisdiction, e.filer_type, e.sector_code_raw,
                e.sector_code_normalized, e.market_cap_usd, e.mktcap_ccy, int(e.mktcap_unknown),
                int(e.below_floor), e.ipo_date, in_universe, int(e.is_live), _dumps(prov),
                first_seen, last_seen, e.enriched_at,
            ),
        )
        self.conn.commit()

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        row = self.conn.execute("SELECT * FROM entity WHERE entity_id=?", (entity_id,)).fetchone()
        return self._row_to_entity(row) if row else None

    def find_entity_by_key(self, *, lei: str | None = None, isin: str | None = None,
                           cik: str | None = None, ticker: str | None = None,
                           exchange: str | None = None) -> Optional[Entity]:
        """Look up an existing entity by a hard key, in cascade order (§4). Used by reconciliation."""
        if lei:
            row = self.conn.execute("SELECT * FROM entity WHERE lei=?", (lei,)).fetchone()
            if row:
                return self._row_to_entity(row)
        if isin:
            row = self.conn.execute("SELECT * FROM entity WHERE isin=?", (isin,)).fetchone()
            if row:
                return self._row_to_entity(row)
        cik_norm = _norm_cik(cik)
        if cik_norm:
            row = self.conn.execute("SELECT * FROM entity WHERE cik=?", (cik_norm,)).fetchone()
            if row:
                return self._row_to_entity(row)
        if ticker and exchange:
            row = self.conn.execute(
                "SELECT * FROM entity WHERE ticker_primary=? AND exchange_primary=?", (ticker, exchange)
            ).fetchone()
            if row:
                return self._row_to_entity(row)
        return None

    def all_entities(self, live_only: bool = True) -> list[Entity]:
        sql = "SELECT * FROM entity"
        if live_only:
            sql += " WHERE is_live=1"
        return [self._row_to_entity(r) for r in self.conn.execute(sql)]

    def count_entities(self, live_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM entity" + (" WHERE is_live=1" if live_only else "")
        return self.conn.execute(sql).fetchone()[0]

    def entities_by_ticker(self, ticker: str | None) -> list[Entity]:
        """All entities whose primary ticker matches ``ticker`` case-insensitively. Used by the §9
        validation harness to resolve a labeled case to a stored entity (ticker is the common join key)."""
        if not ticker:
            return []
        rows = self.conn.execute(
            "SELECT * FROM entity WHERE UPPER(ticker_primary)=UPPER(?)", (ticker.strip(),))
        return [self._row_to_entity(r) for r in rows]

    def entities_needing_cap(self, limit: int | None = None) -> list[Entity]:
        """Live entities that have a ticker but no known USD market cap — the enrich work-list."""
        sql = ("SELECT * FROM entity WHERE is_live=1 AND ticker_primary IS NOT NULL "
               "AND (market_cap_usd IS NULL OR mktcap_unknown=1) ORDER BY entity_id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._row_to_entity(r) for r in self.conn.execute(sql)]

    def entities_needing_cap_isin(self, limit: int | None = None) -> list[Entity]:
        """Live entities with an ISIN but no cap and not yet cap-enriched — the FOREIGN (non-EDGAR)
        enrich work-list. These are the Wikidata-seeded Nordic/EU/CA names: they carry an ISIN (not a
        yfinance-ready ticker), so the cap path is ISIN → OpenFIGI symbol → yfinance (vs the ticker path
        of ``entities_needing_cap``). ``enriched_at IS NULL`` so a resolved miss isn't retried every run."""
        sql = ("SELECT * FROM entity WHERE is_live=1 AND isin IS NOT NULL "
               "AND market_cap_usd IS NULL AND enriched_at IS NULL ORDER BY entity_id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._row_to_entity(r) for r in self.conn.execute(sql)]

    def apply_cap(self, entity_id: str, *, market_cap_usd: Optional[float], currency: Optional[str],
                  floor_usd: float, ceiling_usd: Optional[float] = None, ipo_date: Optional[str] = None,
                  enriched_at: Optional[str] = None) -> Optional[bool]:
        """Persist an enrich result for one entity. Returns ``below_floor`` (None if cap still unknown).

        A known cap sets ``market_cap_usd`` + clears ``mktcap_unknown`` + computes ``below_floor`` and
        (if ``ceiling_usd`` given) ``above_ceiling``. A miss (cap None) only stamps ``enriched_at`` and
        leaves the entity KEPT + ``mktcap_unknown`` (missing data is never a delete)."""
        ts = enriched_at or now_iso()
        if market_cap_usd is None:
            self.conn.execute("UPDATE entity SET enriched_at=?, ipo_date=COALESCE(?, ipo_date) "
                              "WHERE entity_id=?", (ts, ipo_date, entity_id))
            self.conn.commit()
            return None
        below = market_cap_usd < floor_usd
        above = bool(ceiling_usd) and market_cap_usd > ceiling_usd
        self.conn.execute(
            "UPDATE entity SET market_cap_usd=?, mktcap_ccy=?, mktcap_unknown=0, below_floor=?, "
            "above_ceiling=?, ipo_date=COALESCE(?, ipo_date), enriched_at=? WHERE entity_id=?",
            (market_cap_usd, currency, int(below), int(above), ipo_date, ts, entity_id),
        )
        self.conn.commit()
        return below

    def entities_needing_lei(self, limit: int | None = None) -> list[Entity]:
        """Live entities with no LEI, highest-value first (M6 tier, then non-US, then the rest).

        The M6 priority tier and international names benefit most from an LEI (their cross-market
        literature/patent joins in Phase 2 key on it), so they lead the work-list."""
        sql = ("SELECT * FROM entity WHERE is_live=1 AND (lei IS NULL OR lei='') "
               "ORDER BY in_existing_universe DESC, (jurisdiction='US') ASC, entity_id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._row_to_entity(r) for r in self.conn.execute(sql)]

    def set_lei(self, entity_id: str, lei: str) -> None:
        """Backfill an LEI only if the entity currently has none (never overwrite a known LEI)."""
        self.conn.execute(
            "UPDATE entity SET lei=? WHERE entity_id=? AND (lei IS NULL OR lei='')", (lei, entity_id))
        self.conn.commit()

    def recompute_floors(self, floor_usd: float, ceiling_usd: Optional[float] = None) -> int:
        """Set ``below_floor`` (and ``above_ceiling`` if ``ceiling_usd`` given) for every known-cap
        entity, after a band-config change. Returns the count now OUT of band. Unknown-cap rows → 0."""
        self.conn.execute(
            "UPDATE entity SET below_floor = CASE WHEN market_cap_usd IS NOT NULL "
            "AND market_cap_usd < ? THEN 1 ELSE 0 END",
            (floor_usd,),
        )
        if ceiling_usd:
            self.conn.execute(
                "UPDATE entity SET above_ceiling = CASE WHEN market_cap_usd IS NOT NULL "
                "AND market_cap_usd > ? THEN 1 ELSE 0 END", (ceiling_usd,))
        self.conn.commit()
        return self.conn.execute(
            "SELECT COUNT(*) FROM entity WHERE below_floor=1 OR above_ceiling=1").fetchone()[0]

    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> Entity:
        return Entity(
            entity_id=row["entity_id"], legal_name=row["legal_name"], common_name=row["common_name"],
            ticker_primary=row["ticker_primary"], exchange_primary=row["exchange_primary"],
            isin=row["isin"], lei=row["lei"], cik=row["cik"], jurisdiction=row["jurisdiction"],
            filer_type=row["filer_type"], sector_code_raw=row["sector_code_raw"],
            sector_code_normalized=row["sector_code_normalized"], market_cap_usd=row["market_cap_usd"],
            mktcap_ccy=row["mktcap_ccy"], mktcap_unknown=bool(row["mktcap_unknown"]),
            below_floor=bool(row["below_floor"]), above_ceiling=bool(row["above_ceiling"]),
            ipo_date=row["ipo_date"],
            in_existing_universe=bool(row["in_existing_universe"]), is_live=bool(row["is_live"]),
            source_provenance=_loads(row["source_provenance"], []),
            first_seen=row["first_seen"], last_seen=row["last_seen"], enriched_at=row["enriched_at"],
            academic_affiliations=_loads(row["academic_affiliations"], []),
            founder_extracted_at=row["founder_extracted_at"],
            founder_prompt_version=row["founder_prompt_version"],
        )

    # ── listing ──────────────────────────────────────────────────────────────
    def add_listing(self, entity_id: str, *, ticker: str | None, exchange: str | None,
                    country: str | None = None, isin: str | None = None, mic: str | None = None,
                    is_primary: bool = False, provenance: list[str] | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO listing (entity_id, ticker, exchange, country, isin, mic, is_primary, provenance)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_id, ticker, exchange) DO UPDATE SET
                country=COALESCE(excluded.country, listing.country),
                isin=COALESCE(excluded.isin, listing.isin),
                mic=COALESCE(excluded.mic, listing.mic),
                is_primary=excluded.is_primary,
                provenance=excluded.provenance
            """,
            (entity_id, ticker, exchange, country, isin, mic, int(is_primary),
             _dumps(provenance or [])),
        )
        self.conn.commit()

    def listings_for(self, entity_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM listing WHERE entity_id=?", (entity_id,)).fetchall()
        return [dict(r) for r in rows]

    # ── reconciliation queue ───────────────────────────────────────────────────
    def queue_recon(self, row: ReconRow) -> int:
        cur = self.conn.execute(
            "INSERT INTO reconciliation_queue (candidate_json, reason, added_at, resolved) VALUES (?,?,?,?)",
            (_dumps(row.candidate), row.reason, row.added_at or now_iso(), int(row.resolved)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def recon_queue(self, unresolved_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM reconciliation_queue"
        if unresolved_only:
            sql += " WHERE resolved=0"
        return [
            {**dict(r), "candidate": _loads(r["candidate_json"], None)}
            for r in self.conn.execute(sql)
        ]

    def count_recon(self, unresolved_only: bool = True) -> int:
        sql = "SELECT COUNT(*) FROM reconciliation_queue" + (" WHERE resolved=0" if unresolved_only else "")
        return self.conn.execute(sql).fetchone()[0]

    # ── signal (Phase 2) ───────────────────────────────────────────────────────
    def insert_signal(self, s: SignalRecord, *, commit: bool = True) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO signal (signal_id, entity_id, signal_type, source,
                raw_payload_json, detected_at, event_date, language)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (s.signal_id, s.entity_id, s.signal_type, s.source, _dumps(s.raw_payload),
             s.detected_at, s.event_date, s.language),
        )
        if commit:
            self.conn.commit()

    def entities_for_signals(self, limit: int | None = None) -> list[Entity]:
        """Active-universe entities eligible for signal ingestion: live, above floor, with a CIK.

        Includes unknown-cap names (missing ≠ small) — a fresh 13D on an unpriced micro-cap is exactly
        the early signal — but excludes known-below-floor ones."""
        sql = ("SELECT * FROM entity WHERE is_live=1 AND below_floor=0 AND above_ceiling=0 "
               "AND cik IS NOT NULL ORDER BY in_existing_universe DESC, entity_id")
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [self._row_to_entity(r) for r in self.conn.execute(sql)]

    def active_entities(self, limit: int | None = None,
                        tickers: "list[str] | None" = None) -> list[Entity]:
        """Active-universe entities ($10M–$3B, live) — NO CIK requirement (name-keyed signals like the
        clinical-trials search work for foreign filers too). ``tickers`` pins named companies to the
        front of the work-list (guaranteed inside ``limit``), like ``entities_for_extraction``."""
        active = "is_live=1 AND below_floor=0 AND above_ceiling=0"
        picked: list[Entity] = []
        seen: set[str] = set()
        for tk in tickers or []:
            for e in self.entities_by_ticker(tk):
                if e.entity_id in seen:
                    continue
                if self.conn.execute(f"SELECT 1 FROM entity WHERE entity_id=? AND {active}",
                                     (e.entity_id,)).fetchone():
                    picked.append(e)
                    seen.add(e.entity_id)
        for r in self.conn.execute(
                f"SELECT * FROM entity WHERE {active} ORDER BY in_existing_universe DESC, entity_id"):
            e = self._row_to_entity(r)
            if e.entity_id in seen:
                continue
            picked.append(e)
            seen.add(e.entity_id)
        return picked[:limit] if limit else picked

    def cik_to_entity_id(self, active_only: bool = True) -> dict[str, str]:
        """{zero-padded-CIK: entity_id} for matching efts filing CIKs back to the universe.

        ``active_only`` restricts to the active universe (live, above floor) — the scope ownership
        signals target."""
        sql = "SELECT cik, entity_id FROM entity WHERE cik IS NOT NULL AND is_live=1"
        if active_only:
            sql += " AND below_floor=0 AND above_ceiling=0"
        return {_norm_cik(r["cik"]): r["entity_id"] for r in self.conn.execute(sql) if r["cik"]}

    def count_signals(self, signal_type: str | None = None) -> int:
        if signal_type:
            return self.conn.execute("SELECT COUNT(*) FROM signal WHERE signal_type=?",
                                     (signal_type,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM signal").fetchone()[0]

    def signals_for(self, entity_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM signal WHERE entity_id=? ORDER BY event_date DESC", (entity_id,)).fetchall()
        return [{**dict(r), "raw_payload": _loads(r["raw_payload_json"], None)} for r in rows]

    # ── founder-lineage extraction (Phase 2, §5.1) ─────────────────────────────
    def entities_for_extraction(self, prompt_version: str, limit: int | None = None,
                                cold_first: bool = False,
                                tickers: "list[str] | None" = None) -> list[Entity]:
        """Active-universe entities not yet founder-extracted at ``prompt_version`` (identity/prompt-
        version skip-cache — a prompt bump re-opens everyone). Default order prioritizes the M6 tier;
        ``cold_first`` flips it to prioritize **cold-discovery** (non-M6) names — the under-recognized
        end where the thesis has the most edge (the M6-tier names are already covered elsewhere).

        ``tickers`` PINS specific names to the front of the work-list (still active-universe + still
        needing extraction), so an operator can say "run on these 4 *plus* fill to N" — the named ones
        are guaranteed to land inside ``limit`` regardless of the default ordering (which would bury a
        non-M6 name like a cold-discovery pick at the tail)."""
        active = "is_live=1 AND below_floor=0 AND above_ceiling=0"
        need = "(founder_prompt_version IS NULL OR founder_prompt_version != ?)"
        picked: list[Entity] = []
        seen: set[str] = set()
        for tk in tickers or []:
            for e in self.entities_by_ticker(tk):
                if e.entity_id in seen:
                    continue
                if self.conn.execute(
                        f"SELECT 1 FROM entity WHERE entity_id=? AND {active} AND {need}",
                        (e.entity_id, prompt_version)).fetchone():
                    picked.append(e)
                    seen.add(e.entity_id)
        # cold_first prioritizes cold-discovery names AND the small-cap end (smallest known cap first)
        # — the actual thesis; ordering by entity_id alone surfaced mega-caps (Pfizer/Lilly).
        order = ("in_existing_universe ASC, (market_cap_usd IS NULL), market_cap_usd ASC"
                 if cold_first else "in_existing_universe DESC")
        sql = f"SELECT * FROM entity WHERE {active} AND {need} ORDER BY {order}, entity_id"
        for r in self.conn.execute(sql, (prompt_version,)):
            e = self._row_to_entity(r)
            if e.entity_id in seen:
                continue
            picked.append(e)
            seen.add(e.entity_id)
        return picked[:limit] if limit else picked

    def save_founders(self, entity_id: str, *, founders: list[dict], affiliations: list[str],
                      prompt_version: str, source: str = "claude:web_search") -> None:
        """Persist an extraction result for one entity (idempotent upsert per founder). Stamps the
        entity's ``founder_prompt_version`` so it isn't re-extracted at this prompt version."""
        ts = now_iso()
        for f in founders or []:
            name = (f.get("name") or "").strip()
            if not name:
                continue
            self.conn.execute(
                """INSERT INTO founder (entity_id, name, role, institution, is_company_officer,
                       source, extracted_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(entity_id, name) DO UPDATE SET
                       role=COALESCE(excluded.role, founder.role),
                       institution=COALESCE(excluded.institution, founder.institution),
                       is_company_officer=excluded.is_company_officer,
                       source=excluded.source, extracted_at=excluded.extracted_at""",
                (entity_id, name, f.get("role"), f.get("institution"),
                 int(bool(f.get("is_company_officer"))), source, ts),
            )
        self.conn.execute(
            "UPDATE entity SET academic_affiliations=?, founder_extracted_at=?, founder_prompt_version=? "
            "WHERE entity_id=?",
            (_dumps(affiliations or []), ts, prompt_version, entity_id),
        )
        self.conn.commit()

    def founders_for(self, entity_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM founder WHERE entity_id=?", (entity_id,)).fetchall()
        return [dict(r) for r in rows]

    def count_founders(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM founder").fetchone()[0]

    # ── literature/citation resolution (Phase 2, §3.1) ─────────────────────────
    def founders_for_literature(self, limit: int | None = None,
                                only_missing: bool = True) -> list[dict[str, Any]]:
        """Founders (with entity + institution context) to run the literature signal for. By default
        only those not yet resolved (``literature_at`` NULL); prioritizes the M6 priority tier."""
        sql = ("SELECT f.*, e.legal_name AS company_name, e.in_existing_universe AS in_universe "
               "FROM founder f JOIN entity e ON e.entity_id=f.entity_id "
               "WHERE e.is_live=1 AND e.below_floor=0 AND e.above_ceiling=0")
        if only_missing:
            sql += " AND f.literature_at IS NULL"
        sql += " ORDER BY e.in_existing_universe DESC, f.id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(sql)]

    def set_founder_literature(self, founder_id: int, *, author_id: Optional[str],
                               foundational_work_id: Optional[str]) -> None:
        self.conn.execute(
            "UPDATE founder SET openalex_author_id=?, foundational_work_id=?, literature_at=? WHERE id=?",
            (author_id, foundational_work_id, now_iso(), founder_id),
        )
        self.conn.commit()

    def founders_for_independence(self, limit: int | None = None,
                                  only_missing: bool = True) -> list[dict[str, Any]]:
        """Founders (with entity + institution context) resolved to an OpenAlex author + foundational
        paper, for the §5.2 co-authorship independence refinement. Default: only those not yet refined."""
        sql = ("SELECT f.*, e.legal_name AS company_name FROM founder f "
               "JOIN entity e ON e.entity_id=f.entity_id "
               "WHERE e.is_live=1 AND e.below_floor=0 AND e.above_ceiling=0 "
               "AND f.openalex_author_id IS NOT NULL AND f.foundational_work_id IS NOT NULL")
        if only_missing:
            sql += " AND f.independence_at IS NULL"
        sql += " ORDER BY e.in_existing_universe DESC, f.id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(sql)]

    def set_founder_independence(self, founder_id: int, score: float) -> None:
        self.conn.execute("UPDATE founder SET independence_score=?, independence_at=? WHERE id=?",
                          (score, now_iso(), founder_id))
        self.conn.commit()

    # ── scoring (Phase 2, §5.4) ────────────────────────────────────────────────
    def scoring_candidates(self, prompt_version: str, *, min_independent: int = 1,
                           require_capital: bool = True, limit: int | None = None,
                           force: bool = False, clinical_min_phase: int = 0) -> list[Entity]:
        """Rules-based pre-filter (§5.5): active-universe entities that show scientific convergence AND
        (if ``require_capital``) at least one ownership/capital-markets signal, and not already scored at
        ``prompt_version`` (unless ``force``). Bounds the expensive scoring call to genuinely-cornered
        candidates.

        Convergence gate = ≥``min_independent`` independent-lab citations. When ``clinical_min_phase`` > 0
        (spec §3.2, opt-in) it is WIDENED to also admit a name with a **company-led** trial at ≥ that
        phase — an independent convergence path that doesn't need the OpenAlex author-resolution trail."""
        indep_clause = (
            "(SELECT COUNT(*) FROM signal s WHERE s.entity_id=e.entity_id "
            "AND s.signal_type='literature' "
            "AND json_extract(s.raw_payload_json,'$.kind')='citation' "
            "AND json_extract(s.raw_payload_json,'$.independence')='independent') >= ?")
        params: list = [min_independent]
        if clinical_min_phase and clinical_min_phase > 0:
            from .clients.clinicaltrials import MEANINGFUL_STATUSES, PHASE_RANK
            # min_phase → minimum phase_rank. 1 = ANY clinical stage (EARLY_PHASE1 rank 1 and up — keeps
            # the early-stage asymmetric names); 2/3/4 raise the floor to that phase. (PHASE1 rank is 2.)
            min_rank = 1 if int(clinical_min_phase) == 1 else PHASE_RANK.get(f"PHASE{int(clinical_min_phase)}", 0)
            # only a company-led trial that is LIVE or COMPLETED counts — a stalled/withdrawn/unknown
            # trial must not admit a name (it's not de-risking evidence).
            status_in = ",".join("?" for _ in MEANINGFUL_STATUSES)
            clinical_clause = (
                "EXISTS (SELECT 1 FROM signal cs WHERE cs.entity_id=e.entity_id "
                "AND cs.signal_type='clinical_trial' "
                "AND json_extract(cs.raw_payload_json,'$.role')='lead' "
                f"AND UPPER(json_extract(cs.raw_payload_json,'$.status')) IN ({status_in}) "
                "AND CAST(json_extract(cs.raw_payload_json,'$.phase_rank') AS INTEGER) >= ?)")
            gate = f"(({indep_clause}) OR {clinical_clause})"
            params.extend(sorted(MEANINGFUL_STATUSES))
            params.append(min_rank)
        else:
            gate = indep_clause
        cap_clause = (
            " AND EXISTS (SELECT 1 FROM signal s2 WHERE s2.entity_id=e.entity_id "
            "AND s2.signal_type IN ('ownership_crossing','capital_markets'))" if require_capital else "")
        scored_clause = "" if force else (
            " AND NOT EXISTS (SELECT 1 FROM score sc WHERE sc.entity_id=e.entity_id "
            "AND sc.prompt_version=?)")
        sql = f"""
            SELECT e.* FROM entity e
            WHERE e.is_live=1 AND e.below_floor=0 AND e.above_ceiling=0
              AND {gate}
              {cap_clause}{scored_clause}
            ORDER BY e.in_existing_universe DESC, e.entity_id
        """
        if not force:
            params.append(prompt_version)
        if limit:
            sql += " LIMIT ?"
            params.append(int(limit))
        return [self._row_to_entity(r) for r in self.conn.execute(sql, params)]

    def evidence_summary(self, entity_id: str) -> dict[str, Any]:
        """Compact evidence packet for the scoring call — aggregates, not raw signal dumps (an entity
        can have hundreds of Form-4s). Literature independence counts, crossing funds, capital forms."""
        c = self.conn
        lit = {r[0]: r[1] for r in c.execute(
            "SELECT json_extract(raw_payload_json,'$.independence') ind, COUNT(*) FROM signal "
            "WHERE entity_id=? AND signal_type='literature' "
            "AND json_extract(raw_payload_json,'$.kind')='citation' GROUP BY ind", (entity_id,))}
        pubs = c.execute(
            "SELECT COUNT(*) FROM signal WHERE entity_id=? AND signal_type='literature' "
            "AND json_extract(raw_payload_json,'$.kind')='publication'", (entity_id,)).fetchone()[0]
        funds = [r[0] for r in c.execute(
            "SELECT DISTINCT json_extract(raw_payload_json,'$.fund') FROM signal "
            "WHERE entity_id=? AND signal_type='ownership_crossing'", (entity_id,)) if r[0]]
        forms = {r[0]: r[1] for r in c.execute(
            "SELECT json_extract(raw_payload_json,'$.form'), COUNT(*) FROM signal "
            "WHERE entity_id=? AND signal_type='capital_markets' GROUP BY 1", (entity_id,))}
        founders = [{k: f[k] for k in ("name", "role", "institution", "is_company_officer",
                                        "openalex_author_id", "foundational_work_id",
                                        "independence_score")}
                    for f in self.founders_for(entity_id)]
        # best (max) recency-decayed independence score across the entity's founders (§5.2), if refined
        scores = [f["independence_score"] for f in founders if f.get("independence_score") is not None]
        # §3.2 clinical-trials aggregate. A raw count conflates a live program with a stalled one, so we
        # bucket by status: "highest phase" is computed over MEANINGFUL (active + completed) trials only —
        # a WITHDRAWN/TERMINATED/UNKNOWN trial must not inflate the phase or read as de-risking evidence.
        trials = list(c.execute(
            "SELECT json_extract(raw_payload_json,'$.phase_rank'), json_extract(raw_payload_json,'$.role'),"
            " json_extract(raw_payload_json,'$.status') FROM signal "
            "WHERE entity_id=? AND signal_type='clinical_trial'", (entity_id,)))
        from .clients.clinicaltrials import (COMPLETED_STATUSES, MEANINGFUL_STATUSES, PHASE_RANK,
                                             STALLED_STATUSES, trial_health)
        _rank_to_phase = {v: k for k, v in PHASE_RANK.items()}

        def _meaningful(row):
            return (row[2] or "").upper() in MEANINGFUL_STATUSES
        mean_all = [r[0] or 0 for r in trials if _meaningful(r)]
        mean_lead = [r[0] or 0 for r in trials if _meaningful(r) and r[1] == "lead"]
        clinical = {
            "trial_count": len(trials),                      # total on file (all statuses)
            "as_lead": sum(1 for r in trials if r[1] == "lead"),
            "active_trials": sum(1 for r in trials if trial_health(r[2]) == "active"),
            "completed_trials": sum(1 for r in trials if (r[2] or "").upper() in COMPLETED_STATUSES),
            "stalled_trials": sum(1 for r in trials if (r[2] or "").upper() in STALLED_STATUSES),
            # "highest phase" among live/finished trials only (stalled/unknown excluded)
            "highest_phase": _rank_to_phase.get(max(mean_all), None) if mean_all and max(mean_all) else None,
            "highest_phase_as_lead": _rank_to_phase.get(max(mean_lead), None) if mean_lead and max(mean_lead) else None,
        }
        # §3.4 regulatory designations: distinct types (breakthrough/fast_track/orphan/rmat/…) the
        # company has disclosed — FDA validation of the mechanism, independent of the citation trail.
        des_types = sorted({r[0] for r in c.execute(
            "SELECT DISTINCT json_extract(raw_payload_json,'$.designation') FROM signal "
            "WHERE entity_id=? AND signal_type='regulatory_designation'", (entity_id,)) if r[0]})
        return {
            "founders": founders,
            "clinical_trials": clinical,
            "regulatory_designations": {"types": des_types, "count": len(des_types)},
            "literature": {"independent_citations": lit.get("independent", 0),
                           "collaborator_citations": lit.get("collaborator", 0),
                           "same_institution_citations": lit.get("same_institution", 0),
                           "industry_citations": lit.get("industry", 0),
                           "self_citations": lit.get("self", 0),
                           "recent_publications": pubs,
                           "independence_score": round(max(scores), 1) if scores else None},
            "specialist_fund_crossings": funds,
            "capital_markets_forms": forms,
        }

    def save_score(self, *, entity_id: str, run_id: str, model: str, conviction_flag: Optional[str],
                   conviction_score: Optional[float], data: dict, prompt_version: str) -> None:
        self.conn.execute(
            """INSERT INTO score (entity_id, run_id, model, conviction_flag, conviction_score, json,
                   prompt_version, scored_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(entity_id, prompt_version) DO UPDATE SET
                   run_id=excluded.run_id, model=excluded.model,
                   conviction_flag=excluded.conviction_flag, conviction_score=excluded.conviction_score,
                   json=excluded.json, scored_at=excluded.scored_at""",
            (entity_id, run_id, model, conviction_flag, conviction_score, _dumps(data),
             prompt_version, now_iso()),
        )
        self.conn.commit()

    def top_scores(self, prompt_version: str, limit: int = 50) -> list[dict[str, Any]]:
        """Scored candidates ranked for the digest: deep-dive first, then by conviction_score."""
        rows = self.conn.execute(
            """SELECT s.*, e.legal_name, e.ticker_primary, e.jurisdiction, e.in_existing_universe,
                      e.market_cap_usd
               FROM score s JOIN entity e ON e.entity_id=s.entity_id
               WHERE s.prompt_version=?
               ORDER BY CASE s.conviction_flag WHEN 'deep-dive-candidate' THEN 0
                        WHEN 'surveil' THEN 1 ELSE 2 END, s.conviction_score DESC
               LIMIT ?""", (prompt_version, int(limit)))
        return [{**dict(r), "json": _loads(r["json"], {})} for r in rows]

    def get_score(self, entity_id: str, prompt_version: str) -> Optional[dict[str, Any]]:
        """The stored conviction for one entity at ``prompt_version`` (parsed ``json``), or None if
        unscored. Read-only surface for the §9 validation harness."""
        row = self.conn.execute(
            "SELECT * FROM score WHERE entity_id=? AND prompt_version=?",
            (entity_id, prompt_version)).fetchone()
        return {**dict(row), "json": _loads(row["json"], {})} if row else None

    def count_scores(self, prompt_version: str | None = None) -> int:
        if prompt_version:
            return self.conn.execute("SELECT COUNT(*) FROM score WHERE prompt_version=?",
                                     (prompt_version,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM score").fetchone()[0]

    # ── audit ──────────────────────────────────────────────────────────────────
    def audit(self, entry: AuditEntry) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (ts, run_id, entity_id, stage, action, reason, detail_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (entry.ts or now_iso(), entry.run_id, entry.entity_id, entry.stage, entry.action,
             entry.reason, _dumps(entry.detail)),
        )
        self.conn.commit()

    def log(self, *, stage: str, action: str, run_id: str | None = None,
            entity_id: str | None = None, reason: str | None = None, detail: Any = None) -> None:
        """Convenience audit writer."""
        self.audit(AuditEntry(ts=now_iso(), stage=stage, action=action, run_id=run_id,
                              entity_id=entity_id, reason=reason, detail=detail))

    # ── run_meta ───────────────────────────────────────────────────────────────
    def record_run(self, *, run_id: str, started: str, finished: str, market: str,
                   counts: dict[str, Any], config_hash: str | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO run_meta (run_id, started, finished, market, counts_json, config_hash) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, started, finished, market, _dumps(counts), config_hash),
        )
        self.conn.commit()
