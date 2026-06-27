"""Tests for the PIT companyfacts fundamentals extractor (Stage A; D16/D17). Offline — synthetic
companyfacts JSON + injected HTTP. The key property under test is first-print/leak-free `as_of`."""

import json

import pytest

from hype_parser import db, fundamentals as fu

# Synthetic companyfacts: FY2019 revenue first filed 2020-02 (=100), later RESTATED 2021-02 (=110);
# FY2020 revenue filed 2021-02 (=200). Shares for both year-ends.
FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {"units": {"USD": [
                {"end": "2019-12-31", "start": "2019-01-01", "val": 100, "filed": "2020-02-15",
                 "form": "10-K", "fy": 2019, "fp": "FY"},
                {"end": "2019-12-31", "start": "2019-01-01", "val": 110, "filed": "2021-02-15",
                 "form": "10-K/A", "fy": 2019, "fp": "FY"},   # restatement, filed a year later
                {"end": "2020-12-31", "start": "2020-01-01", "val": 200, "filed": "2021-02-15",
                 "form": "10-K", "fy": 2020, "fp": "FY"},
            ]}},
            "CommonStockSharesOutstanding": {"units": {"shares": [
                {"end": "2019-12-31", "val": 1000, "filed": "2020-02-15", "form": "10-K"},
                {"end": "2020-12-31", "val": 1200, "filed": "2021-02-15", "form": "10-K"},
            ]}},
        }
    }
}


def _conn(tmp_path):
    return db.connect(tmp_path / "f.db")


def test_schema_v7(tmp_path):
    assert db.current_version(_conn(tmp_path)) >= 7


def test_extract_facts_keeps_all_periods_and_filed():
    rows = fu.extract_facts(FACTS)
    rev = [r for r in rows if r["concept"] == "revenue"]
    sh = [r for r in rows if r["concept"] == "shares"]
    assert len(rev) == 3 and len(sh) == 2            # restatement kept as its own row
    assert all(r["filed"] for r in rows)             # filed date retained on every row


def test_load_ticker_cik_map():
    payload = json.dumps({"0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple"}}).encode()
    m = fu.load_ticker_cik_map(http_get=lambda u: payload)
    assert m["AAPL"] == "0000320193"                 # upper-cased + zero-padded to 10


def test_default_http_get_routes_through_size_cap(monkeypatch):
    """The real default getter (the one missed by the first D36 hardening pass — D37) must route its
    response body through the shared cap, so a hostile/oversized SEC response can't OOM the process.
    Cap *semantics* are covered by test_nethttp; here we lock the wiring against silent regression."""
    class _Resp:
        def __init__(self, body): self._b = body
        def read(self, n=-1): return self._b[:n] if (n is not None and n >= 0) else self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    seen = {}

    def _spy_cap(resp, *a, **k):
        seen["called"] = True
        return resp.read()

    monkeypatch.setattr(fu, "capped_read", _spy_cap)
    monkeypatch.setattr(fu.urllib.request, "urlopen", lambda req, timeout=30: _Resp(b"payload"))
    assert fu._default_http_get("https://data.sec.gov/whatever") == b"payload"
    assert seen.get("called"), "_default_http_get must read via capped_read"


def test_fetch_company_facts_injected():
    status, rows, err = fu.fetch_company_facts("X", "1", http_get=lambda u: json.dumps(FACTS).encode())
    assert status == "ok" and len(rows) == 5 and err is None


def test_fetch_company_facts_404_is_partial():
    def boom(u):
        raise RuntimeError("HTTP Error 404: Not Found")
    status, rows, err = fu.fetch_company_facts("X", "1", http_get=boom)
    assert status == "partial" and rows == [] and "404" in err


def test_as_of_is_first_print_and_leak_free(tmp_path):
    conn = _conn(tmp_path)
    _, rows, _ = fu.fetch_company_facts("X", "1", http_get=lambda u: json.dumps(FACTS).encode())
    fu.upsert_facts(conn, "X", "1", rows)

    # mid-2020: only the FY2019 first-print (100) has been filed — NOT the 2021 restatement (110)
    # and NOT FY2020 (filed 2021).
    snap = fu.fundamentals_as_of(conn, "X", "2020-06-01")
    assert snap["revenue_ttm"] == 100 and snap["shares"] == 1000 and snap["pre_revenue"] is False

    # mid-2021: FY2020 is now filed -> latest annual is 200 / 1200 shares.
    snap = fu.fundamentals_as_of(conn, "X", "2021-06-01")
    assert snap["revenue_ttm"] == 200 and snap["shares"] == 1200

    # before anything was filed -> nothing known yet.
    snap = fu.fundamentals_as_of(conn, "X", "2019-06-01")
    assert snap["revenue_ttm"] is None and snap["shares"] is None and snap["pre_revenue"] is True


def test_pre_revenue_when_no_revenue_concept(tmp_path):
    conn = _conn(tmp_path)
    facts = {"facts": {"us-gaap": {"CommonStockSharesOutstanding": {"units": {"shares": [
        {"end": "2021-12-31", "val": 500, "filed": "2022-02-01", "form": "10-K"}]}}}}}
    _, rows, _ = fu.fetch_company_facts("BIO", "2", http_get=lambda u: json.dumps(facts).encode())
    fu.upsert_facts(conn, "BIO", "2", rows)
    snap = fu.fundamentals_as_of(conn, "BIO", "2022-06-01")
    assert snap["pre_revenue"] is True and snap["revenue_ttm"] is None and snap["shares"] == 500


def test_revenue_ttm_from_quarterly(tmp_path):
    # no annual row -> derive TTM by summing four true single-quarters (~90d each).
    qs = [("2020-01-01", "2020-03-31", 50, "2020-04-30"),
          ("2020-04-01", "2020-06-30", 60, "2020-07-31"),
          ("2020-07-01", "2020-09-30", 70, "2020-10-31"),
          ("2020-10-01", "2020-12-31", 80, "2021-01-31")]
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"start": s, "end": e, "val": v, "filed": f, "form": "10-Q", "fy": 2020, "fp": "Q"}
        for (s, e, v, f) in qs]}}}}}
    conn = _conn(tmp_path)
    _, rows, _ = fu.fetch_company_facts("Q", "3", http_get=lambda u: json.dumps(facts).encode())
    fu.upsert_facts(conn, "Q", "3", rows)
    snap = fu.fundamentals_as_of(conn, "Q", "2021-03-01")
    assert snap["revenue_ttm"] == pytest.approx(260)        # 50+60+70+80
