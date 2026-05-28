"""Module 6.5 — prefunded-warrant count estimator (v1 heuristic).

Heuristic per spec §4.3:

  prefunded_warrants_count(ticker) =
      SUM(capital_raises.shares_issued
          WHERE ticker = ?
            AND raise_type = 'pfw'
            AND filing_date >= today − lookback_days)

Known limitation: PFWs may have been exercised between filing and the
snapshot; we have no automated way to detect this without parsing 10-Q
footnotes. v1 accepts the over-count and tags `pfw_source` so users
know the provenance. v2 = footnote parsing.

A `pfw_share_dilution_warning` boolean is set when estimated PFW count
exceeds `dilution_warning_pct` of `basic_shares_count` — Claude reads
this in the pack and can call out PFW overhang in its thesis.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


DEFAULT_LOOKBACK_DAYS = 730               # 2-year window
DEFAULT_DILUTION_WARNING_PCT = 25.0       # PFW count > 25% of basic_shares triggers warning


@dataclass
class PfwEstimate:
    ticker: str
    prefunded_warrants_count: Optional[int]
    pfw_source: str                                 # 'capital_raises_sum_2yr' | 'no_raises' | 'no_data'
    n_pfw_raises: int
    pfw_share_dilution_warning: Optional[bool]      # None when basic_shares missing


def estimate_pfw(
    ticker: str,
    pfw_raises: list[dict],
    basic_shares_count: Optional[int],
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    today: Optional[dt.date] = None,
    dilution_warning_pct: float = DEFAULT_DILUTION_WARNING_PCT,
) -> PfwEstimate:
    """Sum `shares_issued` for PFW raises within the lookback window.

    `pfw_raises` is the per-ticker subset of capital_raises with
    raise_type='pfw' (caller supplies the filter — keeps this function
    pure / sqlite-free for unit testing).
    """
    today = today or dt.date.today()
    cutoff = (today - dt.timedelta(days=lookback_days)).isoformat()

    in_window = [
        r for r in pfw_raises
        if (r.get("filing_date") or "") >= cutoff
        and r.get("shares_issued") is not None
    ]

    if not in_window:
        return PfwEstimate(
            ticker=ticker,
            prefunded_warrants_count=0 if pfw_raises is not None else None,
            pfw_source="no_raises" if pfw_raises is not None else "no_data",
            n_pfw_raises=0,
            pfw_share_dilution_warning=False if basic_shares_count else None,
        )

    total = sum(int(r["shares_issued"]) for r in in_window)
    warning: Optional[bool] = None
    if basic_shares_count and basic_shares_count > 0:
        pct = 100.0 * total / basic_shares_count
        warning = pct >= dilution_warning_pct

    return PfwEstimate(
        ticker=ticker,
        prefunded_warrants_count=total,
        pfw_source="capital_raises_sum_2yr",
        n_pfw_raises=len(in_window),
        pfw_share_dilution_warning=warning,
    )
