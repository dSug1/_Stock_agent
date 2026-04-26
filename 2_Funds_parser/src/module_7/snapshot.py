"""Module 7 — snapshot_run.

Reads from `llm_scores.db` for a given run_id and emits one `predictions`
row per (ticker, horizon) actually scored.

Idempotent: re-running the same run_id replaces the prior snapshots for
that run.

Scoring date is taken from `llm_runs.started_at` (one date per run, day
precision). The `score_modifier_json` is snapshotted verbatim so future
M7-γ regression has the audit trail of what factors fired with what
values AT THE TIME of the prediction (essential because YAML edits +
6b_apply_modifiers.py reruns can retroactively rewrite the live
`llm_scores.score_modifier_json`).
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from pathlib import Path
from typing import Optional

from .outcomes_db import (
    db_connect as _outcomes_db_connect,
    delete_snapshots_for_run,
    init_outcomes_db,
)

log = logging.getLogger(__name__)


def _iso_date_only(s: Optional[str]) -> Optional[str]:
    """Strip time component from an ISO datetime; returns None on falsy."""
    if not s:
        return None
    return str(s).split("T")[0].split(" ")[0]


def _now_iso() -> str:
    return dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


_PREDICTIONS_COLS = (
    "ticker", "scoring_date", "quarter", "horizon", "run_id",
    "prompt_version", "model",
    "current_price_usd", "target_price_usd", "time_to_catalyst_weeks",
    "probability", "catalyst_type", "catalyst_detail",
    "score_at_current_pct_per_month", "score_modifier",
    "score_at_current_adjusted_pct_per_month", "score_modifier_json",
    "archetype", "sector", "industry", "fund_count", "market_cap_usd",
    "snapshot_at",
)


def snapshot_run(
    scores_conn: sqlite3.Connection,
    *,
    run_id: int,
    outcomes_db_path: Path,
    packs_db_path: Optional[Path] = None,
) -> int:
    """Write one predictions row per (ticker, horizon) for this run_id.

    Idempotent — replaces any prior snapshots for this run_id.
    Returns the number of rows written.

    `scores_conn` must be a live connection to `llm_scores.db`.
    `packs_db_path`, when supplied, is used to look up sector / industry
    per ticker (read from `context_packs.json_extract`). Optional —
    the columns are nullable.
    """
    init_outcomes_db(outcomes_db_path)

    # Run metadata
    run_row = scores_conn.execute(
        "SELECT run_id, quarter, prompt_version, model, started_at "
        "FROM llm_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if run_row is None:
        log.warning("snapshot_run: run_id=%s not found in llm_runs", run_id)
        return 0
    run_keys = ("run_id", "quarter", "prompt_version", "model", "started_at")
    run = dict(zip(run_keys, run_row))
    scoring_date = _iso_date_only(run.get("started_at")) or _iso_date_only(_now_iso())
    quarter = run.get("quarter")
    prompt_version = run.get("prompt_version")
    model = run.get("model")

    # Per-(ticker, horizon) score rows for this run.
    # Note: we read the LIVE score_modifier_json from llm_scores. If a
    # subsequent 6b_apply_modifiers.py run mutates it, this snapshot becomes
    # the prior audit record (and a re-snapshot would replace it).
    score_rows = scores_conn.execute(
        """
        SELECT ticker, horizon,
               current_price_at_scoring_usd,
               target_price_usd, time_to_catalyst_weeks, probability,
               catalyst_type, catalyst_detail,
               score_at_current_pct_per_month,
               score_modifier,
               score_at_current_adjusted_pct_per_month,
               score_modifier_json
        FROM llm_scores
        WHERE run_id = ?
          AND current_price_at_scoring_usd IS NOT NULL
        """,
        (run_id,),
    ).fetchall()

    if not score_rows:
        log.info("snapshot_run: no scoreable rows for run_id=%s", run_id)
        return 0

    # Per-ticker pack-derived columns (archetype, market_cap, fund_count).
    tickers = sorted({r[0] for r in score_rows})
    placeholders = ",".join("?" * len(tickers))
    fr_rows = scores_conn.execute(
        f"""
        SELECT ticker, archetype, market_cap_usd, fund_count, industry
        FROM final_rankings
        WHERE quarter = ? AND ticker IN ({placeholders})
        ORDER BY ticker, run_id DESC
        """,
        [quarter] + tickers,
    ).fetchall()
    fr_by_ticker: dict[str, dict] = {}
    fr_keys = ("ticker", "archetype", "market_cap_usd", "fund_count", "industry")
    for row in fr_rows:
        d = dict(zip(fr_keys, row))
        fr_by_ticker.setdefault(d["ticker"], d)

    # Per-ticker sector from context_packs (when path provided).
    sector_by_ticker: dict[str, Optional[str]] = {}
    if packs_db_path is not None and packs_db_path.exists():
        try:
            with sqlite3.connect(packs_db_path) as pconn:
                pp = pconn.execute(
                    f"SELECT ticker, sector FROM context_packs "
                    f"WHERE quarter = ? AND ticker IN ({placeholders})",
                    [quarter] + tickers,
                ).fetchall()
                for ticker, sector in pp:
                    sector_by_ticker[ticker] = sector
        except sqlite3.Error as e:
            log.warning("snapshot_run: could not read sectors from %s: %s",
                        packs_db_path, e)

    score_keys = (
        "ticker", "horizon",
        "current_price_at_scoring_usd",
        "target_price_usd", "time_to_catalyst_weeks", "probability",
        "catalyst_type", "catalyst_detail",
        "score_at_current_pct_per_month",
        "score_modifier",
        "score_at_current_adjusted_pct_per_month",
        "score_modifier_json",
    )

    snapshot_at = _now_iso()
    payloads: list[dict] = []
    for row in score_rows:
        d = dict(zip(score_keys, row))
        ticker = d["ticker"]
        meta = fr_by_ticker.get(ticker, {})
        payloads.append({
            "ticker": ticker,
            "scoring_date": scoring_date,
            "quarter": quarter,
            "horizon": d["horizon"],
            "run_id": run_id,
            "prompt_version": prompt_version,
            "model": model,
            "current_price_usd": d.get("current_price_at_scoring_usd"),
            "target_price_usd": d.get("target_price_usd"),
            "time_to_catalyst_weeks": d.get("time_to_catalyst_weeks"),
            "probability": d.get("probability"),
            "catalyst_type": d.get("catalyst_type"),
            "catalyst_detail": d.get("catalyst_detail"),
            "score_at_current_pct_per_month": d.get("score_at_current_pct_per_month"),
            "score_modifier": d.get("score_modifier"),
            "score_at_current_adjusted_pct_per_month":
                d.get("score_at_current_adjusted_pct_per_month"),
            "score_modifier_json": d.get("score_modifier_json"),
            "archetype": meta.get("archetype"),
            "sector": sector_by_ticker.get(ticker),
            "industry": meta.get("industry"),
            "fund_count": meta.get("fund_count"),
            "market_cap_usd": meta.get("market_cap_usd"),
            "snapshot_at": snapshot_at,
        })

    placeholder_str = ",".join(f":{c}" for c in _PREDICTIONS_COLS)
    with _outcomes_db_connect(outcomes_db_path) as oconn:
        # Idempotent re-snapshot: wipe prior rows for this run_id, then insert.
        delete_snapshots_for_run(oconn, run_id)
        oconn.executemany(
            f"INSERT INTO predictions({','.join(_PREDICTIONS_COLS)}) "
            f"VALUES({placeholder_str})",
            payloads,
        )
        oconn.commit()
    log.info("snapshot_run: wrote %d predictions for run_id=%d (quarter=%s)",
             len(payloads), run_id, quarter)
    return len(payloads)
