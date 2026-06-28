"""ADR / dual-listing identity resolution (spec §5.2 "keep primary, link secondaries").

This is NOT a deletion. The cardinal rule (§0.2) forbids deleting a company for being a duplicate,
so we resolve identity BEFORE company rows exist: collapse the provider records of one real company
(same ISIN, else same normalized name) into a single canonical record keyed on its primary listing,
with the other listings recorded in ``secondary_listings``. No candidate is lost — the company is
retained via its primary listing — and Stage 0b therefore never needs an illegal "duplicate" delete.

Recall-safety: fields are UNIONED across the group (sector codes, indices, provenance), so a sector
code or seed-list membership carried only by a secondary listing still reaches the canonical record
and its Stage-0a nets.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .listings import normalize_name, primary_listing
from .models import ListingRecord

# Default primary-exchange preference when no record is explicitly flagged is_primary [BUILDER
# DECISION] — US main boards first, then large EU/Nordic venues. Tunable later via config.
DEFAULT_EXCHANGE_PRIORITY = [
    "NASDAQ", "NYSE", "NYSEAMERICAN",
    "STO", "OMX", "CPH", "HEL", "OSL",          # Nasdaq Nordic
    "EPA", "AMS", "XETRA", "FRA", "SIX", "LSE",  # Euronext / DB / SIX / LSE
]


def _group_key(rec: ListingRecord) -> str:
    return (rec.isin or "").strip().upper() or f"name:{normalize_name(rec.name)}"


def _rank(rec: ListingRecord, priority: list[str]) -> tuple:
    """Lower tuple sorts first → chosen as primary."""
    flagged = 0 if rec.is_primary else 1
    try:
        ex_rank = priority.index((rec.exchange or "").upper())
    except ValueError:
        ex_rank = len(priority)
    cap = rec.mktcap_usd_fd if rec.mktcap_usd_fd is not None else -1.0
    return (flagged, ex_rank, -cap)


def collapse(records: Iterable[ListingRecord],
             exchange_priority: Optional[list[str]] = None) -> list[ListingRecord]:
    """Resolve ADR/dual listings → one canonical record per company. Order-preserving by first sight."""
    priority = exchange_priority or DEFAULT_EXCHANGE_PRIORITY
    groups: dict[str, list[ListingRecord]] = {}
    order: list[str] = []
    for rec in records:
        key = _group_key(rec)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(rec)

    canonical: list[ListingRecord] = []
    for key in order:
        members = sorted(groups[key], key=lambda r: _rank(r, priority))
        primary = members[0]
        secondaries = members[1:]
        if secondaries:
            primary.secondary_listings = sorted(
                set(primary.secondary_listings)
                | {primary_listing(s) for s in secondaries}
            )
            # union recall-relevant fields from secondaries onto the primary
            for s in secondaries:
                primary.indices = sorted(set(primary.indices) | set(s.indices))
                primary.provenance = sorted(set(primary.provenance) | set(s.provenance))
                primary.sic = primary.sic or s.sic
                primary.gics_industry = primary.gics_industry or s.gics_industry
                primary.icb_equiv = primary.icb_equiv or s.icb_equiv
                primary.isin = primary.isin or s.isin
                primary.lei = primary.lei or s.lei
                if primary.mktcap_usd_fd is None:
                    primary.mktcap_usd_fd = s.mktcap_usd_fd
                # a company is live if ANY listing is live
                primary.is_live = primary.is_live or s.is_live
        canonical.append(primary)
    return canonical
