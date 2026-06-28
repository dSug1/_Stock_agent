"""SQLite evidence store + DAO (spec §7) — and the code-level home of the cardinal rule (§0.2).

Single durable store at ``data/store.db``. Schema evolves via ordered additive migrations tracked
by ``PRAGMA user_version`` (repo convention, same as 5_Hype_parser ``db.py``). Schema v1 creates the
seven spec tables: companies · evidence · scores · audit_log · review_queue · seed_labels · run_meta.

The guardrail (spec §0.2 / §7 "DAO guardrail"): ``Store.delete_company(reason)`` raises
``IllegalDeletionError`` unless ``reason`` is in the allowed set (``{mktcap_out_of_band, not_live}``,
possibly narrowed by config but never widened). Every other narrowing — missing data, no TA tag,
embedding cut, low score — is a FLAG + an ``audit_log`` row, and is reversible. This is the one
invariant the whole recall-safe design rests on; ``test_store.py`` proves it holds.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import deletion_allowed_reasons
from .models import AuditEntry, Company, Evidence, Score

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class IllegalDeletionError(Exception):
    """Raised when a delete is attempted for a reason outside the cardinal allowed set.

    The existence of this exception is the point: Stage 0 is the only stage that can lose a
    candidate invisibly, so deletion is fenced off to two unambiguous, recall-safe reasons.
    """


def now_iso() -> str:
    """UTC timestamp to the second (repo datetime discipline)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_iso() -> str:
    """UTC calendar date."""
    return datetime.now(timezone.utc).date().isoformat()


# ── Migrations ────────────────────────────────────────────────────────────────

