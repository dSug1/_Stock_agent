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
