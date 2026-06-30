"""Forward-catalyst calendar provider (Stage 2): scheduled/upcoming events only (spec Decision G).

Returns *scheduled* dates (PDUFA decisions, data-readout windows, earnings dates) — never the result of
a past event (a-posteriori, out of scope). Provider DEFERRED (FDA calendar / clinicaltrials.gov / earnings
calendar). Isolated + **FAIL-OPEN**: empty when no provider is wired.
"""

from __future__ import annotations


def upcoming(ticker: str, asof: str | None = None, horizon_days: int = 90) -> list[tuple]:
    """Upcoming scheduled catalysts as ``(event_date, kind, note, source)`` tuples.

    ``event_date`` is ISO ``YYYY-MM-DD``; ``kind`` in {pdufa, readout, earnings, ...}. ``[]`` when no
    provider is wired (fail-open).
    """
    return []
