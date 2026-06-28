"""M2b tests — directory clients (SEC SIC, Wikidata), Yahoo-symbol mapping, provider assembly.

All offline: parsers run on captured-shape sample payloads; network fetchers are monkeypatched. Live
schema validation (the actual SEC atom / Wikidata JSON over the wire) happens on the first real
`--universe` run — these tests pin the parsing + composition logic, not the endpoints.
"""

import xml.etree.ElementTree as ET

import pytest

from platform_discoverer import directory
from platform_discoverer.clients import sec_sic, wikidata
from platform_discoverer.models import ListingRecord

# ── SEC: cik↔ticker map ──────────────────────────────────────────────────────

def test_parse_cik_exchange():
    payload = {"fields": ["cik", "name", "ticker", "exchange"],
               "data": [[1844642, "Acrivon Therapeutics, Inc.", "ACRV", "Nasdaq"],
                        [1819133, "Tango Therapeutics, Inc.", "TNGX", "Nasdaq"],
                        [999, "No Ticker Co", "", "Nasdaq"]]}
    m = sec_sic.parse_cik_exchange(payload)
    assert m[1844642] == ("ACRV", "Nasdaq", "Acrivon Therapeutics, Inc.")
    assert 999 not in m                       # no ticker -> excluded (not a listed equity)


def test_parse_cik_exchange_prefers_common_over_warrant():
    # same CIK lists common + warrant; the warrant must NOT clobber the common (the Ginkgo DNA bug)
    payload = {"fields": ["cik", "name", "ticker", "exchange"],
               "data": [[1830214, "Ginkgo Bioworks", "DNABW", "NYSE"],   # warrant first
                        [1830214, "Ginkgo Bioworks", "DNA", "NYSE"]]}     # common second
    assert sec_sic.parse_cik_exchange(payload)[1830214][0] == "DNA"
    # order-independent: common first, warrant second
    payload["data"].reverse()
    assert sec_sic.parse_cik_exchange(payload)[1830214][0] == "DNA"


# ── SEC: browse-edgar atom ───────────────────────────────────────────────────

# Real browse-edgar multi-result shape: names hit the SEC "ARRAY(0x..)" bug; the CIK is reliably in
# the <cik> element, the <id> urn:tag, and the link href. The parser extracts CIK regardless.
_ATOM = """<?xml version='1.0' encoding='ISO-8859-1' ?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <entry title='ARRAY(0x5609b1ea1838)'>
    <content type='text/xml'>
      <company-info name='ARRAY(0x5609b1e83488)'>
        <cik>0001844642</cik>
        <sic>2836</sic>
      </company-info>
    </content>
    <id>urn:tag:www.sec.gov:cik=0001844642</id>
    <link href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0001844642&amp;owner=exclude' type='text/html' />
  </entry>
  <entry title='ARRAY(0x5609b1ea9999)'>
    <content type='text/xml'>
      <company-info name='ARRAY(0x5609b1e89999)'>
        <cik>0001819133</cik>
        <sic>2836</sic>
      </company-info>
    </content>
    <id>urn:tag:www.sec.gov:cik=0001819133</id>
    <link href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0001819133' type='text/html' />
  </entry>
</feed>"""


def test_parse_browse_atom_extracts_ciks_despite_array_bug():
    rows = sec_sic.parse_browse_atom(ET.fromstring(_ATOM))
    # names are the ARRAY bug; only the CIKs are reliable (and that's all build_us_records needs)
    assert [cik for cik, _ in rows] == [1844642, 1819133]


def test_build_us_records_joins_sic_and_ticker(monkeypatch):
    monkeypatch.setattr(sec_sic._net, "safe_json", lambda *a, **k: {
        "fields": ["cik", "name", "ticker", "exchange"],
        "data": [[1844642, "Acrivon Therapeutics", "ACRV", "Nasdaq"]]})
    monkeypatch.setattr(sec_sic, "enumerate_sic",
                        lambda sic, **k: [(1844642, "ACRIVON THERAPEUTICS")] if sic == "2836" else [])
    recs = sec_sic.build_us_records(["2836", "2834"])
    assert len(recs) == 1
    r = recs[0]
    assert r.ticker == "ACRV" and r.country == "US" and r.sic == "2836" and r.provenance == ["sector"]


# ── Wikidata SPARQL ──────────────────────────────────────────────────────────

