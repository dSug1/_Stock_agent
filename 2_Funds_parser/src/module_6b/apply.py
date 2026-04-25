"""Module 6b Part (a) — apply composite modifiers to a run's llm_scores
rows + final_rankings (D47).

The pipeline always computes the *model* modifier (weights = 1.0) and
persists it. The HTML re-applies user weights live in JS without
touching the DB — so this module's writes are the canonical baseline.

Public API::

    apply_modifiers_to_run(conn, run_id, quarter, *, cfg=None, now=None)
        Walk every llm_scores row for this (run_id, quarter), compute
        the modifier, write back to llm_scores (3 cols) and update
        final_rankings (3 cols). Idempotent — safe to re-run.

    apply_modifiers_for_quarter(conn, quarter, *, cfg=None, now=None)
        Same, but for the LATEST score per (ticker, horizon) across all
        runs in the quarter — used by the merged-view re-render under
        --selection-from-html.

Spec: 2_Funds_parser/spec/module_6b_spec.md.
Decision: 2_Funds_parser/spec/decisions.md § D47.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Iterable

from .modifiers import (
    COMPONENT_NAMES,
    compute_modifier,
    load_modifier_config,
)

_LOG = logging.getLogger("module_6b.apply")


# ---------------------------------------------------------------------------
# Single-row computation
# ---------------------------------------------------------------------------


def compute_for_row(
    research_brief_json: str | None,
    cfg: dict,
    now: datetime | None = None,
) -> dict:
    """Parse a research_brief JSON string and return the modifier payload.

    Empty / unparsable JSON → returns the neutral all-1.0 result so the
    column stays populated with a defensible default.
    """
    rb: dict = {}
    if research_brief_json:
        try:
            rb = json.loads(research_brief_json) or {}
        except Exception as e:
            _LOG.warning("research_brief_json parse failed: %s", e)
            rb = {}
    return compute_modifier(rb, cfg, now=now)


# ---------------------------------------------------------------------------
# Per-run application
# ---------------------------------------------------------------------------


def apply_modifiers_to_run(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    quarter: str,
    cfg: dict | None = None,
    now: datetime | None = None,
) -> dict[str, dict]:
    """Compute + write modifiers for every llm_scores row in (run_id, quarter).

    Updates ``llm_scores`` (3 cols per row) and ``final_rankings``
    (3 cols per ticker; uses the row at ``final_horizon``).

    Returns a per-ticker summary dict ``{ticker: {modifier, raw_product,
    components, final_score_adjusted}}`` for caller logging / tests.
    """
    if cfg is None:
        cfg = load_modifier_config()

    conn.row_factory = sqlite3.Row
    rows = list(conn.execute(
        """SELECT rowid, ticker, horizon, research_brief_json,
                  score_at_current_pct_per_month
           FROM llm_scores WHERE quarter = ? AND run_id = ?""",
        (quarter, run_id),
    ).fetchall())
    if not rows:
        _LOG.info("No llm_scores rows for run_id=%s quarter=%s", run_id, quarter)
        return {}

    # Modifier is per-ticker (not per-horizon) — research_brief is
    # identical across horizons for the same (ticker, run). Compute once
    # per ticker, stamp on every horizon row.
    by_ticker: dict[str, dict] = {}
    for r in rows:
        t = r["ticker"]
        if t not in by_ticker:
            by_ticker[t] = compute_for_row(r["research_brief_json"], cfg, now=now)

    # ── llm_scores updates ──
    for r in rows:
        t = r["ticker"]
        result = by_ticker[t]
        score_curr = r["score_at_current_pct_per_month"]
        adjusted = (
            float(score_curr) * float(result["modifier"])
            if score_curr is not None else None
        )
        conn.execute(
            """UPDATE llm_scores
               SET score_modifier = ?,
                   score_modifier_json = ?,
                   score_at_current_adjusted_pct_per_month = ?
               WHERE rowid = ?""",
            (float(result["modifier"]),
             json.dumps(_serialise_audit(result), default=str),
             adjusted,
             int(r["rowid"])),
        )

    # ── final_rankings updates ──
    fr_rows = list(conn.execute(
        """SELECT ticker, final_horizon, final_score
           FROM final_rankings WHERE quarter = ? AND run_id = ?""",
        (quarter, run_id),
    ).fetchall())
    summary: dict[str, dict] = {}
    for fr in fr_rows:
        t = fr["ticker"]
        result = by_ticker.get(t)
        if not result:
            # Ticker had no llm_scores rows (shouldn't happen, but be safe).
            continue
        modifier = float(result["modifier"])
        final_score = fr["final_score"]
        adjusted_final = (
            float(final_score) * modifier if final_score is not None else None
        )
        conn.execute(
            """UPDATE final_rankings
               SET score_modifier = ?,
                   score_modifier_json = ?,
                   final_score_adjusted = ?
               WHERE quarter = ? AND run_id = ? AND ticker = ?""",
            (modifier,
             json.dumps(_serialise_audit(result), default=str),
             adjusted_final,
             quarter, run_id, t),
        )
        summary[t] = {
            "modifier": modifier,
            "raw_product": result["raw_product"],
            "clipped_to": result["clipped_to"],
            "final_score": final_score,
            "final_score_adjusted": adjusted_final,
            "components": {n: c.get("factor", 1.0)
                           for n, c in result["components"].items()},
        }
    conn.commit()
    return summary


def apply_modifiers_to_runs(
    conn: sqlite3.Connection,
    runs: Iterable[tuple[int, str]],
    *,
    cfg: dict | None = None,
    now: datetime | None = None,
) -> dict[tuple[int, str], dict[str, dict]]:
    """Convenience: apply to many (run_id, quarter) pairs."""
    if cfg is None:
        cfg = load_modifier_config()
    out: dict[tuple[int, str], dict[str, dict]] = {}
    for run_id, quarter in runs:
        out[(run_id, quarter)] = apply_modifiers_to_run(
            conn, run_id=run_id, quarter=quarter, cfg=cfg, now=now,
        )
    return out


# ---------------------------------------------------------------------------
# Renderer-facing helper: per-ticker model factors for the JS recompute
# ---------------------------------------------------------------------------


def collect_ticker_factors(
    conn: sqlite3.Connection,
    *,
    quarter: str,
    tickers: Iterable[str] | None = None,
    cfg: dict | None = None,
    now: datetime | None = None,
) -> dict[str, dict]:
    """For each ticker, return ``{component: model_factor}`` from the LATEST
    llm_scores row in the quarter (any run). Used by the renderer to
    embed a JSON blob the HTML's JS can re-weight live (D50).

    Returns ``{ticker: {"factors": {comp: float}, "components_evidence":
    {comp: dict}, "modifier": float}}``.
    """
    if cfg is None:
        cfg = load_modifier_config()
    conn.row_factory = sqlite3.Row
    query = "SELECT ticker, run_id, research_brief_json FROM llm_scores WHERE quarter = ?"
    params: list = [quarter]
    if tickers:
        tickers = list(tickers)
        placeholders = ",".join("?" for _ in tickers)
        query += f" AND ticker IN ({placeholders})"
        params.extend(tickers)
    query += " ORDER BY ticker, run_id"
    rows = list(conn.execute(query, params).fetchall())
    # Keep the LATEST run per ticker.
    latest_by_ticker: dict[str, sqlite3.Row] = {}
    for r in rows:
        latest_by_ticker[r["ticker"]] = r

    out: dict[str, dict] = {}
    for ticker, r in latest_by_ticker.items():
        result = compute_for_row(r["research_brief_json"], cfg, now=now)
        out[ticker] = {
            "factors": {n: c.get("factor", 1.0)
                        for n, c in result["components"].items()},
            "components_evidence": result["components"],
            "modifier": result["modifier"],
        }
    return out


# ---------------------------------------------------------------------------
# JSON serialisation helper
# ---------------------------------------------------------------------------


def _serialise_audit(result: dict) -> dict:
    """Project the compute_modifier result to the audit-blob JSON shape.

    Stored in ``llm_scores.score_modifier_json`` and
    ``final_rankings.score_modifier_json``. Compact enough to fit in
    SQLite cells; rich enough to render the breakdown table client-side
    without re-running compute.
    """
    return {
        "modifier":          result["modifier"],
        "raw_product":       result["raw_product"],
        "clipped_to":        result["clipped_to"],
        "applied_at":        result["applied_at"],
        "weights_used":      result.get("weights"),
        "components":        result["components"],
        "effective_factors": result.get("effective_factors"),
        "component_order":   list(COMPONENT_NAMES),
    }
