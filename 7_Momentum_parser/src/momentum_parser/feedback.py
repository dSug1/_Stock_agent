"""The self-growing feedback loop (spec §4b.3 / M16) — the keystone the operator asked for.

Once outcomes settle, this:
  1. **settles** each open catalyst hypothesis (realized drift over its horizon) — closing the falsifiable
     predictions M15 recorded;
  2. records **attribution** (a settled move split into market / factor / idiosyncratic — a diagnostic);
  3. **learns the signal weights** `w_s` (regime-conditional) from whether each signal's anticipated
     direction actually matched the realized move — shrinkage-regularized to the priors at small n, so a
     signal only earns weight once it has demonstrably anticipated moves. β loadings stay the M13 regression.

Pure math (`signal_skill` / `updated_weight` / `attribute`) + a thin store-facing orchestration. The weights
it writes are read straight back by `topdown_model` (M13), so the model grows as the ledger fills.
"""

from __future__ import annotations

from datetime import datetime, timezone


# --- pure math ----------------------------------------------------------------------------------
def signal_skill(hits: int, n: int, pseudocount: float) -> float:
    """Directional skill in [-1, 1] from a shrunk hit-rate. 0 = no skill (or no data); +1 perfect.

    Shrinkage: the hit-rate is pulled toward 0.5 by ``pseudocount`` pseudo-observations, so a signal stays
    near skill 0 (→ weight ≈ prior) until it has enough settled evidence.
    """
    if n <= 0:
        return 0.0
    hit_rate = (hits + 0.5 * pseudocount) / (n + pseudocount)
    return 2.0 * (hit_rate - 0.5)


def updated_weight(w_prior: float, hits: int, n: int, pseudocount: float, w_max: float) -> float:
    """New weight = prior scaled by (1 + skill), clamped to [0, w_max]. Anti-predictive → toward 0."""
    return round(max(0.0, min(w_max, w_prior * (1.0 + signal_skill(hits, n, pseudocount)))), 4)


def attribute(active_signals, weights: dict, loadings: dict, scope_by_signal: dict,
              realized_return: float) -> dict:
    """Split a realized move into market / factor / idiosyncratic predicted contributions (a diagnostic)."""
    market = factor = 0.0
    contribs: dict[str, float] = {}
    for a in active_signals:
        sid, s = a["signal_id"], a["surprise"]
        if s is None:
            continue
        scope = scope_by_signal.get(sid, "market")
        beta = 1.0 if scope == "market" else loadings.get(sid, 0.0)
        c = weights.get(sid, 0.0) * beta * s
        contribs[sid] = round(c, 4)
        if scope == "market":
            market += c
        else:
            factor += c
    return {"market_comp": round(market, 4), "factor_comp": round(factor, 4),
            "idio_comp": round(realized_return - (market + factor), 4), "contributions": contribs}


# --- store-facing orchestration -----------------------------------------------------------------
def settle_catalyst_hypotheses(store, cfg: dict, now: str, log=print) -> dict:
    """Fill realized_drift for hypotheses whose horizon has elapsed (M15 predictions → falsified/confirmed)."""
    settled = 0
    cache: dict = {}
    for hyp in store.open_catalyst_hypotheses():
        t, asof = hyp["ticker"], hyp["asof"]
        horizon = int(hyp["horizon_days"] or 5)
        if t not in cache:
            bars = store.get_bars(t)
            cache[t] = (bars, {b.date: i for i, b in enumerate(bars)})
        bars, idx = cache[t]
        i = idx.get(asof)
        if i is None or i + horizon >= len(bars):
            continue
        base = bars[i].close
        realized = (bars[i + horizon].close / base - 1.0) if base else 0.0
        store.settle_catalyst_hypothesis(t, asof, hyp["event_date"], round(realized, 4), now)
        settled += 1
    log(f"  [feedback] settled {settled} catalyst hypotheses")
    return {"catalysts_settled": settled}


def learn(store, cfg: dict, taxonomy: dict, now: str, log=print) -> dict:
    """Update regime-conditional signal weights from settled outcomes + record attribution."""
    scope_by = {s.id: s.scope for s in taxonomy["signals"]}
    prior_by = {s.id: s.w_prior for s in taxonomy["signals"]}
    learning = taxonomy.get("learning", {})
    pseudo = float(learning.get("shrinkage_prior_n", 30))
    w_max = float(cfg.get("topdown", {}).get("weight_max", 0.5))

    acc: dict = {}                                          # (signal_id, regime) -> [hits, n]
    used = 0
    for row in store.settled_ledger():
        r = row["realized_return"]
        if r is None:
            continue
        asof, ticker = row["asof"], row["ticker"]
        active = store.get_macro_signals(asof, active_only=True)
        if not active:                                     # no top-down read archived for that day -> skip
            continue
        regime = active[0]["regime"] or "neutral"
        loadings = {x["signal_id"]: x["beta"] for x in store.get_ticker_loadings(ticker)}
        weights = {}
        for a in active:
            wrow = store.get_signal_weight(a["signal_id"], regime)
            weights[a["signal_id"]] = wrow["w"] if wrow else prior_by.get(a["signal_id"], 0.0)
        att = attribute(active, weights, loadings, scope_by, r)
        store.upsert_attribution(ticker, asof, row["run_id"], r, att["market_comp"],
                                 att["factor_comp"], att["idio_comp"], att["contributions"], now)
        for a in active:
            sid, s = a["signal_id"], a["surprise"]
            if s is None:
                continue
            beta = 1.0 if scope_by.get(sid, "market") == "market" else loadings.get(sid, 0.0)
            pred = beta * s
            if pred == 0:
                continue
            hit = 1 if (pred > 0) == (r > 0) else 0         # did the anticipated direction match reality?
            for key in ((sid, regime), (sid, "all")):
                h, n = acc.get(key, (0, 0))
                acc[key] = (h + hit, n + 1)
        used += 1

    for (sid, regime), (h, n) in acc.items():
        w = updated_weight(prior_by.get(sid, 0.0), h, n, pseudo, w_max)
        store.set_signal_weight(sid, regime, w, prior_by.get(sid, 0.0), n, now)
    log(f"  [feedback] learned {len(acc)} (signal,regime) weights from {used} settled predictions")
    return {"weights_updated": len(acc), "settled_used": used}


def run(store, cfg: dict, now: str | None = None, log=print) -> dict:
    """The daily feedback pass: settle catalyst hypotheses, then learn weights + record attribution."""
    from .taxonomy import load_taxonomy
    now = now or datetime.now(timezone.utc).isoformat()
    out = dict(settle_catalyst_hypotheses(store, cfg, now, log))
    out.update(learn(store, cfg, load_taxonomy(cfg), now, log))
    return out