def test_parse_sparql():
    payload = {"results": {"bindings": [
        {"companyLabel": {"value": "Genmab"}, "ticker": {"value": "GMAB"},
         "exchangeLabel": {"value": "Nasdaq Copenhagen"}, "countryLabel": {"value": "Denmark"},
         "industryLabel": {"value": "pharmaceutical industry"}},
        {"companyLabel": {"value": "BioArctic"}, "ticker": {"value": "BIOA B"},
         "exchangeLabel": {"value": "Nasdaq Stockholm"}, "countryLabel": {"value": "Sweden"},
         "industryLabel": {"value": "biotechnology"}},
        {"companyLabel": {"value": "NoTicker"}},        # missing ticker -> dropped
    ]}}
    rows = wikidata.parse_sparql(payload)
    assert [r["name"] for r in rows] == ["Genmab", "BioArctic"]


def test_regions_to_qids_expands_eu_and_dedups():
    qids = wikidata.regions_to_qids(["US", "SE", "DK", "EU"])
    assert "Q34" in qids and "Q35" in qids and "Q183" in qids       # SE, DK, DE(via EU)
    assert len(qids) == len(set(qids))                              # de-duplicated
    assert "US" not in str(qids)                                    # US not a Wikidata country here


def test_gics_tag_from_industry():
    assert wikidata._gics_from_industry("pharmaceutical industry") == "Pharmaceuticals"
    assert wikidata._gics_from_industry("biotechnology") == "Biotechnology"
    assert wikidata._gics_from_industry(None) == "Biotechnology"


def test_build_eu_records_tags_sector_net(monkeypatch):
    monkeypatch.setattr(wikidata._net, "safe_json", lambda *a, **k: {"results": {"bindings": [
        {"companyLabel": {"value": "Genmab"}, "ticker": {"value": "GMAB"},
         "exchangeLabel": {"value": "Nasdaq Copenhagen"}, "countryLabel": {"value": "Denmark"},
         "industryLabel": {"value": "pharmaceutical industry"}}]}})
    recs = wikidata.build_eu_records(["DK"])
    assert len(recs) == 1
    assert recs[0].gics_industry == "Pharmaceuticals" and recs[0].provenance == ["sector"]


# ── Yahoo-symbol mapping ─────────────────────────────────────────────────────

@pytest.mark.parametrize("country,exchange,ticker,expected", [
    ("US", "Nasdaq", "ACRV", "ACRV"),                          # US: as-is
    ("Denmark", "Nasdaq Copenhagen", "GMAB", "GMAB.CO"),       # exchange-label suffix
    ("Sweden", "Nasdaq Stockholm", "BIOA B", "BIOA-B.ST"),     # space normalized
    ("Germany", None, "BNTX", "BNTX.DE"),                      # fallback to country
    ("Switzerland", "SIX Swiss Exchange", "IDIA", "IDIA.SW"),
])
def test_yahoo_symbol(country, exchange, ticker, expected):
    rec = ListingRecord(name="X", ticker=ticker, exchange=exchange, country=country)
    assert directory.yahoo_symbol(rec) == expected


def test_yahoo_symbol_none_without_ticker():
    assert directory.yahoo_symbol(ListingRecord(name="X", ticker=None)) is None


# ── Directory provider composition ───────────────────────────────────────────

def _dir_config():
    return {
        "run": {"regions": ["US", "DK"]},
        "stage0a_nets": {"sector_codes": {"sic": ["2836"]},
                         "directory": {"sources": ["sec_us", "wikidata_eu"]}},
    }


def test_directory_provider_composes_sources_and_assigns_symbols(monkeypatch):
    monkeypatch.setattr(directory.sec_sic, "build_us_records",
                        lambda sics, **k: [ListingRecord(name="Acrivon", ticker="ACRV",
                                                         country="US", sic="2836",
                                                         provenance=["sector"])])
    monkeypatch.setattr(directory.wikidata, "build_eu_records",
                        lambda regions, **k: [ListingRecord(name="Genmab", ticker="GMAB",
                                                            exchange="Nasdaq Copenhagen",
                                                            country="Denmark", provenance=["sector"])])
    recs = directory.ListingDirectoryProvider(_dir_config()).fetch()
    # US ticker unchanged; non-US ticker gets its Yahoo suffix (so Stage 0b can enrich it)
    assert {r.ticker for r in recs} == {"ACRV", "GMAB.CO"}


def test_directory_does_not_enrich(monkeypatch):
    """Provider enumerates only — no yfinance/cap here (that's Stage 0b's job)."""
    monkeypatch.setattr(directory.sec_sic, "build_us_records",
                        lambda sics, **k: [ListingRecord(name="X", ticker="X", country="US",
                                                         sic="2836", provenance=["sector"])])
    monkeypatch.setattr(directory.wikidata, "build_eu_records", lambda regions, **k: [])
    recs = directory.ListingDirectoryProvider(_dir_config()).fetch(["US"])
    assert recs[0].mktcap_usd_fd is None      # enumerated, not enriched
