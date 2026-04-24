"""Module 5 — ticker selection.

Per D21/D27/D28/D29, v1 applies **only** the explicit denylist and
`include_unclassified: false` toggle. The `composite_best_min`,
sector/industry allowlists, market-cap caps, and rescue clause are
reserved stubs — they activate later, inside Module 6 at query time.

This module returns:
  - `to_enrich_df`: tickers that get packs built
  - `exclusions_df`: rejected rows with `reason` and `detail` audit columns
"""
from __future__ import annotations

import logging
from typing import Iterable

import pandas as pd

log = logging.getLogger(__name__)


_EXCLUSION_COLS = ["ticker", "rank", "archetype", "composite_best", "reason", "detail"]


def apply_selection(
    ranked_df: pd.DataFrame,
    selection_config: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Partition `ranked_df` into (to_enrich, exclusions).

    In v1 only the explicit denylist and the `include_unclassified` toggle
    filter rows. Everything else passes through.
    """
    denylist: set[str] = set(_as_str_set(selection_config.get("denylist_tickers") or []))
    include_unclassified = bool(selection_config.get("include_unclassified", True))

    exclusions: list[dict] = []
    keep_mask = pd.Series(True, index=ranked_df.index)

    if denylist:
        denylist_mask = ranked_df["ticker"].astype(str).isin(denylist)
        for _, row in ranked_df[denylist_mask].iterrows():
            exclusions.append({
                "ticker": row.get("ticker"),
                "rank": row.get("rank"),
                "archetype": row.get("archetype"),
                "composite_best": row.get("composite_best"),
                "reason": "denylist",
                "detail": "in enrichment.selection.denylist_tickers",
            })
        keep_mask &= ~denylist_mask

    if not include_unclassified:
        unc_mask = ranked_df["archetype"].astype(str).eq("unclassified")
        for _, row in ranked_df[unc_mask & keep_mask].iterrows():
            exclusions.append({
                "ticker": row.get("ticker"),
                "rank": row.get("rank"),
                "archetype": row.get("archetype"),
                "composite_best": row.get("composite_best"),
                "reason": "unclassified_excluded",
                "detail": "enrichment.selection.include_unclassified = false",
            })
        keep_mask &= ~unc_mask

    to_enrich = ranked_df[keep_mask].copy().reset_index(drop=True)
    exclusions_df = (
        pd.DataFrame(exclusions, columns=_EXCLUSION_COLS)
        if exclusions
        else pd.DataFrame(columns=_EXCLUSION_COLS)
    )

    log.info(
        "Selection: %d ranked -> %d enriched (%d excluded by denylist/unclassified)",
        len(ranked_df), len(to_enrich), len(exclusions_df),
    )
    return to_enrich, exclusions_df


def _as_str_set(xs: Iterable) -> set[str]:
    return {str(x).strip() for x in xs if str(x).strip()}
