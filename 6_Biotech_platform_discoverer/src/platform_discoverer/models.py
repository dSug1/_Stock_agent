"""Typed records passed between stages (spec §7 data model).

Frozen dataclasses so stages exchange typed records, not loose dicts. JSON-array columns
(``source_nets``, ``ta_tags``) are carried as ``list`` here and serialized to JSON at the DAO
boundary (``store.py``). Keep these in lock-step with the ``store._migration_1`` schema.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ListingRecord:
    """Raw provider output for one listing, BEFORE identity resolution (Stage 0a input).

    Providers (seed CSV, market-data vendor, yfinance) emit these; ``dedup.collapse`` resolves
    ADR/dual listings into one canonical record per company, then Stage 0a admits union-of-nets
    members as ``Company`` rows. Mutable so dedup can merge fields across a listing group.
    """

    name: str
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    country: Optional[str] = None
    sic: Optional[str] = None
    gics_industry: Optional[str] = None
    icb_equiv: Optional[str] = None
    indices: list[str] = field(default_factory=list)
    isin: Optional[str] = None
    lei: Optional[str] = None
    mktcap_native: Optional[float] = None
    currency: Optional[str] = None
    shares_fd: Optional[float] = None
    mktcap_usd_fd: Optional[float] = None     # set if the provider already gives USD
    is_primary: bool = False
    is_live: bool = True
    provenance: list[str] = field(default_factory=list)   # e.g. ["seed_list"], ["yfinance"]
    secondary_listings: list[str] = field(default_factory=list)  # linked ADR/dual listings


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
    business_description: Optional[str] = None   # yfinance longBusinessSummary (Stage 1 tags on it)
    sector: Optional[str] = None
    industry: Optional[str] = None
    ipo_date: Optional[str] = None               # listing first-trade date (ISO); Stage-5 age signal
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
    config_hash: Optional[str] = None    # scoring-config hash at score time (§12 incremental re-runs)


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
