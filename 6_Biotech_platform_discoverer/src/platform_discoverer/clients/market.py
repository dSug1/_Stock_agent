"""yfinance per-ticker enrichment — market cap, sector, country, liveness.

LOCAL-ONLY (yfinance ToS) — fine for the prototype, swap for a licensed vendor before public deploy.
Fail-open: missing yfinance or any fetch error → None, so the company is kept and flagged
``mktcap_unknown`` rather than dropped (recall-safe, cardinal rule §0.2).

**Market cap is BASIC (shares × price) as Yahoo reports it — pre-funded warrants / full dilution are
NOT included at this stage.** That refinement (FD/PFW share counts cross-checked against filings) is a
deferred TODO; the `mktcap_usd_fd` column keeps its name but currently carries the basic cap.
"""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)


def fetch_ticker_info(ticker: str) -> Optional[dict]:
    """Return {mktcap_native, currency, sector, industry, country, name, is_live} or None.

    ``mktcap_native`` is in the listing currency (``currency``); the caller converts to USD via
    ``fx.FXConverter``. Liveness heuristic: a dead/delisted ticker returns no cap and no live price.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed; market enrichment disabled")
        return None
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:  # noqa: BLE001 — fail-open per spec §5.2
        log.debug("yfinance enrich failed for %s: %s", ticker, exc)
        return None

    cap = info.get("marketCap")
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    return {
        "mktcap_native": float(cap) if cap else None,   # BASIC cap (no PFW) — deferred refinement
        "currency": info.get("currency"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "name": info.get("longName") or info.get("shortName"),
        "business_description": info.get("longBusinessSummary"),   # Stage 1 (M3) tags on this
        "is_live": bool(cap) or bool(price),
    }
