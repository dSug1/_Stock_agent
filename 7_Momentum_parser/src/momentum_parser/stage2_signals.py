"""Stage 2 — compute the trade-signal basket for each ticker as of its latest cached bar."""

from __future__ import annotations

from typing import Optional

from .models import Bar, Signal
from .signals import compute_signals
from .store import Store


def rolling_composites(bars: list[Bar], cfg: dict, min_bars: int) -> list[Optional[float]]:
    """Composite signal recomputed on each expanding window — the history Stage 3's empirical
    estimator buckets against. ``None`` for early bars that lack warm-up."""
    sig_cfg = cfg.get("signals", {})
    out: list[Optional[float]] = []
    for i in range(len(bars)):
        if i + 1 < min_bars:
            out.append(None)
            continue
        _, comp = compute_signals(bars[: i + 1], sig_cfg)
        out.append(comp)
    return out


def run(store: Store, tickers: list[str], cfg: dict, log=print) -> dict:
    sig_cfg = cfg.get("signals", {})
    min_bars = int(cfg.get("signals", {}).get("min_bars", 60))
    computed = thin = 0
    for t in tickers:
        bars = store.get_bars(t)
        if len(bars) < min_bars:
            thin += 1
            continue
        signals, _comp = compute_signals(bars, sig_cfg)
        store.write_signals(t, bars[-1].date, signals)
        computed += 1
        fired = [s.name for s in signals if s.fired]
        log(f"  [sig]  {t}: {len(signals)} signals" + (f" | fired: {', '.join(fired)}" if fired else ""))
    return {"computed": computed, "thin_history": thin}
