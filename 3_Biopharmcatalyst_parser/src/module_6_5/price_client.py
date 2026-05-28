"""Module 6.5 — share-price fetcher (yfinance).

Pure read-side. Returns the most recent close per ticker. The result is
cached *inline* on the `financials.last_price_usd / last_price_as_of`
columns — there is no separate prices.db sidecar (we deliberately keep
fundamentals.db as the single source of truth).

Memory `project_data_provider_switch` flags yfinance as a pre-deploy
swap target (must move to Twelve Data or similar before public hosting).
Until then yfinance is fine for nightly batch runs.

Stale-while-revalidate semantics (memory `feedback_swr_pattern`): the
M7 context_pack reads whatever's in fundamentals.db immediately, and
this module runs in the background only if the cached price is older
than `max_age_hours`.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

try:
    import yfinance as yf
except ImportError:                                              # pragma: no cover
    yf = None                                                    # type: ignore

log = logging.getLogger(__name__)


@dataclass
class PriceResult:
    ticker: str
    last_price_usd: Optional[float]
    last_price_as_of: Optional[str]            # ISO date string
    status: str                                # 'ok' | 'partial' | 'failed'
    error: Optional[str] = None


def _to_iso_date(ts) -> Optional[str]:
    """Normalize a pandas Timestamp / datetime / date to ISO date string."""
    if ts is None:
        return None
    try:
        if hasattr(ts, "date"):
            return ts.date().isoformat()
        if isinstance(ts, dt.date):
            return ts.isoformat()
        return str(ts)[:10]
    except Exception:                                            # noqa: BLE001
        return None


def fetch_last_close(ticker: str, *, lookback_days: int = 7) -> PriceResult:
    """Return the most recent close for one ticker (last 7 trading days).

    Why 7 days: BPC's BPC docx exports usually include a recent close;
    we just want a fresh authoritative number for FDSC market cap.
    """
    if yf is None:
        return PriceResult(
            ticker=ticker, last_price_usd=None, last_price_as_of=None,
            status="failed", error="yfinance not installed",
        )
    try:
        df = yf.Ticker(ticker).history(period=f"{lookback_days}d", auto_adjust=True)
    except Exception as e:                                       # noqa: BLE001
        return PriceResult(ticker, None, None, status="failed",
                           error=f"{type(e).__name__}: {e}")

    if df is None or df.empty or "Close" not in df.columns:
        return PriceResult(ticker, None, None, status="partial",
                           error="empty yfinance response")

    # Drop NaN closes; pick the most recent.
    closes = df["Close"].dropna()
    if closes.empty:
        return PriceResult(ticker, None, None, status="partial",
                           error="no non-null close in window")

    latest_ts = closes.index[-1]
    return PriceResult(
        ticker=ticker,
        last_price_usd=float(closes.iloc[-1]),
        last_price_as_of=_to_iso_date(latest_ts),
        status="ok", error=None,
    )


def fetch_last_close_batch(tickers: list[str]) -> dict[str, PriceResult]:
    """Batched fetch using `yf.download` — one HTTP request for the whole list.

    Falls back to per-ticker fetch_last_close if the batch shape isn't usable.
    """
    if not tickers:
        return {}
    if yf is None:
        return {t: PriceResult(t, None, None, "failed",
                               "yfinance not installed") for t in tickers}
    if len(tickers) == 1:
        return {tickers[0]: fetch_last_close(tickers[0])}

    try:
        df = yf.download(
            tickers=" ".join(tickers),
            period="7d", auto_adjust=True,
            group_by="ticker", progress=False, threads=True,
        )
    except Exception as e:                                       # noqa: BLE001
        log.warning("batch yfinance failed: %s; falling back per-ticker", e)
        return {t: fetch_last_close(t) for t in tickers}

    out: dict[str, PriceResult] = {}
    for t in tickers:
        try:
            sub = df[t].dropna(how="all")
            closes = sub["Close"].dropna() if "Close" in sub.columns else None
            if closes is None or closes.empty:
                out[t] = PriceResult(t, None, None, "partial",
                                     "empty in batch response")
                continue
            latest_ts = closes.index[-1]
            out[t] = PriceResult(
                ticker=t,
                last_price_usd=float(closes.iloc[-1]),
                last_price_as_of=_to_iso_date(latest_ts),
                status="ok", error=None,
            )
        except Exception as e:                                   # noqa: BLE001
            out[t] = PriceResult(t, None, None, "failed",
                                 f"batch decode: {type(e).__name__}: {e}")
    return out
