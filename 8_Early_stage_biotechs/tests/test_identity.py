"""M2 — identity resolution / entity_id minting offline tests."""

from __future__ import annotations

from early_detection.identity import mint_entity_id, normalize_name, reconcile
from early_detection.models import Listing


# ── minting cascade ──────────────────────────────────────────────────────────
def test_mint_cascade_prefers_lei():
    assert mint_entity_id(lei="abc", isin="US1", cik="1", ticker="X", country="US") == "lei:ABC"


def test_mint_falls_through_to_isin_then_cik_then_ticker():
    assert mint_entity_id(lei=None, isin="us0001", cik="1", ticker="X", country="US") == "isin:US0001"
    assert mint_entity_id(lei=None, isin=None, cik="1234", ticker="X", country="US") == "cik:0000001234"
    assert mint_entity_id(lei=None, isin=None, cik=None, ticker="acme", country="us") == "tkc:ACME|US"


def test_mint_none_without_any_hard_key():
    assert mint_entity_id(lei=None, isin=None, cik=None, ticker="ACME", country=None) is None
    assert mint_entity_id(lei=None, isin=None, cik=None, ticker=None, country=None) is None


def test_cik_zero_padded_and_deterministic():
    a = mint_entity_id(lei=None, isin=None, cik="1", ticker=None, country=None)
    b = mint_entity_id(lei=None, isin=None, cik="0000000001", ticker=None, country=None)
    assert a == b == "cik:0000000001"


# ── name normalization ───────────────────────────────────────────────────────
def test_normalize_folds_accents_and_strips_suffixes():
    assert normalize_name("Genmab A/S") == "genmab"
    assert normalize_name("Acrivon Therapeutics, Inc.") == "acrivon"
    assert normalize_name("Zealand Pharma A/S") == "zealand"
    assert normalize_name("BioNTech SE") == "biontech se" or normalize_name("BioNTech SE") == "biontech"


# ── reconcile grouping ───────────────────────────────────────────────────────
def _L(name, **kw):
    return Listing(name=name, **kw)


def test_dual_listing_collapses_by_shared_lei():
    us = _L("Acme Bio", ticker="ACME", exchange="NASDAQ", lei="LEI1", provenance=["edgar_us"])
    eu = _L("Acme Bio AG", ticker="ACM", exchange="XETRA", lei="LEI1", provenance=["gleif"])
    res = reconcile([us, eu])
    assert len(res.entities) == 1
    ent = next(iter(res.entities.values()))
    assert ent.entity_id == "lei:LEI1"
    assert set(ent.source_provenance) == {"edgar_us", "gleif"}
    assert res.stats["merged"] == 1


def test_different_hard_keys_stay_separate():
    a = _L("Alpha", ticker="A", exchange="N", cik="1")
    b = _L("Beta", ticker="B", exchange="N", cik="2")
    res = reconcile([a, b])
    assert len(res.entities) == 2
    assert res.queued == []


def test_m6_lei_and_edgar_cik_merge_via_shared_ticker_country():
    # THE bug this fix targets: M6 carries LEI+ticker (no CIK), EDGAR carries CIK+ticker (no LEI).
    # They must collapse to ONE entity via the shared ticker+country signal, and the entity keeps
    # both the priority-tier flag (from M6) and the CIK (from EDGAR).
    m6 = _L("Acrivon Therapeutics", ticker="ACRV", exchange="NASDAQ", country="US",
            lei="LEIACRV", in_existing_universe=True, provenance=["m6"])
    edgar = _L("Acrivon Therapeutics, Inc.", ticker="ACRV", exchange="Nasdaq", country="US",
               cik="0001", sic="2836", sector_normalized="therapeutics", provenance=["edgar_us"])
    res = reconcile([m6, edgar])
    assert len(res.entities) == 1
    ent = next(iter(res.entities.values()))
    assert ent.entity_id == "lei:LEIACRV"          # strongest key in the component
    assert ent.cik == "0000000001"                 # backfilled from EDGAR
    assert ent.in_existing_universe is True         # latched from M6
    assert ent.sector_code_normalized == "therapeutics"
    assert set(ent.source_provenance) == {"m6", "edgar_us"}


def test_conflicting_lei_merges_but_is_flagged():
    # two listings share a ticker+country (hard key) but disagree on LEI → merge + conflict review row
    a = _L("Acme", ticker="ACME", country="US", lei="LEI_A")
    b = _L("Acme", ticker="ACME", country="US", lei="LEI_B")
    res = reconcile([a, b])
    assert len(res.entities) == 1
    assert any(q.reason == "conflicting_lei" for q in res.queued)


def test_keyless_listing_is_queued_not_merged():
    # a name-only listing that matches an existing entity's name must NOT auto-merge
    keyed = _L("Acme Bio Inc", ticker="ACME", exchange="NASDAQ", cik="1")
    keyless = _L("Acme Bio", ticker=None, exchange=None)   # no hard key
    res = reconcile([keyed, keyless])
    assert len(res.entities) == 1                          # only the keyed one became an entity
    assert len(res.queued) == 1
    q = res.queued[0]
    assert q.reason in ("weak_name_match", "ambiguous_multi_match")
    assert q.candidate.get("name_match_entity_ids") == ["cik:0000000001"]


def test_keyless_no_name_match_is_no_key_match():
    keyless = _L("Totally Unknown Co", ticker=None, exchange=None)
    res = reconcile([keyless])
    assert len(res.entities) == 0
    assert res.queued[0].reason == "no_key_match"


def test_ambiguous_multi_name_match_flagged():
    # two distinct entities share a normalized name; a keyless third with that name is ambiguous
    e1 = _L("Acme Bio Inc", ticker="ACME", exchange="NASDAQ", cik="1")
    e2 = _L("Acme Bio Corp", ticker="ACM", exchange="TSX", cik="2")
    keyless = _L("Acme Bio", ticker=None, exchange=None)
    res = reconcile([e1, e2, keyless])
    assert len(res.entities) == 2
    assert res.queued[0].reason == "ambiguous_multi_match"
    assert len(res.queued[0].candidate["name_match_entity_ids"]) == 2


def test_in_existing_universe_latches_on_merge():
    m6 = _L("Acme Bio", ticker="ACME", exchange="NASDAQ", cik="1", in_existing_universe=True, provenance=["m6"])
    edgar = _L("Acme Bio Inc", ticker="ACME", exchange="NASDAQ", cik="1", provenance=["edgar_us"])
    res = reconcile([edgar, m6])   # order shouldn't matter
    ent = next(iter(res.entities.values()))
    assert ent.in_existing_universe is True
