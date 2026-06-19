"""LLM layer for 4_List_renderer (Phase 3).

All Claude calls route through `run_llm_task` (D24): one call path, a pluggable
`BillingContext` (v1 = `self`), and per-task cost/payer attribution in the
`llm_tasks` ledger — so future paid features (AI summaries) and payer models
(BYOK / corporate / advertiser) plug in without a refactor.
"""

from .billing import BillingContext, self_context, load_env
from .cost import estimate_cost, format_cost_panel
from .runner import run_llm_task

__all__ = [
    "BillingContext",
    "self_context",
    "load_env",
    "estimate_cost",
    "format_cost_panel",
    "run_llm_task",
]
