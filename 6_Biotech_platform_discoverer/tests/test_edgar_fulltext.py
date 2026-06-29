"""Unit tests for the EDGAR full-text parsers (D17/B5). Pure functions only — no network."""

from __future__ import annotations

from platform_discoverer.clients import edgar_fulltext as ef


def test_strip_html_drops_tags_scripts_and_unescapes():
    raw = ("<html><head><style>.x{color:red}</style><script>var a=1;</script></head>"
           "<body><p>Acrivon&nbsp;builds&amp;tests a platform.</p><div>AP3 &lt;engine&gt;</div></body></html>")
    out = ef._strip_html(raw)
    assert "color:red" not in out and "var a=1" not in out
    assert "Acrivon builds&tests a platform." in out
    assert "AP3 <engine>" in out


def test_extract_item1_picks_longest_section_and_stops_at_1a():
    # Mimics a 10-K: a short ToC mention of "Item 1." then the real long Business section, then Item 1A.
    toc = "Item 1. Business 4 Item 1A. Risk Factors 20 "
    body = "Item 1. Business " + ("Our proprietary phosphoproteomic data engine generates drug-response "
                                   "profiles at scale. " * 40) + "Item 1A. Risk Factors The following risks..."
    text = toc + body
    out = ef._extract_item1(text)
    assert "proprietary phosphoproteomic data engine" in out
    assert "Risk Factors The following" not in out          # stopped at Item 1A
    assert len(out) <= ef.MAX_BUSINESS_CHARS


def test_extract_item1_rejects_heading_only():
    assert ef._extract_item1("Item 1. Business 4 Item 1A. Risk Factors 20") == ""
    assert ef._extract_item1("") == ""


def test_latest_annual_report_picks_most_recent_10k():
    payload = {"filings": {"recent": {
        "form": ["8-K", "10-K", "10-Q", "10-K", "20-F"],
        "filingDate": ["2024-01-01", "2023-03-01", "2024-05-01", "2025-03-01", "2022-06-01"],
        "accessionNumber": ["0-0", "0-1", "0-2", "0-3", "0-4"],
        "primaryDocument": ["a.htm", "b.htm", "c.htm", "d.htm", "e.htm"],
    }}}
    rpt = ef._latest_annual_report(payload)
    assert rpt["form"] == "10-K" and rpt["filing_date"] == "2025-03-01"
    assert rpt["accession"] == "0-3" and rpt["primary_doc"] == "d.htm"


def test_latest_annual_report_none_when_no_annual_form():
    payload = {"filings": {"recent": {"form": ["8-K", "10-Q"], "filingDate": ["2024-01-01", "2024-02-01"],
                                      "accessionNumber": ["x", "y"], "primaryDocument": ["a", "b"]}}}
    assert ef._latest_annual_report(payload) is None
    assert ef._latest_annual_report({}) is None


def test_fetch_returns_none_for_unknown_ticker():
    class C:
        primary_ticker = "ZZZZ"
    assert ef.fetch(C(), ticker_cik_map={"ACRV": 1683553}) is None
