"""Stage 1 — fetch daily OHLCV for the universe and cache it in the store (incremental)."""

from __future__ import annotations

from .clients import market
from .store import Store
from .universe import UniverseRow


def run(store: Store, universe: list[UniverseRow], cfg: dict, log=print) -> dict:
    """Fetch + upsert bars for each ticker. Returns a small funnel dict for observability."""
    period = cfg.get("prices", {}).get("history_period", "2y")
    fetched = skipped = 0
    for row in universe:
        bars = market.fetch_daily_bars(row.ticker, period=period)
        if not bars:
            skipped += 1
            log(f"  [skip] {row.ticker}: no data (fail-open)")
            continue
        n = store.upsert_bars(row.ticker, bars)
        fetched += 1
        log(f"  [ok]   {row.ticker}: {n} bars -> {bars[-1].date}")
    return {"tickers": len(universe), "fetched": fetched, "skipped": skipped}


def benchmark_ticker(cfg: dict) -> str:
    """The RS benchmark (M20): explicit `leading.benchmark`, else the top-down market proxy, else SPY."""
    return (cfg.get("leading", {}).get("benchmark")
            or cfg.get("topdown", {}).get("proxies", {}).get("market") or "SPY")


def ensure_benchmark(store: Store, cfg: dict, log=print) -> dict:
    """Cache the RS benchmark's bars so relative-strength is computable offline at score time (fail-open)."""
    t = benchmark_ticker(cfg)
    bars = market.fetch_daily_bars(t, period=cfg.get("prices", {}).get("history_period", "2y"))
    if not bars:
        log(f"  [skip] benchmark {t}: no data (RS omitted, fail-open)")
        return {"benchmark": t, "bars": 0}
    store.upsert_bars(t, bars)
    log(f"  [ok]   benchmark {t}: {len(bars)} bars cached for RS")
    return {"benchmark": t, "bars": len(bars)}