def _migration_1(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE companies (
            company_id      TEXT PRIMARY KEY,   -- stable hash(name|primary_listing)
            name            TEXT NOT NULL,
            primary_ticker  TEXT,
            exchange        TEXT,
            country         TEXT,
            isin            TEXT,
            lei             TEXT,
            mktcap_usd_fd   REAL,
            mktcap_unknown  INTEGER NOT NULL DEFAULT 0,
            source_nets     TEXT,               -- JSON array (which Stage-0a nets hit)
            ta_tags         TEXT,               -- JSON array of taxonomy ids
            dev_stage       TEXT,
            stage1_excluded INTEGER NOT NULL DEFAULT 0,
            is_live         INTEGER NOT NULL DEFAULT 1,
            first_seen      TEXT,
            last_seen       TEXT
        );

        CREATE TABLE evidence (
            company_id   TEXT NOT NULL,
            source       TEXT NOT NULL,         -- openalex|ctgov|edgar|patents|ir
            cursor       TEXT,                  -- latest-seen marker for incremental
            payload_hash TEXT,
            payload_json TEXT,
            fetched_at   TEXT,
            PRIMARY KEY (company_id, source)
        );

        CREATE TABLE scores (
            company_id TEXT NOT NULL,
            run_id     TEXT NOT NULL,
            model      TEXT,                    -- which Claude tier produced this
            json       TEXT,                    -- full rubric JSON (§9.3)
            A REAL, B REAL, C REAL, D REAL, E REAL,
            composite  REAL,
            confidence REAL,
            PRIMARY KEY (company_id, run_id)
        );

        CREATE TABLE audit_log (
            ts          TEXT NOT NULL,
            run_id      TEXT,
            company_id  TEXT,
            stage       TEXT,
            action      TEXT,                   -- flagged|excluded|deleted|cut|scored
            reason      TEXT,
            detail_json TEXT
        );

        CREATE TABLE review_queue (
            company_id TEXT PRIMARY KEY,
            reason     TEXT,
            added_at   TEXT
        );

        CREATE TABLE seed_labels (
            company_id TEXT PRIMARY KEY,
            label      TEXT                      -- positive|negative
        );

        CREATE TABLE run_meta (
            run_id       TEXT PRIMARY KEY,
            started      TEXT,
            finished     TEXT,
            config_hash  TEXT,
            cost_usd     REAL,
            metrics_json TEXT
        );

        CREATE INDEX idx_audit_company ON audit_log(company_id);
        CREATE INDEX idx_audit_stage   ON audit_log(stage, reason);
        CREATE INDEX idx_scores_run    ON scores(run_id);
        CREATE INDEX idx_evidence_co   ON evidence(company_id);
        """
    )


_MIGRATIONS = {1: _migration_1}


def _run_migrations(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current + 1, SCHEMA_VERSION + 1):
        log.info("applying migration -> v%d", version)
        _MIGRATIONS[version](conn)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


def _json_or_none(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, separators=(",", ":"), default=str)


def _loads(value: Optional[str], default: Any) -> Any:
    if value is None or value == "":
        return default
    return json.loads(value)


# ── DAO ───────────────────────────────────────────────────────────────────────

class Store:
    """Data-access object over ``store.db``. Owns the cardinal-rule guardrail.

    ``allowed_delete_reasons`` is derived from config (intersected with the hard-coded cardinal
    set in ``config.deletion_allowed_reasons``), so a misconfigured file can narrow but never widen
    what may be deleted.
    """

    def __init__(self, conn: sqlite3.Connection, config: dict[str, Any] | None = None):
        self.conn = conn
        self.allowed_delete_reasons = deletion_allowed_reasons(config)

    # -- lifecycle --
    @classmethod
    def open(cls, path: str | Path = "data/store.db", config: dict[str, Any] | None = None) -> "Store":
        """Open (creating parent dir + schema if needed) and return a Store."""
        path = Path(path)
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        _run_migrations(conn)
        return cls(conn, config)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- audit (the spine) --
    def audit(self, *, stage: str, action: str, company_id: Optional[str] = None,
              reason: Optional[str] = None, run_id: Optional[str] = None,
              detail: Any = None) -> None:
        """Append an audit_log row. Every flag/exclude/cut/delete/score routes through here."""
        self.conn.execute(
            "INSERT INTO audit_log(ts, run_id, company_id, stage, action, reason, detail_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (now_iso(), run_id, company_id, stage, action, reason, _json_or_none(detail)),
        )
        self.conn.commit()

    # -- companies --
    def upsert_company(self, company: Company, *, run_id: Optional[str] = None) -> None:
        """Insert or update a company. ``first_seen`` is preserved; ``last_seen`` stamped now."""
        existing = self.conn.execute(
            "SELECT first_seen FROM companies WHERE company_id=?", (company.company_id,)
        ).fetchone()
        first_seen = (existing["first_seen"] if existing and existing["first_seen"]
                      else company.first_seen or now_iso())
        self.conn.execute(
            """
            INSERT INTO companies (company_id, name, primary_ticker, exchange, country, isin, lei,
                mktcap_usd_fd, mktcap_unknown, source_nets, ta_tags, dev_stage, stage1_excluded,
                is_live, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(company_id) DO UPDATE SET
                name=excluded.name, primary_ticker=excluded.primary_ticker,
                exchange=excluded.exchange, country=excluded.country, isin=excluded.isin,
                lei=excluded.lei, mktcap_usd_fd=excluded.mktcap_usd_fd,
                mktcap_unknown=excluded.mktcap_unknown, source_nets=excluded.source_nets,
                ta_tags=excluded.ta_tags, dev_stage=excluded.dev_stage,
                stage1_excluded=excluded.stage1_excluded, is_live=excluded.is_live,
                last_seen=excluded.last_seen
            """,
            (company.company_id, company.name, company.primary_ticker, company.exchange,
             company.country, company.isin, company.lei, company.mktcap_usd_fd,
             int(company.mktcap_unknown), _json_or_none(company.source_nets),
             _json_or_none(company.ta_tags), company.dev_stage, int(company.stage1_excluded),
             int(company.is_live), first_seen, company.last_seen or now_iso()),
        )
        self.conn.commit()

    def get_company(self, company_id: str) -> Optional[Company]:
        row = self.conn.execute(
            "SELECT * FROM companies WHERE company_id=?", (company_id,)
        ).fetchone()
        return _row_to_company(row) if row else None

    def all_companies(self) -> list[Company]:
        rows = self.conn.execute("SELECT * FROM companies ORDER BY company_id").fetchall()
        return [_row_to_company(r) for r in rows]

    def count_companies(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    # -- the guardrail: deletion (the ONLY thing that removes a company) --
    def delete_company(self, company_id: str, reason: str, *, stage: str = "stage0b",
                       run_id: Optional[str] = None, detail: Any = None) -> None:
        """Delete a company — ONLY for an allowed reason (cardinal rule §0.2).

        Raises ``IllegalDeletionError`` for any other reason, leaving the row and writing no
        ``deleted`` audit entry. The two allowed reasons (``mktcap_out_of_band``, ``not_live``)
        are the only unambiguous, recall-safe cuts in the entire pipeline.
        """
        if reason not in self.allowed_delete_reasons:
            raise IllegalDeletionError(
                f"refusing to delete {company_id!r}: reason {reason!r} not in "
                f"{sorted(self.allowed_delete_reasons)} (cardinal rule §0.2 — flag, don't delete)"
            )
        self.conn.execute("DELETE FROM companies WHERE company_id=?", (company_id,))
        self.conn.commit()
        self.audit(stage=stage, action="deleted", company_id=company_id, reason=reason,
                   run_id=run_id, detail=detail)

    # -- non-deletion narrowings (flags; all reversible) --
    def flag_company(self, company_id: str, flag: str, *, reason: Optional[str] = None,
                     stage: str = "stage0b", run_id: Optional[str] = None,
                     detail: Any = None) -> None:
        """Set a recognized boolean flag column + write an audit row. Never removes the row.

        Known flags: ``mktcap_unknown``, ``stage1_excluded``, ``is_live`` (the last sets is_live=0
        WITHOUT deleting — liveness loss is recorded as a flag here; deletion is a separate, fenced
        decision via ``delete_company``).
        """
        column = {"mktcap_unknown": "mktcap_unknown",
                  "stage1_excluded": "stage1_excluded",
                  "not_live": "is_live"}.get(flag)
        if column == "is_live":
            self.conn.execute("UPDATE companies SET is_live=0 WHERE company_id=?", (company_id,))
        elif column:
            self.conn.execute(
                f"UPDATE companies SET {column}=1 WHERE company_id=?", (company_id,)
            )
        self.conn.commit()
        self.audit(stage=stage, action="flagged", company_id=company_id,
                   reason=reason or flag, run_id=run_id, detail=detail or {"flag": flag})

    def exclude_stage1(self, company_id: str, reason: str, *, run_id: Optional[str] = None) -> None:
        """Mark a company excluded from the default Stage-2 harvest set — REVERSIBLE.

        The row stays in the store; ``stage1_excluded=1`` only removes it from the *default* harvest
        set. A config flag (``include_excluded``) re-admits it without re-harvesting anything.
        """
        self.conn.execute(
            "UPDATE companies SET stage1_excluded=1 WHERE company_id=?", (company_id,)
        )
        self.conn.commit()
        self.audit(stage="stage1", action="excluded", company_id=company_id, reason=reason,
                   run_id=run_id)

    # -- review queue --
    def add_to_review_queue(self, company_id: str, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO review_queue(company_id, reason, added_at) VALUES (?,?,?) "
            "ON CONFLICT(company_id) DO UPDATE SET reason=excluded.reason, added_at=excluded.added_at",
            (company_id, reason, now_iso()),
        )
        self.conn.commit()
        self.audit(stage="review_queue", action="flagged", company_id=company_id, reason=reason)

    # -- evidence --
    def upsert_evidence(self, ev: Evidence) -> None:
        self.conn.execute(
            "INSERT INTO evidence(company_id, source, cursor, payload_hash, payload_json, fetched_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(company_id, source) DO UPDATE SET cursor=excluded.cursor, "
            "payload_hash=excluded.payload_hash, payload_json=excluded.payload_json, "
            "fetched_at=excluded.fetched_at",
            (ev.company_id, ev.source, ev.cursor, ev.payload_hash,
             _json_or_none(ev.payload), ev.fetched_at or now_iso()),
        )
        self.conn.commit()

    def get_cursor(self, company_id: str, source: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT cursor FROM evidence WHERE company_id=? AND source=?", (company_id, source)
        ).fetchone()
        return row["cursor"] if row else None

    # -- scores --
    def record_score(self, score: Score, *, run_id: Optional[str] = None) -> None:
        self.conn.execute(
            "INSERT INTO scores(company_id, run_id, model, json, A, B, C, D, E, composite, confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(company_id, run_id) DO UPDATE SET model=excluded.model, json=excluded.json, "
            "A=excluded.A, B=excluded.B, C=excluded.C, D=excluded.D, E=excluded.E, "
            "composite=excluded.composite, confidence=excluded.confidence",
            (score.company_id, score.run_id, score.model, _json_or_none(score.json),
             score.A, score.B, score.C, score.D, score.E, score.composite, score.confidence),
        )
        self.conn.commit()
        self.audit(stage="stage4", action="scored", company_id=score.company_id,
                   reason=score.model, run_id=score.run_id or run_id,
                   detail={"composite": score.composite})

    # -- seed labels --
    def set_seed_label(self, company_id: str, label: str) -> None:
        if label not in ("positive", "negative"):
            raise ValueError(f"seed label must be positive|negative, got {label!r}")
        self.conn.execute(
            "INSERT INTO seed_labels(company_id, label) VALUES (?,?) "
            "ON CONFLICT(company_id) DO UPDATE SET label=excluded.label",
            (company_id, label),
        )
        self.conn.commit()

    # -- §11 query helpers --
    def why_excluded(self, company_id: str) -> list[AuditEntry]:
        """All flag/exclude/cut/delete rows for a company, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM audit_log WHERE company_id=? AND action IN "
            "('flagged','excluded','cut','deleted') ORDER BY ts", (company_id,)
        ).fetchall()
        return [_row_to_audit(r) for r in rows]

    def removed_at_stage(self, stage: str, reason: Optional[str] = None) -> list[AuditEntry]:
        """Everything removed/cut at a stage — the 'show me what it removed and why' query (§1.3)."""
        if reason is None:
            rows = self.conn.execute(
                "SELECT * FROM audit_log WHERE stage=? AND action IN ('cut','deleted','excluded') "
                "ORDER BY ts", (stage,)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM audit_log WHERE stage=? AND reason=? AND action IN "
                "('cut','deleted','excluded') ORDER BY ts", (stage, reason)
            ).fetchall()
        return [_row_to_audit(r) for r in rows]

    def review_queue_dump(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT company_id, reason, added_at FROM review_queue ORDER BY added_at"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Row adapters ────────────────────────────────────────────────────────────────

def _row_to_company(row: sqlite3.Row) -> Company:
    return Company(
        company_id=row["company_id"], name=row["name"], primary_ticker=row["primary_ticker"],
        exchange=row["exchange"], country=row["country"], isin=row["isin"], lei=row["lei"],
        mktcap_usd_fd=row["mktcap_usd_fd"], mktcap_unknown=bool(row["mktcap_unknown"]),
        source_nets=_loads(row["source_nets"], []), ta_tags=_loads(row["ta_tags"], []),
        dev_stage=row["dev_stage"], stage1_excluded=bool(row["stage1_excluded"]),
        is_live=bool(row["is_live"]), first_seen=row["first_seen"], last_seen=row["last_seen"],
    )


def _row_to_audit(row: sqlite3.Row) -> AuditEntry:
    return AuditEntry(
        ts=row["ts"], run_id=row["run_id"], company_id=row["company_id"], stage=row["stage"],
        action=row["action"], reason=row["reason"], detail=_loads(row["detail_json"], None),
    )
