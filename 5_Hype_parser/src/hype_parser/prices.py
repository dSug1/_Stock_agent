"""Forward-return engine for the labeled panel (Protocol section 2.1).

Given (ticker, t0, horizon) computes the forward return plus max draw-up / draw-down over the
window — the raw material for labelling (target_hit / touched_then_faded / flat / loss). Uses
yfinance (LOCAL-PERSONAL ONLY until a licensed provider is swapped — repo memory
`project_data_provider_switch`); the fetch function is injectable so tests run offline and a
provider swap is a one-function change.
"""

import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)


def _default_fetch(ticker: str, start: str, end: str):
    """Return [(YYYY-MM-DD, adj_close), ...] sorted ascending. yfinance-backed."""
    import yfinance as yf
    df = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
    out = []
    for idx, row in df.iterrows():
        close = row.get("Close")
        if close is None or close != close:  # skip NaN
            continue
        out.append((idx.strftime("%Y-%m-%d"), float(close)))
    return out


def _add_weeks(iso: str, weeks: int) -> str:
    return (date.fromisoformat(iso) + timedelta(weeks=weeks)).isoformat()


def forward_returns(ticker: str, t0: str, horizons_weeks, *, fetch=None,
                    today: str | None = None, reach_tolerance_days: int = 14) -> dict:
    """Per-horizon forward return from t0. A horizon is only reported if price data actually
    reaches within `reach_tolerance_days` of the target date (so a too-recent t0 yields only the
    horizons that have elapsed). Returns {weeks: {start_price, end_price, fwd_return, max_drawup,
    max_drawdown}}."""
    fetch = fetch or _default_fetch
    today = today or date.today().isoformat()
    max_w = max(horizons_weeks)
    end = min(_add_weeks(t0, max_w + 2), today)
    series = fetch(ticker, t0, end)
    if len(series) < 2:
        return {}
    start_price = series[0][1]
    out = {}
    for h in sorted(horizons_weeks):
        target = _add_weeks(t0, h)
        window = [(d, p) for d, p in series if d <= target]
        if len(window) < 2:
            continue
        last_date, end_price = window[-1]
        # require data to actually reach near the target (else the horizon hasn't elapsed)
        if (date.fromisoformat(target) - date.fromisoformat(last_date)).days > reach_tolerance_days:
            continue
        prices = [p for _, p in window]
        out[h] = {
            "start_price": start_price,
            "end_price": end_price,
            "fwd_return": end_price / start_price - 1.0,
            "max_drawup": max(prices) / start_price - 1.0,
            "max_drawdown": min(prices) / start_price - 1.0,
        }
    return out
