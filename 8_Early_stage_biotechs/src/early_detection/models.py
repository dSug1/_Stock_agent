"""Typed records exchanged between universe stages (Phase-1 data model).

Frozen dataclasses so providers/identity/store pass typed records, not loose dicts. JSON-array
columns (``source_provenance``, ``raw``) are carried as ``list``/``dict`` here and serialized to JSON
at the DAO boundary (``store.py``). Keep these in lock-step with ``store._migration_1``.

See ``spec/phase1_universe_build_spec.md`` §2 (schema) and §2.3 of the overall spec (canonical entity).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# Normalized sector taxonomy (spec §2.3 sector_code_normalized).
SECTORS = ("therapeutics", "diagnostics", "tools_platform", "devices", "agbio", "other")


@dataclass
class Listing:
    """Raw provider output for one tradable listing, BEFORE identity resolution (§3 input).

    Providers (m6_seed, edgar_us, edgar_canada, gleif) emit these; ``identity.reconcile`` groups
    them into ``Entity`` rows under one ``entity_id``. Mutable so reconciliation can merge fields
    across a listing group.
    """

    name: str
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    country: Optional[str] = None
    isin: Optional[str] = None
    lei: Optional[str] = None
    cik: Optional[str] = None                 # SEC CIK, zero-padded 10 (the key M6 lacks)
    mic: Optional[str] = None                 # ISO 10383 market identifier code, if known
    sic: Optional[str] = None                 # source-native SIC (US)
    gics_industry: Optional[str] = None
    sector_normalized: Optional[str] = None   # one of SECTORS (mapped by provider/config)
    filer_type: Optional[str] = None          # domestic / FPI / other
    mktcap_usd: Optional[float] = None
    mktcap_unknown: bool = False
    is_primary: bool = False
    is_live: bool = True
    in_existing_universe: bool = False        # True when sourced from Module 6 (§2.4 priority tier)
    provenance: list[str] = field(default_factory=list)   # e.g. ["m6"], ["edgar_us"]


@dataclass(frozen=True)
class Entity:
    """A row of ``entity`` — canonical company after identity resolution (spec §2.3).

    ``entity_id`` is minted deterministically from the strongest available hard key
    (``lei:…`` > ``isin:…`` > ``cik:…`` > ``tkx:TICKER|EXCHANGE``), so re-runs are idempotent.
    """

    entity_id: str
    legal_name: str
    common_name: Optional[str] = None
    ticker_primary: Optional[str] = None
    exchange_primary: Optional[str] = None
    isin: Optional[str] = None
    lei: Optional[str] = None
    cik: Optional[str] = None
    jurisdiction: Optional[str] = None            # ISO country
    filer_type: Optional[str] = None
    sector_code_raw: Optional[str] = None         # source-native (SIC / GICS)
    sector_code_normalized: Optional[str] = None  # one of SECTORS
    market_cap_usd: Optional[float] = None
    mktcap_ccy: Optional[str] = None              # native currency of the cap before USD conversion
    mktcap_unknown: bool = False
    below_floor: bool = False                     # known cap < floor → excluded from active universe (flag, not delete)
    ipo_date: Optional[str] = None                # listing first-trade date (ISO); future age signal
    in_existing_universe: bool = False
    is_live: bool = True
    source_provenance: list[str] = field(default_factory=list)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    enriched_at: Optional[str] = None             # when market data was last fetched
    academic_affiliations: list[str] = field(default_factory=list)   # §5.1 Claude extraction output
    founder_extracted_at: Optional[str] = None
    founder_prompt_version: Optional[str] = None  # skip-cache key (a prompt bump re-opens the entity)


@dataclass(frozen=True)
class SignalRecord:
    """A row of ``signal`` — spec §3 shared table. Created in schema v1, unused until Phase 2."""

    signal_id: str
    signal_type: str
    source: str
    entity_id: Optional[str] = None           # nullable — signal may precede entity match
    raw_payload: Any = None                   # serialized to raw_payload_json
    detected_at: Optional[str] = None
    event_date: Optional[str] = None          # date of the underlying event, not detection
    language: Optional[str] = None            # ISO code


@dataclass(frozen=True)
class ReconRow:
    """A row of ``reconciliation_queue`` — unmatched/ambiguous entity flagged for review (§2.3).

    Never force-merge: a possible duplicate that no hard key resolves lands here rather than being
    silently merged, because a false merge is worse than a duplicate row.
    """

    candidate: Any                            # serialized to candidate_json
    reason: str                               # no_key_match | ambiguous_multi_match | conflicting_lei
    added_at: Optional[str] = None
    resolved: bool = False


@dataclass(frozen=True)
class AuditEntry:
    """A row of ``audit_log`` — provenance spine (mirrors Module 6's audit table)."""

    ts: str
    stage: str
    action: str                               # admitted | merged | flagged | queued | refreshed
    run_id: Optional[str] = None
    entity_id: Optional[str] = None
    reason: Optional[str] = None
    detail: Any = None                        # serialized to detail_json
