"""yfinance per-ticker market-cap enrichment (adapted from platform_discoverer/clients/market.py).

LOCAL-ONLY (yfinance ToS) — fine for the prototype, swap for a licensed vendor before public deploy.
Fail-open: missing yfinance or any fetch error → None, so the entity is kept + flagged
``mktcap_unknown`` rather than dropped (recall-safe).

Market cap is BASIC (shares × price) as Yahoo reports it — pre-funded warrants / full dilution are
NOT included. Cap is in the listing currency; the caller converts to USD via ``fx.FXConverter``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


def _epoch_to_iso_date(epoch: Optional[float]) -> Optional[str]:
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _first_trade_date(info: dict) -> Optional[str]:
    """Listing first-trade date as ISO 'YYYY-MM-DD' (a future age signal). yfinance moved this field
    across versions — try seconds, then milliseconds, then the expected-IPO string."""
    for key in ("firstTradeDateEpochUtc", "firstTradeDateUtc"):
        iso = _epoch_to_iso_date(info.get(key))
        if iso:
            return iso
    ms = info.get("firstTradeDateMilliseconds")
    if ms:
        iso = _epoch_to_iso_date(float(ms) / 1000.0)
        if iso:
            return iso
    expected = (info.get("ipoExpectedDate") or "").strip()
    if len(expected) == 10 and expected[4] == "-":
        return expected
    return None


def fetch_market_cap(ticker: str) -> Optional[dict]:
    """Return {mktcap_native, currency, ipo_date, is_live} for a ticker, or None on any failure.

    ``mktcap_native`` is in ``currency``; caller converts to USD. Fail-open by design.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed; market enrichment disabled")
        return None
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.debug("yfinance enrich failed for %s: %s", ticker, exc)
        return None

    cap = info.get("marketCap")
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    return {
        "mktcap_native": float(cap) if cap else None,
        "currency": info.get("currency"),
        "ipo_date": _first_trade_date(info),
        "is_live": bool(cap) or bool(price),
    }
