"""M18/M19 — Wikidata universe providers (Nordic + Europe) offline tests (injected SPARQL; no network)."""

from __future__ import annotations

from early_detection.clients import wikidata
from early_detection.providers import canada, europe, nordic


def _binding(**kv):
    return {k: {"value": v} for k, v in kv.items() if v is not None}


_PAYLOAD = {"results": {"bindings": [
    # has ISIN + LEI → admitted
    _binding(c="http://www.wikidata.org/entity/Q1", cLabel="Zealand Pharma", isin="DK0060257814",
             lei="LEIZEAL", countryLabel="Denmark", exchangeLabel="Nasdaq Copenhagen"),
    # ticker only → admitted
    _binding(c="http://www.wikidata.org/entity/Q2", cLabel="Camurus", ticker="CAMX",
             countryLabel="Sweden"),
    # duplicate entity URI (second venue) → deduped
    _binding(c="http://www.wikidata.org/entity/Q1", cLabel="Zealand Pharma", isin="US...ADR",
             countryLabel="Denmark"),
    # name only, no identifier → dropped (would queue keyless)
    _binding(c="http://www.wikidata.org/entity/Q3", cLabel="Obscure Bio", countryLabel="Norway"),
    # LEI ONLY (a private, unlisted company like LEO Pharma) → dropped (LEI ≠ listed)
    _binding(c="http://www.wikidata.org/entity/Q4", cLabel="Private Pharma", lei="LEIPRIV",
             countryLabel="Denmark"),
]}}


def test_rows_flattens_bindings():
    r = wikidata.rows(_PAYLOAD)
    assert r[0]["cLabel"] == "Zealand Pharma" and r[0]["isin"] == "DK0060257814"
    assert "isin" not in r[1]                       # Camurus has no isin binding


def test_nordic_provider_maps_and_filters():
    listings = nordic.load_nordic_listings(query_fn=lambda q, **kw: _PAYLOAD)
    names = [l.name for l in listings]
    assert names == ["Zealand Pharma", "Camurus"]   # deduped; keyless + LEI-only dropped
    z = listings[0]
    assert z.isin == "DK0060257814" and z.lei == "LEIZEAL" and z.country == "DK"
    assert z.exchange == "Nasdaq Copenhagen" and z.sector_normalized == "therapeutics"
    assert z.mktcap_unknown is True and z.provenance == ["wikidata_nordic"]
    assert listings[1].ticker == "CAMX" and listings[1].country == "SE"


def test_europe_provider_uses_shared_builder_and_iso_map():
    payload = {"results": {"bindings": [
        _binding(c="http://www.wikidata.org/entity/Q9", cLabel="Immunocore", isin="US45258D1054",
                 countryLabel="United Kingdom"),
        _binding(c="http://www.wikidata.org/entity/Q10", cLabel="Argenx", isin="NL0010832176",
                 countryLabel="Belgium"),
    ]}}
    listings = europe.load_europe_listings(query_fn=lambda q, **kw: payload)
    assert [l.name for l in listings] == ["Immunocore", "Argenx"]
    assert listings[0].country == "UK" and listings[1].country == "BE"   # United Kingdom → UK
    assert all(l.provenance == ["wikidata_europe"] for l in listings)


def test_canada_wikidata_provider():
    payload = {"results": {"bindings": [
        _binding(c="http://www.wikidata.org/entity/Q7", cLabel="Defence Therapeutics", isin="CA24463V1013",
                 countryLabel="Canada"),
    ]}}
    listings = canada.load_canada_wikidata_listings(query_fn=lambda q, **kw: payload)
    assert [l.name for l in listings] == ["Defence Therapeutics"]
    assert listings[0].country == "CA" and listings[0].provenance == ["wikidata_canada"]


def test_providers_fail_soft_and_empty():
    def boom(q, **kw):
        raise RuntimeError("wikidata down")
    assert nordic.load_nordic_listings(query_fn=boom) == []
    assert europe.load_europe_listings(query_fn=lambda q, **kw: {}) == []
    assert canada.load_canada_wikidata_listings(query_fn=boom) == []
