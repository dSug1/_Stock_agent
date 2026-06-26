"""Panel stratification diagnostics (Protocol §2.3) — Stage C scaffolding (D19).

The kill-switch only earns a verdict on a panel that is **representative**, not a disguised
"2021 biotech bubble" screener (D14 blockers 3 & 4). Protocol §2.3 sets the rules:
  - **size** — n ≥ 100 labeled episodes (≥ 50 positive);
  - **quota** — hard_negative : positive ≥ 1 : 1; easy_negative capped;
  - **temporal** — positives must NOT cluster in any single 12-month window;
  - **sector** — spread across ≥ several sectors (not all biotech);
  - **regime** — both A (discrete catalyst) and B (continuous) present.

This module MEASURES those (and flags violations) so the Stage-C expansion is guided by what's actually
missing rather than by guesswork. Pure functions; a thin reader pulls rows from the panel table.
"""

from collections import Counter
from datetime import date, timedelta


def max_window_share(dates, window_days: int = 365) -> float:
    """Largest fraction of `dates` (YYYY-MM-DD) falling in any rolling `window_days` window. 1.0 = all
    clustered together; ~uniform spread over K years ≈ window/span. The temporal-clustering metric."""
    ds = sorted(date.fromisoformat(d) for d in dates if d)
    if not ds:
        return 0.0
    n = len(ds)
    best = 0
    for s in ds:
        e = s + timedelta(days=window_days)
        best = max(best, sum(1 for d in ds if s <= d < e))
    return best / n


def evaluate_strata(rows, *, min_n=100, min_positive=50, max_temporal_share=0.50,
                    min_sectors=2, required_regimes=("A", "B")):
    """rows: dicts with label, t0, sector, theme, regime. Returns a diagnostics dict whose `flags`
    list is the set of Protocol §2.3 violations (empty => `stratified_ok`)."""
    n = len(rows)
    pos = [r for r in rows if r.get("label") == "positive"]
    hard = [r for r in rows if r.get("label") == "hard_negative"]
    easy = [r for r in rows if r.get("label") == "easy_negative"]
    sectors = Counter(r.get("sector") for r in rows if r.get("sector") is not None)
    themes = Counter(r.get("theme") for r in rows)
    regimes = Counter(r.get("regime") for r in rows if r.get("regime"))
    eras = Counter((r.get("t0") or "")[:4] for r in rows if r.get("t0"))
    temporal = max_window_share([r.get("t0") for r in pos]) if pos else 0.0

    flags = []
    if n < min_n:
        flags.append(f"underpowered: n={n} < {min_n}")
    if len(pos) < min_positive:
        flags.append(f"too few positives: {len(pos)} < {min_positive}")
    if pos and len(hard) < len(pos):
        flags.append(f"quota: hard_negative {len(hard)} < positives {len(pos)} (need >=1:1)")
    if temporal > max_temporal_share:
        flags.append(f"temporal clustering: {temporal:.0%} of positives in a 12mo window "
                     f"> {max_temporal_share:.0%}")
    if len(sectors) < min_sectors:
        flags.append(f"sector spread: {len(sectors)} sector(s) < {min_sectors}")
    missing = [r for r in required_regimes if r not in regimes]
    if missing:
        flags.append(f"regime balance: missing {missing}")

    return {
        "n": n,
        "n_positive": len(pos),
        "n_hard_negative": len(hard),
        "n_easy_negative": len(easy),
        "pos_neg_ratio": (len(pos) / len(hard)) if hard else None,
        "temporal_positive_share": temporal,
        "sectors": dict(sectors),
        "themes": dict(themes),
        "regimes": dict(regimes),
        "eras": dict(sorted(eras.items())),
        "flags": flags,
        "stratified_ok": not flags,
    }


def strata_from_panel(conn, label_source="crude_derived", **kw):
    """Read the panel rows and run evaluate_strata. Sector is read from the stored `sector` feature
    (1.0=bio / 0.0=tech) so it reflects what the build actually recorded."""
    rows = []
    for r in conn.execute(
        "SELECT panel_id, label, t0_date, theme, regime FROM panel WHERE label_source=?",
        (label_source,),
    ):
        sec = conn.execute(
            "SELECT value FROM panel_features WHERE panel_id=? AND feature='sector'",
            (r["panel_id"],)).fetchone()
        sector = None
        if sec is not None:
            sector = "bio" if sec[0] == 1.0 else "tech"
        rows.append({"label": r["label"], "t0": r["t0_date"], "theme": r["theme"],
                     "regime": r["regime"], "sector": sector})
    return evaluate_strata(rows, **kw)
