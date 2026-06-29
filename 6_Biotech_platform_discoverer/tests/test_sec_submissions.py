"""D12 tests — SEC earliest-filing-date ipo_date fallback (pure parsers + ticker→CIK inversion)."""

from platform_discoverer.clients import sec_submissions as ss


def test_earliest_filing_date_picks_oldest():
    payload = {"filings": {"recent": {"filingDate": ["2024-03-01", "2021-06-25", "2023-09-10"]}}}
    assert ss._earliest_filing_date(payload) == "2021-06-25"


def test_earliest_filing_date_empty_and_malformed():
    assert ss._earliest_filing_date({}) is None
    assert ss._earliest_filing_date({"filings": {"recent": {"filingDate": []}}}) is None
    # malformed entries are ignored, valid one wins
    assert ss._earliest_filing_date(
        {"filings": {"recent": {"filingDate": ["bad", "2020-01-01", None]}}}) == "2020-01-01"


def test_invert_cik_map_upper_and_first_wins():
    cik_map = {320193: ("AAPL", "Nasdaq", "Apple"), 11: ("acrv", "Nasdaq", "Acrivon")}
    inv = ss._invert_cik_map(cik_map)
    assert inv["AAPL"] == 320193 and inv["ACRV"] == 11


def test_first_filing_date_no_cik_returns_none():
    # ticker not in the map → None without any fetch
    assert ss.first_filing_date("ZZZZ", ticker_cik_map={"AAA": 1}) is None
