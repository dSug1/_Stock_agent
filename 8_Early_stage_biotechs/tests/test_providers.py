"""M3 — provider + SEC-client offline tests (no network; injected payloads + a fixture M6 store)."""

from __future__ import annotations

import sqlite3

import defusedxml.ElementTree as ET
import pytest

from early_detection.clients import sec
from early_detection.providers.m6_seed import load_m6_listings

# ── SEC cik↔exchange parser ──────────────────────────────────────────────────
_CIK_EXCHANGE_PAYLOAD = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [
        [1, "Acme Bio Inc", "ACME", "Nasdaq"],
        [1, "Acme Bio Inc", "ACMEW", "Nasdaq"],       # warrant — must NOT clobber common
        [2, "Beta Tx", "BETA", "NYSE"],
        [3, "No Ticker Co", "", "Nasdaq"],            # no ticker → dropped
    ],
}


def test_parse_cik_exchange_prefers_common_over_warrant():
    out = sec.parse_cik_exchange(_CIK_EXCHANGE_PAYLOAD)
    assert out[1][0] == "ACME"          # not ACMEW
    assert out[2] == ("BETA", "NYSE", "Beta Tx")
    assert 3 not in out                 # tickerless dropped


_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>ACME BIO INC</title>
    <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000000001"/>
  </entry>
  <entry>
    <title>BETA TX</title>
    <content><cik>2</cik></content>
  </entry>
</feed>"""


def test_parse_browse_atom_extracts_cik_from_link_and_element():
    root = ET.fromstring(_ATOM)
    got = sec.parse_browse_atom(root)
    assert (1, "ACME BIO INC") in got
    assert (2, "BETA TX") in got


def test_build_us_listings_populates_cik_and_sector(monkeypatch):
    # stub enumerate_sic so no network; two CIKs under 2834
    monkeypatch.setattr(sec, "enumerate_sic",
                        lambda sic, **kw: [(1, "ACME BIO INC"), (2, "BETA TX")])
    listings = sec.build_us_listings(
        {"2834": "therapeutics"},
        cik_map=sec.parse_cik_exchange(_CIK_EXCHANGE_PAYLOAD),
        submissions_lookup=lambda cik: None,   # offline: no submissions fallback network call
    )
    by_ticker = {l.ticker: l for l in listings}
    assert by_ticker["ACME"].cik == "0000000001"
    assert by_ticker["ACME"].sector_normalized == "therapeutics"
    assert by_ticker["ACME"].sic == "2834"
    assert by_ticker["ACME"].country == "US"
    assert by_ticker["ACME"].provenance == ["edgar_us"]


def test_build_us_listings_skips_unlisted_cik(monkeypatch):
    monkeypatch.setattr(sec, "enumerate_sic", lambda sic, **kw: [(1, "ACME"), (99, "Private Co")])
    listings = sec.build_us_listings({"2834": "therapeutics"},
                                     cik_map=sec.parse_cik_exchange(_CIK_EXCHANGE_PAYLOAD),
                                     submissions_lookup=lambda cik: None)   # 99 unmapped + no fallback → skipped
    assert {l.cik for l in listings} == {"0000000001"}   # CIK 99 not in the listed map → skipped


def test_build_us_listings_submissions_fallback_recovers_missing(monkeypatch):
    # a SIC-enumerated CIK absent from the (incomplete) ticker file is recovered via submissions (the fix)
    monkeypatch.setattr(sec, "enumerate_sic", lambda sic, **kw: [(1, "ACME"), (1492422, "APELLIS")])
    listings = sec.build_us_listings(
        {"2834": "therapeutics"}, cik_map=sec.parse_cik_exchange(_CIK_EXCHANGE_PAYLOAD),
        submissions_lookup=lambda cik: ("APLS", "Nasdaq", "Apellis Pharmaceuticals, Inc.") if cik == 1492422 else None)
    by = {l.ticker: l for l in listings}
    assert set(by) == {"ACME", "APLS"}                    # APLS recovered despite being absent from the map
    assert by["APLS"].cik == "0001492422" and by["APLS"].sector_normalized == "therapeutics"


# ── M6 seed reader ────────────────────────────────────────────────────────────
def _make_fixture_m6_store(path):
    """Minimal M6-shaped store: just the columns m6_seed selects."""
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE companies (
            company_id TEXT PRIMARY KEY, name TEXT, primary_ticker TEXT, exchange TEXT,
            country TEXT, isin TEXT, lei TEXT, mktcap_usd_fd REAL, mktcap_unknown INTEGER, is_live INTEGER
        )"""
    )
    conn.executemany(
        "INSERT INTO companies VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("a", "Acrivon Therapeutics", "ACRV", "NASDAQ", "US", None, "LEIACRV", 7.1e7, 0, 1),
            ("b", "PeptiDream Inc", "4587", "TSE", "Japan", None, None, 3.0e9, 0, 1),
            ("c", "Dead Co", "DEAD", "NASDAQ", "US", None, None, None, 1, 0),   # not live → excluded
        ],
    )
    conn.commit()
    conn.close()


def test_load_m6_listings_reads_live_and_flags_priority(tmp_path):
    p = tmp_path / "store.db"
    _make_fixture_m6_store(str(p))
    listings = load_m6_listings(p)
    assert len(listings) == 2                            # dead co excluded
    names = {l.name for l in listings}
    assert names == {"Acrivon Therapeutics", "PeptiDream Inc"}
    assert all(l.in_existing_universe for l in listings)
    assert all(l.provenance == ["m6"] for l in listings)
    jp = [l for l in listings if l.ticker == "4587"][0]
    assert jp.country == "JP"                            # "Japan" → ISO code
    acrv = [l for l in listings if l.ticker == "ACRV"][0]
    assert acrv.lei == "LEIACRV"


def test_load_m6_listings_missing_store_is_failopen(tmp_path):
    assert load_m6_listings(tmp_path / "nope.db") == []
