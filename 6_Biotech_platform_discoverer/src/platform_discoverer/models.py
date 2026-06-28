"""Typed records passed between stages (spec §7 data model).

Frozen dataclasses so stages exchange typed records, not loose dicts. JSON-array columns
(``source_nets``, ``ta_tags``) are carried as ``list`` here and serialized to JSON at the DAO
boundary (``store.py``). Keep these in lock-step with the ``store._migration_1`` schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class Company:
    """A row of ``companies``. ``company_id`` is a stable hash(name|primary_listing)."""

    company_id: str
    name: str
    primary_ticker: Optional[str] = None
    exchange: Optional[str] = None
    country: Optional[str] = None
    isin: Optional[str] = None
    lei: Optional[str] = None
    mktcap_usd_fd: Optional[float] = None
    mktcap_unknown: bool = False
    source_nets: list[str] = field(default_factory=list)   # which Stage-0a nets hit
    ta_tags: list[str] = field(default_factory=list)        # taxonomy ids (§4)
    dev_stage: Optional[str] = None
    stage1_excluded: bool = False
    is_live: bool = True
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


@dataclass(frozen=True)
class Evidence:
    """A row of ``evidence`` — one harvested bundle per (company, source)."""

    company_id: str
    source: str                       # openalex|ctgov|edgar|patents|ir
    cursor: Optional[str] = None      # latest-seen marker for incremental re-runs
    payload_hash: Optional[str] = None
    payload: Any = None               # parsed object; serialized to payload_json
    fetched_at: Optional[str] = None


@dataclass(frozen=True)
class Score:
    """A row of ``scores`` — one rubric result per (company, run)."""

    company_id: str
    run_id: str
    model: str                        # which Claude tier produced this
    json: dict[str, Any] = field(default_factory=dict)   # full rubric JSON (§9.3)
    A: Optional[float] = None
    B: Optional[float] = None
    C: Optional[float] = None
    D: Optional[float] = None
    E: Optional[float] = None
    composite: Optional[float] = None
    confidence: Optional[float] = None


@dataclass(frozen=True)
class AuditEntry:
    """A row of ``audit_log`` — the spine of the cardinal-rule guarantee (§11)."""

    ts: str
    run_id: Optional[str]
    company_id: Optional[str]
    stage: str
    action: str                       # flagged|excluded|deleted|cut|scored
    reason: Optional[str]
    detail: Any = None                # serialized to detail_json
