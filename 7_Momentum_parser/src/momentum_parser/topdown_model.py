"""Top-down contribution to `p_model` (spec §4b.4 / M13).

Turns the day's ANTICIPATED signals into a single log-odds shift for a ticker:
``score = Σ_s w_s · β_{t,s} · surprise_s`` over the ACTIVE (anticipated) signals, then ``logit = gain·score``.
- ``w_s``  — the LEARNED signal weight (regime-conditional; falls back to 'all', then 0).
- ``β_{t,s}`` — MARKET-scope signals feel the regime uniformly (β=1); factor/sector/ticker use the learned
  loading (falls back to ``beta_default`` = 0 = no tilt until initialized).
- ``surprise_s`` — the anticipated tailwind(+)/headwind(-) from Stage 2b.
Pure ``topdown_score``; ``logit_for`` gathers the store rows around it.
"""

from __future__ import annotations


def topdown_score(active_signals, weights: dict, loadings: dict,
                  scope_by_signal: dict, cfg: dict) -> tuple[float, dict]:
    """Σ w_s·β·surprise over active signals. ``active_signals`` rows expose ['signal_id','surprise']."""
    beta_default = float(cfg.get("topdown", {}).get("beta_default", 0.0))
    score = 0.0
    breakdown: dict[str, float] = {}
    for a in active_signals:
        sid = a["signal_id"]
        s = a["surprise"]
        if s is None:
            continue
        scope = scope_by_signal.get(sid, "market")
        beta = 1.0 if scope == "market" else loadings.get(sid, beta_default)
        contrib = weights.get(sid, 0.0) * beta * s
        if contrib:
            breakdown[sid] = round(contrib, 4)
        score += contrib
    return score, breakdown


def logit_for(store, ticker: str, cfg: dict, taxonomy: dict) -> tuple[float, dict]:
    """The log-odds shift for ``ticker`` from the latest harvest. (0.0, {}) when nothing is anticipated."""
    asof = store.latest_macro_asof()
    if not asof:
        return 0.0, {}
    active = store.get_macro_signals(asof, active_only=True)
    if not active:
        return 0.0, {}
    regime = active[0]["regime"] or "neutral"              # all rows in a harvest share the regime read
    scope_by = {s.id: s.scope for s in taxonomy["signals"]}
    weights = {}
    for a in active:
        wrow = store.get_signal_weight(a["signal_id"], regime)
        weights[a["signal_id"]] = wrow["w"] if wrow else 0.0
    loadings = {r["signal_id"]: r["beta"] for r in store.get_ticker_loadings(ticker)}
    score, breakdown = topdown_score(active, weights, loadings, scope_by, cfg)
    gain = float(cfg.get("topdown", {}).get("model_gain", 1.5))
    return gain * score, breakdown
