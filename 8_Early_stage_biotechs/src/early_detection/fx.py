"""FX normalization to USD (adapted from platform_discoverer/fx.py).

Prototype rates are static and illustrative [INF]; override via ``config.yaml`` ``fx.rates`` or pass a
provider's live rates. Unknown currency → ``None`` (treated as unknown cap → KEEP + flag, never a
delete). CAD is added for Phase-1 Canadian (TSX/TSXV) listings.
"""

from __future__ import annotations

from typing import Optional

# Approximate units-of-USD-per-1-unit-of-currency [INF]. Override in config for accuracy.
DEFAULT_RATES_TO_USD: dict[str, float] = {
    "USD": 1.0, "CAD": 0.73, "EUR": 1.08, "GBP": 1.27, "CHF": 1.12,
    "SEK": 0.095, "DKK": 0.145, "NOK": 0.093, "JPY": 0.0067, "KRW": 0.00075,
}


class FXConverter:
    def __init__(self, rates: Optional[dict[str, float]] = None):
        self.rates = {**DEFAULT_RATES_TO_USD, **{k.upper(): v for k, v in (rates or {}).items()}}

    def to_usd(self, amount: Optional[float], currency: Optional[str]) -> Optional[float]:
        """Convert to USD. Returns None for missing amount or unknown currency (→ unknown cap)."""
        if amount is None:
            return None
        rate = self.rates.get((currency or "USD").upper())
        if rate is None:
            return None
        return amount * rate
