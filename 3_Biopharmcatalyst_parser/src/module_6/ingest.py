"""Module 6 orchestrator.

Joins catalyst_snapshots + catalyst_timing + v_executive_open_market_trades
for the target snapshot_date; ATTACH-es the funds DB (unless skipped);
applies H1-H5 filters; computes the three soft signals; writes one row
per catalyst into catalyst_scores.

Spec §12.7-§12.8; decisions D8 + D9.
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .config import ScoringConfig
from .filters import apply_hard_filters
from .funds_reader import (
    FundsContext,
    FundsDBError,
    attach_funds_db,
    detach_funds_db,
    load_fund_accumulation,
    resolve_funds_db_path,
)
from .scoring import (
    InsiderTrade,
    compute_fund_accumulation,
    compute_insider,
    compute_momentum,
    composite,
)

log = logging.getLogger(__name__)


@dataclass
class ScoreStats:
    snapshot_date: date
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    rows_in: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_rejected: int = 0
    hard_pass_count: int = 0
    bucket_counts: dict[str, int] = field(default_factory=dict)
    fail_reason_counts: dict[str, int] = field(default_factory=dict)
    funds_quarter_latest: str | None = None
    funds_quarter_previous: str | None = None
    funds_stale: bool = False
    funds_skipped: bool = False
    status: str = "running"
    error_message: str | None = None


# D35 — switched from INSERT OR REPLACE to ON CONFLICT DO UPDATE so the
# `rescued` + `rescue_class` columns (managed by scripts/3_8_compute_rescue.py)
# survive M6 re-runs. INSERT OR REPLACE would reset them to defaults
# (0 / NULL), forcing a compute_rescue re-run after every M6 ingest.
_INSERT_SQL = """
INSERT INTO catalyst_scores (
    snapshot_date, ticker, drug, nct_number, next_catalyst_type,
    hard_pass, fail_reasons, timing_bucket,
    insider_gross_weighted_usd, insider_score,
    return_30d_pct, momentum_score,
    fund_quarter_latest, fund_quarter_previous,
    funds_holding_latest, funds_holding_previous,
    fund_accumulation_usd, fund_accumulation_score,
    composite_score, computed_at, rules_version
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
DO UPDATE SET
    hard_pass                  = excluded.hard_pass,
    fail_reasons               = excluded.fail_reasons,
    timing_bucket              = excluded.timing_bucket,
    insider_gross_weighted_usd = excluded.insider_gross_weighted_usd,
    insider_score              = excluded.insider_score,
    return_30d_pct             = excluded.return_30d_pct,
    momentum_score             = excluded.momentum_score,
    fund_quarter_latest        = excluded.fund_quarter_latest,
    fund_quarter_previous      = excluded.fund_quarter_previous,
    funds_holding_latest       = excluded.funds_holding_latest,
    funds_holding_previous     = excluded.funds_holding_previous,
    fund_accumulation_usd      = excluded.fund_accumulation_usd,
    fund_accumulation_score    = excluded.fund_accumulation_score,
    composite_score            = excluded.composite_score,
    computed_at                = excluded.computed_at,
    rules_version              = excluded.rules_version
    -- INTENTIONALLY NOT updated: rescued, rescue_class
"""


def _write_ingest_log(conn: sqlite3.Connection, stats: ScoreStats) -> None:
    conn.execute(
        """
        INSERT INTO ingest_log
            (module, started_at, finished_at, status, input_ref,
             rows_in, rows_inserted, rows_updated, rows_rejected, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "score_catalysts",
            stats.started_at.isoformat(timespec="seconds"),
            (stats.finished_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            stats.status,
            stats.snapshot_date.isoformat(),
            stats.rows_in,
            stats.rows_inserted,
            stats.rows_updated,
            stats.rows_rejected,
            stats.error_message,
        ),
    )


def _fetch_catalysts(conn: sqlite3.Connection, snapshot_date: date) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.snapshot_date, s.ticker, s.drug, s.nct_number, s.next_catalyst_type,
               s.market_cap_usd, s.stage, s.price_history_30d,
               t.precision_tier, t.date_min, t.date_max
        FROM catalyst_snapshots s
        LEFT JOIN catalyst_timing t
          ON  s.snapshot_date      = t.snapshot_date
          AND s.ticker             = t.ticker
          AND s.drug               = t.drug
          AND s.nct_number         = t.nct_number
          AND s.next_catalyst_type = t.next_catalyst_type
        WHERE s.snapshot_date = ?
        """,
        (snapshot_date.isoformat(),),
    ).fetchall()


def _fetch_insider_trades(
    conn: sqlite3.Connection, snapshot_date: date, lookback_days: int,
) -> dict[str, list[InsiderTrade]]:
    """Return {ticker -> [InsiderTrade, ...]} for buys within the
    lookback window, anchored on snapshot_date (audit-replayable).
    """
    cutoff_iso = (snapshot_date - _days(lookback_days)).isoformat()
    rows = conn.execute(
        """
        SELECT ticker, executive_role, gross_usd
        FROM v_executive_open_market_trades
        WHERE buy_sell = 'Buy'
          AND filing_date IS NOT NULL
          AND filing_date >= ?
          AND filing_date <= ?
          AND gross_usd IS NOT NULL
          AND gross_usd > 0
        """,
        (cutoff_iso, snapshot_date.isoformat()),
    ).fetchall()

    by_ticker: dict[str, list[InsiderTrade]] = defaultdict(list)
    for r in rows:
        by_ticker[r["ticker"]].append(
            InsiderTrade(executive_role=r["executive_role"], gross_usd=float(r["gross_usd"]))
        )
    return by_ticker


def _days(n: int) -> "_TimeDelta":
    # tiny shim so we don't import timedelta everywhere
    from datetime import timedelta
    return timedelta(days=n)


_TimeDelta = type(_days(0))


def score_snapshot(
    snapshot_date: date,
    conn: sqlite3.Connection,
    cfg: ScoringConfig,
    *,
    project_root: Path,
    skip_funds: bool = False,
) -> ScoreStats:
    """Score every catalyst at ``snapshot_date`` against ``cfg`` and
    upsert into catalyst_scores. Returns counts + per-bucket breakdown.
    """
    stats = ScoreStats(snapshot_date=snapshot_date, funds_skipped=skip_funds)

    # 1. Fetch the catalyst universe + timing for this snapshot
    rows = _fetch_catalysts(conn, snapshot_date)
    stats.rows_in = len(rows)
    if not rows:
        stats.status = "success"
        stats.finished_at = datetime.now(timezone.utc)
        with conn:
            _write_ingest_log(conn, stats)
        return stats

    # 2. Fetch insider trades (snapshot-anchored)
    insider_by_ticker = _fetch_insider_trades(conn, snapshot_date, cfg.insider.lookback_days)

    # 3. Attach funds DB and load accumulation (unless skipped)
    funds_ctx: FundsContext | None = None
    funds_attached = False
    if not skip_funds:
        funds_path = resolve_funds_db_path(project_root, cfg.funds.db_path_relative_to_repo_root)
        try:
            attach_funds_db(conn, funds_path)
            funds_attached = True
            tickers = sorted({r["ticker"] for r in rows})
            funds_ctx = load_fund_accumulation(
                conn,
                tickers=tickers,
                snapshot_date=snapshot_date,
                stale_warning_days=cfg.funds.stale_warning_days,
            )
            stats.funds_quarter_latest = funds_ctx.quarter_latest
            stats.funds_quarter_previous = funds_ctx.quarter_previous
            stats.funds_stale = funds_ctx.stale
        except FundsDBError as e:
            log.warning("funds DB unavailable (%s); falling back to --skip-funds semantics", e)
            skip_funds = True
            stats.funds_skipped = True
            funds_ctx = None
            if funds_attached:
                detach_funds_db(conn)
                funds_attached = False

    # 4. Pre-load existing PKs so we can split inserted vs updated
    existing_pks: set[tuple[str, str, str, str]] = {
        (r["ticker"], r["drug"], r["nct_number"], r["next_catalyst_type"])
        for r in conn.execute(
            "SELECT ticker, drug, nct_number, next_catalyst_type "
            "FROM catalyst_scores WHERE snapshot_date = ?",
            (snapshot_date.isoformat(),),
        ).fetchall()
    }

    bucket_counts: dict[str, int] = defaultdict(int)
    fail_reason_counts: dict[str, int] = defaultdict(int)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # D34 — load the delisted-ticker allowlist once per run. Empty when
    # the table doesn't exist yet (older biotech.db files); ingest still
    # works but H6 is a no-op.
    delisted_tickers: frozenset[str] = frozenset()
    try:
        delisted_rows = conn.execute(
            "SELECT ticker FROM delisted_tickers"
        ).fetchall()
        delisted_tickers = frozenset(
            (r["ticker"] or "").upper() for r in delisted_rows if r["ticker"]
        )
        if delisted_tickers:
            log.info("M6 H6: %d delisted tickers loaded", len(delisted_tickers))
    except sqlite3.OperationalError:
        pass

    try:
        with conn:
            for r in rows:
                ticker = r["ticker"]
                date_min = date.fromisoformat(r["date_min"]) if r["date_min"] else None
                date_max = date.fromisoformat(r["date_max"]) if r["date_max"] else None
                precision_tier = r["precision_tier"] or "unknown"

                verdict = apply_hard_filters(
                    market_cap_usd=r["market_cap_usd"],
                    precision_tier=precision_tier,
                    date_min=date_min,
                    date_max=date_max,
                    stage=r["stage"],
                    next_catalyst_type=r["next_catalyst_type"],
                    snapshot_date=snapshot_date,
                    cfg=cfg,
                    ticker=ticker,
                    delisted_tickers=delisted_tickers,
                )
                for code in verdict.fail_reasons:
                    fail_reason_counts[code] += 1

                # Score regardless of hard_pass — we want the signals
                # visible in the audit row even when filtered out.
                insider = compute_insider(insider_by_ticker.get(ticker, []), cfg)
                momentum = compute_momentum(r["price_history_30d"], cfg)

                # Funds accumulation
                if funds_ctx is None:
                    fund_q_latest = None
                    fund_q_prev = None
                    funds_holding_latest = None
                    funds_holding_previous = None
                    fund_accum = compute_fund_accumulation(None, cfg)
                else:
                    fund_q_latest = funds_ctx.quarter_latest
                    fund_q_prev = funds_ctx.quarter_previous
                    fund_row = funds_ctx.rows_by_ticker.get(ticker)
                    if fund_row is None:
                        funds_holding_latest = 0
                        funds_holding_previous = 0
                        fund_accum = compute_fund_accumulation(None, cfg)
                    else:
                        funds_holding_latest = fund_row.funds_holding_latest
                        funds_holding_previous = fund_row.funds_holding_previous
                        fund_accum = compute_fund_accumulation(
                            fund_row.fund_accumulation_usd, cfg
                        )

                # D35 — compute composite for ALL rows (not just hard_pass)
                # so the Rescued tab can sort/display by composite. The signal
                # scores themselves (insider/momentum/funds) are already
                # computed above unconditionally, so composite has all its
                # inputs available regardless of hard_pass.
                comp = composite(
                    insider_score=insider.insider_score,
                    momentum_score=momentum.momentum_score,
                    fund_accumulation_score=fund_accum.fund_accumulation_score,
                    cfg=cfg,
                    skip_funds=skip_funds,
                )
                composite_score: float | None = comp.composite_score
                if verdict.hard_pass:
                    stats.hard_pass_count += 1
                    bucket_counts[verdict.timing_bucket or "?"] += 1

                pk = (r["ticker"], r["drug"], r["nct_number"], r["next_catalyst_type"])
                is_update = pk in existing_pks
                conn.execute(
                    _INSERT_SQL,
                    (
                        r["snapshot_date"],
                        r["ticker"], r["drug"], r["nct_number"], r["next_catalyst_type"],
                        1 if verdict.hard_pass else 0,
                        ",".join(verdict.fail_reasons) if verdict.fail_reasons else None,
                        verdict.timing_bucket,
                        insider.insider_gross_weighted_usd if insider.insider_gross_weighted_usd > 0 else None,
                        insider.insider_score,
                        momentum.return_30d_pct,
                        momentum.momentum_score,
                        fund_q_latest,
                        fund_q_prev,
                        funds_holding_latest,
                        funds_holding_previous,
                        fund_accum.fund_accumulation_usd if fund_accum.fund_accumulation_usd > 0 else None,
                        fund_accum.fund_accumulation_score,
                        composite_score,
                        now_iso,
                        cfg.rules_version,
                    ),
                )
                if is_update:
                    stats.rows_updated += 1
                else:
                    stats.rows_inserted += 1
                    existing_pks.add(pk)

        stats.status = "partial" if stats.funds_stale else "success"
    except Exception as e:
        stats.status = "failed"
        stats.error_message = str(e)
        raise
    finally:
        if funds_attached:
            detach_funds_db(conn)
        stats.finished_at = datetime.now(timezone.utc)
        stats.bucket_counts = dict(bucket_counts)
        stats.fail_reason_counts = dict(fail_reason_counts)
        try:
            with conn:
                _write_ingest_log(conn, stats)
        except Exception:
            log.exception("failed to write ingest_log row for score_catalysts")

    return stats


def latest_snapshot_date(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT MAX(snapshot_date) FROM catalyst_snapshots").fetchone()
    if row is None or row[0] is None:
        return None
    return date.fromisoformat(row[0])


def all_snapshot_dates(conn: sqlite3.Connection) -> list[date]:
    return [
        date.fromisoformat(r[0])
        for r in conn.execute(
            "SELECT DISTINCT snapshot_date FROM catalyst_snapshots ORDER BY snapshot_date"
        ).fetchall()
    ]
