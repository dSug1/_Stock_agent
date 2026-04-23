"""Module 4 — "Train hasn't left" filter and archetype ranking.

Two-phase pipeline:

- 4a (hard_filters):  cheap fund-side filters -> snapshot fetch -> snapshot filters
                      Output: survivors_{quarter}.parquet, hard_filter_rejections_{quarter}.parquet,
                              Outputs/filter_summary_{quarter}.html
- 4b (ranking):       incremental price fetch -> ratio matrix -> archetype match -> rank
                      Output: ranked_candidates_{quarter}.parquet,
                              Outputs/ranking_report_{quarter}.{html,xlsx}

Both phases share `data/prices.db` (snapshots + bars).

See:
- spec/module_4_spec.md         — locked specification
- spec/decisions_module_4.md    — D1-D18 design rationale
- spec/decisions.md § Module 4  — implementation decisions
"""
from .archetypes import load_archetypes, match_archetypes
from .hard_filters import (
    apply_cheap_filters,
    apply_snapshot_filters,
    fetch_or_reuse_snapshots,
    run_hard_filters,
)
from .prices import (
    fetch_incremental_prices,
    get_price_on_date,
    init_prices_db,
)
from .ranking import rank_universe
from .ratios import compute_ratios_for_ticker

__all__ = [
    "apply_cheap_filters",
    "apply_snapshot_filters",
    "compute_ratios_for_ticker",
    "fetch_incremental_prices",
    "fetch_or_reuse_snapshots",
    "get_price_on_date",
    "init_prices_db",
    "load_archetypes",
    "match_archetypes",
    "rank_universe",
    "run_hard_filters",
]
