"""Identity resolution & deterministic entity_id minting (phase1 build spec §4, spec §2.3).

Groups provider ``Listing`` records into canonical entities by **union-find over shared hard keys**,
then mints each entity's id from the strongest key present in its component:

    LEI  >  ISIN  >  CIK  >  ticker+country

Union-find (not a single top-of-cascade key) is required because different providers carry different
key *types* for the same company: Module 6 seeds LEI/ISIN/ticker but no CIK; SEC EDGAR seeds CIK +
ticker but no LEI. Two listings that share **any** hard key are the same entity. The composite key is
``ticker+country`` (not ticker+exchange) because exchange labels differ across sources ("Nasdaq" vs
"NASDAQ") while country is stable — this is what actually stitches an M6 seed to its EDGAR row.

Discipline (§2.3): a listing with **no** hard key is never auto-merged on a name alone — it goes to the
reconciliation queue for review (a false merge is worse than a duplicate row). A component carrying two
distinct LEIs/ISINs is merged (a shared hard key links them) but a ``conflicting_lei`` review row is
recorded so the data conflict is visible.

Pure/offline — holds no DB handle; the caller (``universe.py``) persists the result.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .models import Entity, Listing, ReconRow
from .store import now_iso

# Key strength ranking for entity_id minting (lower = stronger).
_KEY_RANK = {"lei": 0, "isin": 1, "cik": 2, "tkc": 3}

# ── name normalization (mirrors Module 6's dedup idiom: accent-fold + strip corp suffixes) ──
_SUFFIXES = (
    "inc", "incorporated", "corp", "corporation", "co", "ltd", "limited", "llc", "plc",
    "sa", "ag", "nv", "ab", "as", "asa", "oyj", "spa", "kk", "gmbh", "holdings", "holding",
    "therapeutics", "pharmaceuticals", "pharma", "biosciences", "bioscience", "biotech",
    "biopharma", "sciences", "the",
)
_WS = re.compile(r"\s+")
_NONALNUM = re.compile(r"[^a-z0-9 ]+")


def normalize_name(name: str) -> str:
    """Accent-fold, lowercase, drop punctuation + common corporate/sector suffix words.

    Deliberately aggressive because the result is only ever used for a WEAK (queue-not-merge) match,
    so over-collapsing produces a review item, never a silent bad merge.
    """
    if not name:
        return ""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    # Collapse intra-token separators so "A/S", "A.S." → "as" (a known corporate suffix) rather
    # than splitting into orphan single letters that survive suffix-stripping.
    folded = folded.lower().replace("/", "").replace(".", "")
    folded = _NONALNUM.sub(" ", folded)
    tokens = [t for t in _WS.sub(" ", folded).strip().split(" ") if t and t not in _SUFFIXES]
    return " ".join(tokens)


def _norm_cik(cik: Optional[str]) -> Optional[str]:
    """Zero-pad a CIK to 10 digits (SEC canonical form); pass through falsy/non-numeric untouched."""
    if not cik:
        return None
    digits = re.sub(r"\D", "", str(cik))
    return digits.zfill(10) if digits else None


def hard_signals(lst: Listing) -> list[str]:
    """All hard-key signal strings a listing carries, e.g. ['lei:ABC', 'tkc:ACME|US'].

    These are the edges union-find joins on. A name is never a hard signal.
    """
    out: list[str] = []
    if lst.lei:
        out.append(f"lei:{lst.lei.strip().upper()}")
    if lst.isin:
        out.append(f"isin:{lst.isin.strip().upper()}")
    c = _norm_cik(lst.cik)
    if c:
        out.append(f"cik:{c}")
    if lst.ticker and lst.country:
        out.append(f"tkc:{lst.ticker.strip().upper()}|{lst.country.strip().upper()}")
    return out


def mint_entity_id(*, lei: Optional[str] = None, isin: Optional[str] = None, cik: Optional[str] = None,
                   ticker: Optional[str] = None, country: Optional[str] = None) -> Optional[str]:
    """Deterministic entity_id from the strongest available hard key of a single listing.

    Cascade: ``lei:`` > ``isin:`` > ``cik:`` > ``tkc:TICKER|COUNTRY``. ``None`` if no hard key.
    """
    lst = Listing(name="", lei=lei, isin=isin, cik=cik, ticker=ticker, country=country)
    return _mint_from_signals(hard_signals(lst))


def _mint_from_signals(signals: Iterable[str]) -> Optional[str]:
    """Pick the strongest signal (lowest rank; ties broken lexicographically) as the entity_id."""
    sigs = list(signals)
    if not sigs:
        return None
    return min(sigs, key=lambda s: (_KEY_RANK.get(s.split(":", 1)[0], 99), s))


# ── union-find ──────────────────────────────────────────────────────────────────

class _UF:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def add(self, x: str) -> None:
        self.parent.setdefault(x, x)

    def find(self, x: str) -> str:
        self.add(x)
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:      # path compression
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)   # deterministic root


# ── reconciliation over a batch of listings ─────────────────────────────────────

@dataclass
class ReconcileResult:
    entities: dict[str, Entity]                          # entity_id -> Entity
    listings: dict[str, list[Listing]]                   # entity_id -> contributing listings
    queued: list[ReconRow] = field(default_factory=list)  # unmatched/ambiguous, for manual review
    stats: dict[str, int] = field(default_factory=dict)


def _merge_into(acc: Entity, lst: Listing) -> Entity:
    """Fold a listing's fields into an accumulating entity (COALESCE-style: fill only blanks).

    Unions provenance; latches ``in_existing_universe`` True (M6 seed is authoritative for the tier);
    ``mktcap_unknown`` stays True only if *every* contributing listing lacked a cap.
    """
    prov = list(dict.fromkeys(list(acc.source_provenance) + list(lst.provenance)))
    return Entity(
        entity_id=acc.entity_id,
        legal_name=acc.legal_name or lst.name,
        common_name=acc.common_name,
        ticker_primary=acc.ticker_primary or lst.ticker,
        exchange_primary=acc.exchange_primary or lst.exchange,
        isin=acc.isin or lst.isin,
        lei=acc.lei or lst.lei,
        cik=acc.cik or _norm_cik(lst.cik),
        jurisdiction=acc.jurisdiction or lst.country,
        filer_type=acc.filer_type or lst.filer_type,
        sector_code_raw=acc.sector_code_raw or lst.sic or lst.gics_industry,
        sector_code_normalized=acc.sector_code_normalized or lst.sector_normalized,
        market_cap_usd=acc.market_cap_usd if acc.market_cap_usd is not None else lst.mktcap_usd,
        mktcap_unknown=acc.mktcap_unknown and (lst.mktcap_unknown or lst.mktcap_usd is None),
        in_existing_universe=acc.in_existing_universe or lst.in_existing_universe,
        is_live=acc.is_live or lst.is_live,
        source_provenance=prov,
    )


def _entity_from(entity_id: str, lst: Listing) -> Entity:
    return Entity(
        entity_id=entity_id,
        legal_name=lst.name,
        ticker_primary=lst.ticker,
        exchange_primary=lst.exchange,
        isin=lst.isin,
        lei=lst.lei,
        cik=_norm_cik(lst.cik),
        jurisdiction=lst.country,
        filer_type=lst.filer_type,
        sector_code_raw=lst.sic or lst.gics_industry,
        sector_code_normalized=lst.sector_normalized,
        market_cap_usd=lst.mktcap_usd,
        mktcap_unknown=lst.mktcap_unknown or lst.mktcap_usd is None,
        in_existing_universe=lst.in_existing_universe,
        is_live=lst.is_live,
        source_provenance=list(lst.provenance),
    )


def reconcile(listings: Iterable[Listing]) -> ReconcileResult:
    """Group listings into canonical entities by union-find over shared hard keys (§4).

    - Listings sharing ANY hard key (lei/isin/cik/ticker+country) collapse into one entity, whose id
      is the strongest key in the component. Solves the M6(LEI)↔EDGAR(CIK) split.
    - A keyless listing (no hard key) is queued for review — weak name match attached if one exists,
      never auto-merged (§2.3).
    - A component with conflicting LEIs/ISINs is kept merged (a shared hard key links it) but a
      ``conflicting_lei`` review row is recorded.
    """
    listings = list(listings)
    uf = _UF()
    keyed: list[tuple[Listing, list[str]]] = []
    keyless: list[Listing] = []

    for lst in listings:
        sigs = hard_signals(lst)
        if not sigs:
            keyless.append(lst)
            continue
        keyed.append((lst, sigs))
        for s in sigs:
            uf.add(s)
        for s in sigs[1:]:
            uf.union(sigs[0], s)

    # Group keyed listings by union-find root.
    comp: dict[str, list[Listing]] = defaultdict(list)
    comp_sigs: dict[str, set[str]] = defaultdict(set)
    for lst, sigs in keyed:
        root = uf.find(sigs[0])
        comp[root].append(lst)
        comp_sigs[root].update(sigs)

    entities: dict[str, Entity] = {}
    listing_groups: dict[str, list[Listing]] = {}
    name_index: dict[str, set[str]] = defaultdict(set)
    queued: list[ReconRow] = []
    stats = {"components": 0, "merged": 0, "queued_no_key": 0, "queued_weak_name": 0, "conflicts": 0}

    for root, group in comp.items():
        eid = _mint_from_signals(comp_sigs[root])
        assert eid is not None
        acc = _entity_from(eid, group[0])
        for lst in group[1:]:
            acc = _merge_into(acc, lst)
            stats["merged"] += 1
        entities[eid] = acc
        listing_groups[eid] = list(group)
        stats["components"] += 1
        for lst in group:
            nk = normalize_name(lst.name)
            if nk:
                name_index[nk].add(eid)
        # conflict detection: >1 distinct lei or isin in one component
        for kind in ("lei", "isin"):
            vals = {s.split(":", 1)[1] for s in comp_sigs[root] if s.startswith(kind + ":")}
            if len(vals) > 1:
                stats["conflicts"] += 1
                queued.append(ReconRow(candidate={"entity_id": eid, kind: sorted(vals)},
                                       reason="conflicting_lei", added_at=now_iso()))

    # keyless listings — weak by definition, never auto-merge (§2.3)
    for lst in keyless:
        nk = normalize_name(lst.name)
        cand = _listing_dict(lst)
        if not nk:
            queued.append(ReconRow(candidate=cand, reason="no_key_match", added_at=now_iso()))
            stats["queued_no_key"] += 1
            continue
        matches = name_index.get(nk, set())
        if matches:
            cand["name_match_entity_ids"] = sorted(matches)
            reason = "ambiguous_multi_match" if len(matches) > 1 else "weak_name_match"
            stats["queued_weak_name"] += 1
        else:
            reason = "no_key_match"
            stats["queued_no_key"] += 1
        queued.append(ReconRow(candidate=cand, reason=reason, added_at=now_iso()))

    stats["entities"] = len(entities)
    stats["queued"] = len(queued)
    return ReconcileResult(entities=entities, listings=listing_groups, queued=queued, stats=stats)


def _listing_dict(lst: Listing) -> dict:
    return {
        "name": lst.name, "ticker": lst.ticker, "exchange": lst.exchange, "country": lst.country,
        "isin": lst.isin, "lei": lst.lei, "cik": lst.cik, "sic": lst.sic,
        "provenance": list(lst.provenance),
    }
