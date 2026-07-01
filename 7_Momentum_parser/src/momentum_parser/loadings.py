"""Per-ticker loadings β_{t,s} — initialization (spec §4b / M13).

A ticker's sensitivity to each FACTOR/SECTOR signal is seeded by regressing its trailing daily returns on
the factor-spread returns (the same spread the surprise is built from), then the M16 feedback loop learns
from there. MARKET-scope signals are not regressed — everyone feels the regime, so their β is 1 by
convention (applied in `topdown_model`). Pure regression here; a thin store-facing `refresh_universe`
persists results. Missing/short data → the signal is simply skipped (absence ≠ signal).
"""

from __future__ import annotations

from typing import Optional, Sequence

# factor/sector signal id -> the (numerator, denominator) proxy roles whose spread-return is the factor
FACTOR_SPREADS = {
    "ai_crowding": ("ai_basket", "market"),
    "style_factors": ("growth", "value"),
    "sector_flows": ("hibeta", "lowvol"),
}


def daily_returns(series: Sequence[Optional[float]]) -> list[float]:
    s = [float(x) for x in (series or []) if x is not None]
    return [s[i] / s[i - 1] - 1.0 for i in range(1, len(s)) if s[i - 1]]


def ols_beta(y: Sequence[float], x: Sequence[float]) -> Optional[float]:
    """Slope of the OLS fit y ~ a + b·x over the overlapping tail; None if too short or x is constant."""
    n = min(len(y), len(x))
    if n < 3:
        return None
    y, x = list(y[-n:]), list(x[-n:])
    mx, my = sum(x) / n, sum(y) / n
    var = sum((xi - mx) ** 2 for xi in x)
    if var <= 1e-12:
        return None
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    return cov / var


def _spread_returns(series_by_role: dict, num: str, den: str) -> list[float]:
    ra, rb = daily_returns(series_by_role.get(num, [])), daily_returns(series_by_role.get(den, []))
    n = min(len(ra), len(rb))
    return [ra[-n + i] - rb[-n + i] for i in range(n)] if n else []


def init_ticker_loadings(ticker_closes: Sequence[float], series_by_role: dict,
                         taxonomy: dict, cfg: dict) -> dict[str, float]:
    """β for each factor/sector signal = OLS slope of ticker returns on the factor-spread returns."""
    win = int(cfg.get("topdown", {}).get("loadings_window", 60))
    tr = daily_returns(ticker_closes)[-win:]
    factor_ids = {s.id for s in taxonomy["signals"] if s.scope in ("factor", "sector")}
    out: dict[str, float] = {}
    for sid, (num, den) in FACTOR_SPREADS.items():
        if sid not in factor_ids:
            continue
        fr = _spread_returns(series_by_role, num, den)[-win:]
        n = min(len(tr), len(fr))
        if n < 3:
            continue
        b = ols_beta(tr[-n:], fr[-n:])
        if b is not None:
            out[sid] = round(b, 4)
    return out


def refresh_universe(store, tickers: list[str], cfg: dict, taxonomy: dict | None = None,
                     fetch=None, updated_at: str = "", log=print) -> dict:
    """Persist initial β for each ticker (reuses cached bars; fetches proxy series once). Injectable ``fetch``."""
    from .stage2b_topdown import _default_series
    from .taxonomy import load_taxonomy
    taxonomy = taxonomy or load_taxonomy(cfg)
    fetch = fetch or _default_series
    proxies = cfg.get("topdown", {}).get("proxies", {})
    series = {}
    for role, tk in proxies.items():
        try:
            series[role] = fetch(tk) or []
        except Exception:                                  # pragma: no cover - fail open
            series[role] = []
    done = 0
    for t in tickers:
        closes = [b.close for b in store.get_bars(t)]
        loads = init_ticker_loadings(closes, series, taxonomy, cfg)
        for sid, b in loads.items():
            store.upsert_ticker_loading(t, sid, beta=b, beta_prior=b, n=0, updated_at=updated_at)
        if loads:
            done += 1
    log(f"  [loadings] initialized β for {done}/{len(tickers)} tickers")
    return {"tickers": done}
