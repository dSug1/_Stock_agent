"""Stage 5 — hybrid blend + long-only ranking + ledger append (spec §5, §8, Decisions A/H/J/K).

For each live ticker: read the `p_claude` leg from `scores`, compute the `p_model` leg (`model.p_up`),
blend → `p_final`, compute confidence, write the blended `Prediction`, and append an open row to the
forward-validation `ledger`. Pure-ish orchestration (no network); `model`/`blend` are pure.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from . import blend as blend_mod
from . import model, targets, topdown_model
from .models import Prediction, Signal
from .signals import compute_signals
from .store import Store
from .taxonomy import load_taxonomy


def _data_coverage(store: Store, ticker: str, asof: str, n_bars: int, min_bars: int) -> float:
    """Fraction of the four dimensions actually populated (6_Biotech rule 10)."""
    present = 1.0 if n_bars >= min_bars else 0.5 if n_bars else 0.0     # technical
    dims = {r["dimension"]: r for r in store.get_evidence(ticker, asof)}
    for d in ("media", "search"):
        feats = json.loads(dims[d]["features_json"]) if d in dims and dims[d]["features_json"] else {}
        present += 1.0 if feats.get("surge_ratio") is not None else 0.0
    present += 1.0 if store.next_catalyst(ticker, asof) else 0.0        # catalyst
    return present / 4.0


def run(store: Store, tickers: list[str], cfg: dict, run_id: str, log=print) -> dict:
    prob_cfg = cfg.get("probability", {})
    sig_cfg = cfg.get("signals", {})
    bl = cfg.get("blend", {})
    w = float(bl.get("claude_weight", 0.6))
    disagree = float(bl.get("disagree_threshold", 0.25))
    horizon = int(prob_cfg.get("horizon_days", 5))
    min_bars = int(sig_cfg.get("min_bars", 60))
    up_call = float(cfg.get("validation", {}).get("up_call_threshold", 0.5))

    # load persisted calibrators (M6) — identity until fitted; dispatched by leg
    cal_model = store.load_calibrator("model")
    cal_claude = store.load_calibrator("claude")
    calibrate = lambda p, leg: (cal_model if leg == "model" else cal_claude).apply(p)
    taxonomy = load_taxonomy(cfg)                          # for the top-down term (§4b / M13)

    written = with_claude = flagged = 0
    for t in tickers:
        bars = store.get_bars(t)
        if not bars:
            continue
        asof = bars[-1].date
        closes = [b.close for b in bars]

        td_logit, td_breakdown = topdown_model.logit_for(store, t, cfg, taxonomy)  # 0.0 if no harvest
        p_model, exp_ret, comps = model.p_up(bars, cfg, topdown_logit=td_logit)
        sc = store.latest_score(t, asof)
        p_claude = sc["p_up"] if sc else None
        conviction = sc["conviction"] if sc else 0.4
        if p_claude is not None:
            with_claude += 1

        p_final, d, review = blend_mod.blend(p_claude, p_model, w, disagree, calibrate=calibrate)
        coverage = _data_coverage(store, t, asof, len(bars), min_bars)
        hist = min(1.0, len(bars) / (horizon * 8))
        conf = blend_mod.confidence(conviction, coverage, d, hist)
        if review:
            flagged += 1

        signals, composite = compute_signals(bars, sig_cfg)
        store.write_prediction(Prediction(
            ticker=t, asof_date=asof, horizon_days=horizon, p_up=p_final, expected_return=exp_ret,
            confidence=conf, composite=composite, signals=signals, last_close=closes[-1],
            p_claude=p_claude, p_model=p_model, disagreement=d, review=review,
        ), run_id)

        predicted_label = targets.UP if p_final >= up_call else targets.FLAT
        store.append_ledger(t, asof, run_id, horizon, p_up=p_final, p_final=p_final,
                            predicted_label=predicted_label, sigma_week=comps.get("sigma_week"))
        written += 1
        log(f"  [blend] {t}@{asof}: p_final={p_final:.2f} (claude={p_claude} model={p_model:.2f} "
            f"d={d if d is None else round(d,2)}) conf={conf:.2f}{' REVIEW' if review else ''}")

    return {"written": written, "with_claude": with_claude, "flagged_review": flagged}
