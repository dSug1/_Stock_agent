"""Clear cached CUSIP resolutions that the pre-tightened filter
misclassified as equities (mostly ETFs and closed-end trusts), plus
every NULL row so the new securityType filter gets a chance to
re-classify CUSIPs that previously returned no match.

Run from 1_not_used/ with PYTHONPATH=src:

    python scripts/clear_etf_cusips.py
"""
from __future__ import annotations

from database.db import get_connection


# Known ETF / trust / bond tickers we've seen slip into active monitoring.
# Expand as new false positives surface in the diagnostic.
KNOWN_NON_EQUITY_TICKERS = [
    "GBTC", "USIG", "VIOO", "VOOV", "GLD", "SPY",
    "QQQ", "IWM", "XLF", "XLK", "XLV", "XLE",
    "IWB", "IJH", "IJR", "VTI", "VOO", "BND",
]


def main() -> int:
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(KNOWN_NON_EQUITY_TICKERS))
        deleted_known = conn.execute(
            f"DELETE FROM cusip_ticker_map WHERE ticker IN ({placeholders})",
            KNOWN_NON_EQUITY_TICKERS,
        ).rowcount
        conn.commit()
        print(
            f"Cleared {deleted_known} known ETF/bond CUSIP cache entries"
        )

        deleted_nulls = conn.execute(
            "DELETE FROM cusip_ticker_map WHERE ticker IS NULL"
        ).rowcount
        conn.commit()
        print(f"Cleared {deleted_nulls} NULL entries for re-resolution")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
