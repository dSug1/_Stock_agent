"""Jury-timeline nascency gate — rank discovered themes by how *nascent + accelerating* they are.

Spec §3a/§4 step 4: a discovered theme worth catching is one whose expert-jury recognition is **recent
and accelerating** (specialist slope ↑), not one whose juries fired a decade ago (already mainstream).
We measure that directly from the ``jury_signals.year`` timeline already ingested — zero network, zero
Claude. The slope **reuses the diffusion engine's ``beta_spec``** (OLS of ln(1+N) over a recent window),
so "jury-attention β" is the same instrument as the corpus β_spec, just over annual jury counts.

The rank fuses three independent things: **convergence** (how many independent credible leading juries
agree — `convergence.score_group`), **acceleration** (β over the jury-year timeline), and **recency**
(share of signals in the last few years). Snapshot signals with no year don't distort it (they're
simply absent from the timeline); the YC/Nobel feeds carry real years.
"""

import logging
from datetime import datetime, timezone

from .. import diffusion as D
from . import convergence

log = logging.getLogger(__name__)


def theme_year_counts(conn, theme_id: str, *, leading_only: bool = True) -> dict:
    """{year: n_signals} backing a discovered theme (leading juries only by default). Year-less
    snapshot signals are excluded (they carry no timeline)."""
    pos = "AND s.diffusion_position='leading'" if leading_only else ""
    rows = conn.execute(
        f"SELECT s.year AS year, COUNT(*) AS n FROM theme_convergence tc "
        f"JOIN jury_signals s ON s.signal_id=tc.signal_id "
        f"WHERE tc.theme_id=? AND s.year IS NOT NULL {pos} GROUP BY s.year",
        (theme_id,)).fetchall()
    return {int(r["year"]): r["n"] for r in rows}


def _contiguous_year_series(counts: dict):
    """{year:n} -> (sorted 'YYYY' periods, {'YYYY': n}) with no gaps, so the slope window is real years
    (a missing year is a real 0, not a skipped point) — matches diffusion's contiguous month handling."""
    if not counts:
        return [], {}
    years = range(min(counts), max(counts) + 1)
    periods = [f"{y:04d}" for y in years]
    return periods, {f"{y:04d}": counts.get(y, 0) for y in years}


def nascency_metrics(counts: dict, *, slope_window_years: int, current_year: int,
                     recent_window_years: int = 3) -> dict:
    """Nascency diagnostics for one theme's jury-year timeline."""
    periods, by_period = _contiguous_year_series(counts)
    beta = D.beta_spec(periods, by_period, slope_window_years) if periods else 0.0
    first_year = min(counts) if counts else None
    last_year = max(counts) if counts else None
    total = sum(counts.values())
    recent = sum(n for y, n in counts.items() if y > current_year - recent_window_years)
    return {
        "beta_jury": round(beta, 4),
        "accelerating": beta > 0,
        "first_year": first_year,
        "last_year": last_year,
        "years_since_first": (current_year - first_year) if first_year is not None else None,
        "total_signals": total,
        "recency": round(recent / total, 4) if total else 0.0,
    }


def _theme_signals(conn, theme_id: str) -> list[dict]:
    """The (source, position, credibility) of every signal backing a theme — convergence-score input."""
    return [dict(r) for r in conn.execute(
        "SELECT s.source_id, s.diffusion_position, s.jury_credibility "
        "FROM theme_convergence tc JOIN jury_signals s ON s.signal_id=tc.signal_id "
        "WHERE tc.theme_id=?", (theme_id,))]


def _refined_horizon(horizon_years, band, metrics: dict):
    """Nudge the tier-based runway within its band using the timeline: all-recent + accelerating ⇒
    toward the long (early) end; old / decelerating ⇒ toward the short end. Heuristic, but now
    data-informed rather than tier-only (spec §5 wants horizon from history)."""
    if not band:
        return horizon_years
    lo, hi = band
    f = metrics["recency"] * (1.0 if metrics["accelerating"] else 0.5)
    f = max(0.0, min(1.0, f))
    return round(lo + f * (hi - lo), 2)


def rank_discovered(conn, cfg: dict, *, current_year: int | None = None) -> list[dict]:
    """Rank discovered themes by convergence × acceleration × recency. Pure read (recomputes from the
    stored jury_signals, the diffusion compute-on-read pattern). Returns dicts sorted best-first."""
    current_year = current_year or datetime.now(timezone.utc).year
    nb = cfg.get("nascency", {})
    L = nb.get("slope_window_years", 6)
    rw = nb.get("recent_window_years", 3)
    floor = nb.get("rank", {}).get("recency_floor", 0.5)
    hcfg = cfg["horizon"]
    out = []
    for t in conn.execute(
        "SELECT theme_id, label, horizon_years FROM themes WHERE discovered_from='jury_convergence'"
    ).fetchall():
        tid = t["theme_id"]
        counts = theme_year_counts(conn, tid, leading_only=True) or \
            theme_year_counts(conn, tid, leading_only=False)
        m = nascency_metrics(counts, slope_window_years=L, current_year=current_year,
                             recent_window_years=rw)
        sigs = _theme_signals(conn, tid)
        diag = convergence.score_group(sigs, cfg)
        horizon = convergence.estimate_horizon(diag["positions"], cfg)
        rank_score = diag["score"] * (floor + m["recency"]) * (1 + max(0.0, m["beta_jury"]))
        out.append({
            "theme_id": tid,
            "label": t["label"],
            "convergence_score": diag["score"],
            "n_leading_juries": diag["n_leading_juries"],
            "rank_score": round(rank_score, 4),
            "refined_horizon_years": _refined_horizon(horizon["years"], horizon["band"], m),
            **m,
        })
    out.sort(key=lambda r: -r["rank_score"])
    return out


def persist_refined_horizon(conn, ranked: list[dict]) -> int:
    """Write each theme's timeline-refined horizon back to themes.horizon_years (confidence='timeline').
    Returns rows updated. Optional — the rank itself is recomputed on read."""
    n = 0
    for r in ranked:
        conn.execute(
            "UPDATE themes SET horizon_years=?, horizon_confidence='timeline', updated_at=updated_at "
            "WHERE theme_id=?", (r["refined_horizon_years"], r["theme_id"]))
        n += 1
    conn.commit()
    return n
