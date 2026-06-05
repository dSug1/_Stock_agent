"""D39 — Ticker-coverage gate.

One Claude call per company is enough — that's the user's rule. The
D38 ack-gate enforces this AFTER the user has explicitly ticked the row;
this module's gate enforces it BEFORE, at dispatch time, on the basis
of whether any prior `deep_dives` row exists for the ticker.

Mechanism:
  * `tickers_with_existing_dispatch(db_path)` reads `SELECT DISTINCT
    ticker FROM deep_dives` and returns the upper-cased frozenset. M7
    and M8 dispatches both write to the same table, so any past Claude
    analysis on a ticker — regardless of prompt variant — counts as
    "covered".
  * `apply_coverage_gate(candidates, covered)` mirrors D38's
    `apply_ticker_gate`: drops candidates whose `ticker` is in the
    covered set, returns `(kept, dropped)`. Same defensive behaviour
    (case-insensitive; missing/blank ticker fields are kept).
  * The renderer (`scripts/3_6_render_scores.py`) also calls
    `tickers_with_existing_dispatch` and exposes the covered set to the
    JS as `window.__DATA.covered_tickers`. When a row has NO deep_dive
    of its own but its ticker is in that set, the expectancy/week cell
    renders "already covered" instead of the em-dash so the user knows
    the ticker has been analysed under a different drug.

`--override-coverage-gate` on any of the M7/M8 dispatch + estimator
scripts bypasses the gate when the user genuinely wants to re-analyse
(e.g., BPC published a substantively new catalyst for the company).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


DEFAULT_DEEP_DIVES_DB_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "claude_deep_dives.db"
)


def tickers_with_existing_dispatch(
    db_path: Path | str | None = None,
) -> frozenset[str]:
    """Read distinct tickers from `deep_dives`. Returns empty frozenset
    when the DB is missing, the table doesn't exist, or any error
    occurs — the gate fails OPEN so a startup hiccup never blocks a
    user-driven dispatch."""
    p = Path(db_path) if db_path else DEFAULT_DEEP_DIVES_DB_PATH
    if not p.exists():
        return frozenset()
    try:
        cx = sqlite3.connect(str(p))
    except sqlite3.OperationalError:
        return frozenset()
    try:
        rows = cx.execute("SELECT DISTINCT ticker FROM deep_dives").fetchall()
    except sqlite3.OperationalError:
        return frozenset()
    finally:
        cx.close()
    return frozenset(
        r[0].strip().upper()
        for r in rows
        if isinstance(r[0], str) and r[0].strip()
    )


def apply_coverage_gate(
    candidates: list[dict],
    covered: frozenset[str] | set[str],
    *,
    ticker_key: str = "ticker",
) -> tuple[list[dict], list[dict]]:
    """Drop candidates whose ticker is in the covered set.

    Mirrors `module_7.gate.apply_ticker_gate` deliberately so the two
    gates have identical semantics: case-insensitive match, defensively
    keeps candidates with missing/blank ticker.
    """
    if not covered:
        return list(candidates), []
    cov = frozenset(str(t).strip().upper() for t in covered)
    kept: list[dict] = []
    dropped: list[dict] = []
    for cand in candidates:
        t = cand.get(ticker_key)
        if not isinstance(t, str) or not t.strip():
            kept.append(cand)
            continue
        if t.strip().upper() in cov:
            dropped.append(cand)
        else:
            kept.append(cand)
    return kept, dropped
