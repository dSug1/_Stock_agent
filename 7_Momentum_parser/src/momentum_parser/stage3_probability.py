"""Stage 3 — turn signals into a weekly price-change probability per ticker, persisted per run."""

from __future__ import annotations

from .models import Prediction
from .probability import estimate
from .signals import compute_signals
from .stage2_signals import rolling_composites
from .store import Store


def run(store: Store, tickers: list[str], cfg: dict, run_id: str, log=print) -> dict:
    sig_cfg = cfg.get("signals", {})
    prob_cfg = cfg.get("probability", {})
    min_bars = int(sig_cfg.get("min_bars", 60))
    predicted = 0
    for t in tickers:
        bars = store.get_bars(t)
        if len(bars) < min_bars:
            continue
        closes = [b.close for b in bars]
        signals, composite = compute_signals(bars, sig_cfg)
        comps_hist = rolling_composites(bars, cfg, min_bars) if prob_cfg.get("use_empirical", True) else None
        p_up, exp_ret, conf = estimate(closes, composite, prob_cfg, comps_hist)
        pred = Prediction(
            ticker=t, asof_date=bars[-1].date,
            horizon_days=int(prob_cfg.get("horizon_days", 5)),
            p_up=p_up, expected_return=exp_ret, confidence=conf,
            composite=composite, signals=signals, last_close=closes[-1],
        )
        store.write_prediction(pred, run_id)
        predicted += 1
        log(f"  [prob] {t}: P(up,{pred.horizon_days}d)={p_up:.2f} ER={exp_ret:+.2%} conf={conf:.2f}")
    return {"predicted": predicted}
