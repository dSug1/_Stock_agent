"""Module 1 ingest — load a BPC FDA-calendar CSV into catalyst_snapshots.

Per spec §3:
- Header validated up-front (strict 19 columns); mismatch raises
  SchemaValidationError BEFORE any DB write.
- Each row goes through pydantic; invalid rows logged with reason and
  skipped, valid rows upserted (INSERT OR REPLACE on composite PK).
- On success the source CSV is copied to _csv_source/archive/.
- A single ingest_log row records the run (success | partial | failed).
"""
from __future__ import annotations

import csv
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .csv_schema import CSV_TO_FIELD, EXPECTED_COLUMNS, CatalystRow

log = logging.getLogger(__name__)


class SchemaValidationError(Exception):
    """Raised when CSV header doesn't match the expected column set."""


@dataclass
class IngestStats:
    csv_path: Path
    snapshot_date: _date
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    rows_in: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_rejected: int = 0
    status: str = "running"  # 'running'|'success'|'partial'|'failed'|'dry_run'
    error_message: str | None = None
    rejections: list[tuple[int, str, str]] = field(default_factory=list)


def _validate_header(header: list[str], csv_path: Path) -> None:
    actual = set(header)
    missing = EXPECTED_COLUMNS - actual
    extra = actual - EXPECTED_COLUMNS
    if missing or extra:
        parts: list[str] = []
        if missing:
            parts.append(f"missing columns: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected columns: {sorted(extra)}")
        raise SchemaValidationError(
            f"{csv_path.name}: header mismatch — " + "; ".join(parts)
        )


def _row_to_kwargs(raw: dict[str, str]) -> dict[str, str]:
    return {CSV_TO_FIELD[col]: val for col, val in raw.items()}


def _archive_csv(csv_path: Path, snapshot_date: _date, project_root: Path) -> Path:
    dest_dir = project_root / "_csv_source" / "archive"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{snapshot_date.isoformat()}_{csv_path.name}"
    shutil.copy2(csv_path, dest)
    return dest


def _write_ingest_log(conn: sqlite3.Connection, stats: IngestStats) -> None:
    conn.execute(
        """
        INSERT INTO ingest_log
            (module, started_at, finished_at, status, input_ref,
             rows_in, rows_inserted, rows_updated, rows_rejected, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "catalysts",
            stats.started_at.isoformat(timespec="seconds"),
            (stats.finished_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            stats.status,
            stats.csv_path.name,
            stats.rows_in,
            stats.rows_inserted,
            stats.rows_updated,
            stats.rows_rejected,
            stats.error_message,
        ),
    )


_INSERT_SQL = """
INSERT OR REPLACE INTO catalyst_snapshots
    (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
     name, price, price_history_30d, indication, stage, status,
     catalyst_date, catalyst_text, conference, historical_loa,
     historical_pop, sentiment, market_cap_usd, no_of_shares,
     bpc_last_updated)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _row_tuple(row: CatalystRow, snapshot_date: _date) -> tuple:
    return (
        snapshot_date.isoformat(),
        row.ticker, row.drug, row.nct_number, row.next_catalyst_type,
        row.name, row.price, row.price_history_30d, row.indication,
        row.stage, row.status,
        row.catalyst_date.isoformat() if row.catalyst_date else None,
        row.catalyst_text, row.conference, row.historical_loa,
        row.historical_pop, row.sentiment, row.market_cap_usd,
        row.no_of_shares,
        row.bpc_last_updated.isoformat(timespec="seconds") if row.bpc_last_updated else None,
    )


def ingest_catalyst_csv(
    csv_path: Path,
    snapshot_date: _date,
    conn: sqlite3.Connection,
    *,
    project_root: Path | None = None,
    archive: bool = True,
    dry_run: bool = False,
) -> IngestStats:
    csv_path = Path(csv_path).resolve()
    project_root = project_root or csv_path.parent.parent
    stats = IngestStats(csv_path=csv_path, snapshot_date=snapshot_date)

    # Parse + validate everything in memory first; nothing touches the
    # DB until header has been verified.
    try:
        with open(csv_path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            _validate_header(list(reader.fieldnames or []), csv_path)

            existing_pks: set[tuple[str, str, str, str]] = {
                (r["ticker"], r["drug"], r["nct_number"], r["next_catalyst_type"])
                for r in conn.execute(
                    "SELECT ticker, drug, nct_number, next_catalyst_type "
                    "FROM catalyst_snapshots WHERE snapshot_date = ?",
                    (snapshot_date.isoformat(),),
                ).fetchall()
            }

            valid_rows: list[CatalystRow] = []
            for idx, raw in enumerate(reader, start=2):  # row 1 = header
                stats.rows_in += 1
                try:
                    valid_rows.append(CatalystRow(**_row_to_kwargs(raw)))
                except ValidationError as e:
                    tkr = (raw.get("Ticker") or "").strip().upper() or "?"
                    reason = "; ".join(
                        f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                        for err in e.errors()
                    )
                    log.warning("[row %d %s] rejected — %s", idx, tkr, reason)
                    stats.rejections.append((idx, tkr, reason))
                    stats.rows_rejected += 1

    except SchemaValidationError as e:
        stats.status = "failed"
        stats.error_message = str(e)
        stats.finished_at = datetime.now(timezone.utc)
        try:
            _write_ingest_log(conn, stats)
            conn.commit()
        except Exception:
            log.exception("failed to write ingest_log row for header-validation failure")
        raise

    if dry_run:
        stats.status = "dry_run"
        stats.finished_at = datetime.now(timezone.utc)
        return stats

    try:
        with conn:  # transactional: commits on success, rolls back on exception
            for row in valid_rows:
                pk = (row.ticker, row.drug, row.nct_number, row.next_catalyst_type)
                is_update = pk in existing_pks
                conn.execute(_INSERT_SQL, _row_tuple(row, snapshot_date))
                if is_update:
                    stats.rows_updated += 1
                else:
                    stats.rows_inserted += 1
                    existing_pks.add(pk)  # guard against dup PK within the same CSV
        stats.status = "success" if stats.rows_rejected == 0 else "partial"
    except Exception as e:
        stats.status = "failed"
        stats.error_message = str(e)
        raise
    finally:
        stats.finished_at = datetime.now(timezone.utc)
        try:
            _write_ingest_log(conn, stats)
            conn.commit()
        except Exception:
            log.exception("failed to write ingest_log row")

    if archive and stats.status in ("success", "partial"):
        _archive_csv(csv_path, snapshot_date, project_root)

    return stats
