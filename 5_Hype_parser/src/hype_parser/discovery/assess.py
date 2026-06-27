"""Theme assessment — fuse the two nascency signals and compare discovered themes against the
hand-seeded baseline (spec §9 step 5).

Discovery now produces two independent reads of *how early* a theme is:
  - the **jury timeline** (`nascency.py`): is expert recognition recent + accelerating? (`beta_jury`)
  - the **corpus diffusion** (`diffusion_bridge.py` → radar): is the *literature* accelerating while
    the mainstream stays low? (`beta_spec`, `p_main`, `nascency_gate`)

This module fuses them into one `combined_score` per discovered theme, and lays the discovered themes
next to the **hand-seeded** themes on the *same* diffusion metrics — so you can see whether discovery
surfaces themes with comparable early-diffusion signatures to the curated baseline. It is a pure read
over the stored series (no network); the radar must have measured a theme for its corpus row to appear
(`5_radar.py --include-discovered`). The full panel/forward-return back-test (Protocol §4) is separate.
"""

import logging

from .. import diffusion as D
from .. import themes as T
from . import nascency

log = logging.getLogger(__name__)


def theme_corpus_metrics(conn, theme_id: str, params: dict) -> dict | None:
    """Diffusion summary (β_spec / p_main / nascency_gate) over a theme's stored series, or None if the
    radar hasn't measured it yet. Same instrument for discovered and hand-seeded themes."""
    series = [dict(r) for r in T.read_series(conn, theme_id)]
    if not series:
        return None
    periods = [r["period"] for r in series]
    n_spec = {r["period"]: r["n_spec"] for r in series}
    n_main = {r["period"]: r["n_main"] for r in series}
    s = D.summarize(periods, n_spec, n_main, L=params["L"], tau_member=params["tau_member"],
                    beta_min=params["beta_min"], p_max=params["p_max"])
    return {
        "n_spec_total": s["n_spec_total"],
        "beta_spec": round(s["beta_spec"], 4),
        "p_main": round(s["p_main"], 4),
        "nascency_gate": bool(s["nascency_gate"]),
        "latest_period": s["latest_period"],
    }


def assess_discovered(conn, cfg: dict, params: dict, *, current_year: int | None = None) -> list[dict]:
    """Discovered themes with BOTH signals + a fused score, best-first.

    `combined_score` = jury-timeline `rank_score` lifted by the corpus slope when the theme has been
    measured (`× (1 + max(0, beta_spec))`); jury-only when it hasn't. So measuring a theme on the
    diffusion engine can only *raise* its rank — an unmeasured theme is never penalised, just un-lifted.
    """
    ranked = nascency.rank_discovered(conn, cfg, current_year=current_year)
    for r in ranked:
        cm = theme_corpus_metrics(conn, r["theme_id"], params)
        r["corpus"] = cm
        r["corpus_measured"] = cm is not None
        r["combined_score"] = round(
            r["rank_score"] * (1 + max(0.0, cm["beta_spec"])) if cm else r["rank_score"], 4)
    ranked.sort(key=lambda r: -r["combined_score"])
    return ranked


def seed_baseline(conn, params: dict) -> list[dict]:
    """Hand-seeded themes with their corpus diffusion metrics — the comparison baseline. Only themes
    the radar has measured (a series exists) appear."""
    rows = conn.execute(
        "SELECT theme_id, label FROM themes WHERE discovered_from IS NULL ORDER BY theme_id"
    ).fetchall()
    out = []
    for r in rows:
        cm = theme_corpus_metrics(conn, r["theme_id"], params)
        if cm:
            out.append({"theme_id": r["theme_id"], "label": r["label"], "corpus": cm})
    out.sort(key=lambda x: -x["corpus"]["beta_spec"])
    return out


def compare(conn, cfg: dict, params: dict, *, current_year: int | None = None) -> dict:
    """Both sides of the §9-step-5 comparison: discovered (fused) vs hand-seeded baseline, plus a tiny
    summary (how many discovered themes have been measured, and how their β_spec compares to seeds)."""
    discovered = assess_discovered(conn, cfg, params, current_year=current_year)
    seeds = seed_baseline(conn, params)
    measured = [d for d in discovered if d["corpus_measured"]]
    seed_betas = [s["corpus"]["beta_spec"] for s in seeds]
    disc_betas = [d["corpus"]["beta_spec"] for d in measured]
    summary = {
        "n_discovered": len(discovered),
        "n_discovered_measured": len(measured),
        "n_seed_measured": len(seeds),
        "median_seed_beta_spec": _median(seed_betas),
        "median_discovered_beta_spec": _median(disc_betas),
    }
    return {"discovered": discovered, "seed_baseline": seeds, "summary": summary}


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    mid = n // 2
    return round(s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2, 4)
