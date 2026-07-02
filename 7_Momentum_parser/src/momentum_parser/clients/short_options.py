"""Tier-2 leading-input providers — short interest + options-implied move (spec §4c / M22).

CAVEATED, OPT-IN (`sources.short_options`). yfinance is the scaffold source: short stats come from
`Ticker.info` (exchange settlement data, **stale ~2x/month**) and options-implied move from `Ticker.option_chain`
(**delayed**). ToS-gray like the OHLCV provider — swap for a licensed short/options feed before relying on it
(data_provider_switch). Provider-isolated + fail-open: any failure returns ``{}`` and the signal is simply
absent (never bearish). Imports yfinance lazily so the package imports without it.
"""

from __future__ import annotations

import math
from typing import Optional


def _yf(ticker: str):
    try:
        import yfinance as yf
    except ImportError:  # pragma: no cover - provider optional
        return None
    try:
        return yf.Ticker(ticker)
    except Exception:  # pragma: no cover - provider error -> fail open
        return None


def _f(v) -> Optional[float]:
    try:
        f = float(v)
        return f if not math.isnan(f) else None
    except (TypeError, ValueError):
        return None


def fetch_short_stats(ticker: str) -> dict:  # pragma: no cover - network
    """Raw short-interest stats (stale). ``{}`` on any failure (fail-open)."""
    t = _yf(ticker)
    if t is None:
        return {}
    try:
        info = t.info or {}
    except Exception:
        return {}
    out = {
        "short_pct_float": _f(info.get("shortPercentOfFloat")),
        "shares_short": _f(info.get("sharesShort")),
        "shares_short_prior": _f(info.get("sharesShortPriorMonth")),
        "days_to_cover": _f(info.get("shortRatio")),
    }
    return out if any(v is not None for v in out.values()) else {}


def fetch_options_iv(ticker: str, horizon_days: int = 7) -> dict:  # pragma: no cover - network
    """ATM implied move + IV + a rough put/call skew for the nearest expiry >= horizon. ``{}`` on failure."""
    t = _yf(ticker)
    if t is None:
        return {}
    try:
        expiries = list(t.options or [])
        if not expiries:
            return {}
        expiry = expiries[min(range(len(expiries)), key=lambda i: abs(i - 0)) if len(expiries) == 1 else
                          _nearest_expiry_idx(expiries, horizon_days)]
        chain = t.option_chain(expiry)
        spot = _f((t.fast_info or {}).get("last_price")) or _f(t.info.get("currentPrice"))
        return _implied(chain.calls, chain.puts, spot)
    except Exception:
        return {}


def _nearest_expiry_idx(expiries, horizon_days: int) -> int:  # pragma: no cover - network
    from datetime import date
    today = date.today()
    diffs = []
    for e in expiries:
        try:
            d = (date.fromisoformat(e) - today).days
        except (ValueError, TypeError):
            d = 9999
        diffs.append(abs(d - horizon_days) if d >= 0 else 9999)
    return min(range(len(expiries)), key=lambda i: diffs[i])


def _implied(calls, puts, spot: Optional[float]) -> dict:  # pragma: no cover - network
    if spot is None or spot <= 0 or calls is None or puts is None or calls.empty or puts.empty:
        return {}
    ci = (calls["strike"] - spot).abs().idxmin()
    pi = (puts["strike"] - spot).abs().idxmin()
    atm_call, atm_put = calls.loc[ci], puts.loc[pi]
    cmid = _mid(atm_call)
    pmid = _mid(atm_put)
    atm_iv = _f(atm_call.get("impliedVolatility"))
    implied_move = ((cmid + pmid) / spot) if (cmid and pmid) else None
    # rough skew: an OTM put IV minus an OTM call IV (positive = downside hedging demand)
    otm_put = puts[puts["strike"] < spot]
    otm_call = calls[calls["strike"] > spot]
    skew = None
    if not otm_put.empty and not otm_call.empty:
        pv = _f(otm_put.iloc[-1].get("impliedVolatility"))
        cv = _f(otm_call.iloc[0].get("impliedVolatility"))
        if pv is not None and cv is not None:
            skew = round(pv - cv, 4)
    return {k: v for k, v in {"implied_move_pct": round(implied_move, 4) if implied_move else None,
                              "atm_iv": round(atm_iv, 4) if atm_iv else None, "skew": skew}.items()
            if v is not None}


def _mid(row) -> Optional[float]:  # pragma: no cover - network
    bid, ask, last = _f(row.get("bid")), _f(row.get("ask")), _f(row.get("lastPrice"))
    if bid and ask:
        return (bid + ask) / 2
    return last
