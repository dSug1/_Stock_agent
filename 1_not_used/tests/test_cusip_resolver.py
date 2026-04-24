"""Tests for layer_minus1.cusip_resolver.

Focus: the non-equity filter. OpenFIGI returns ETFs, preferreds, bonds,
etc. on US exchanges; 13F filings legitimately include those holdings
and we must not score them as common stock.
"""
from __future__ import annotations

import json

import pytest

from database.db import get_connection
from layer_minus1 import cusip_resolver


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    c = get_connection(db_path)
    yield c
    c.close()


def _mock_http_post(response_payload):
    """Build an http_post callable that returns the canned OpenFIGI payload
    regardless of request body, serialised to bytes like the real endpoint.
    """
    def _post(url, body, headers):
        return json.dumps(response_payload).encode("utf-8")
    return _post


def test_openfigi_common_stock_resolves_to_ticker(conn):
    http = _mock_http_post([
        {"data": [{
            "ticker": "AAPL",
            "exchCode": "US",
            "securityType": "Common Stock",
            "securityType2": "Common Stock",
        }]},
    ])
    result = cusip_resolver.resolve_cusip(
        "037833100", conn, http_post=http
    )
    assert result == "AAPL"

    row = conn.execute(
        "SELECT ticker, security_type FROM cusip_ticker_map "
        "WHERE cusip = ?",
        ("037833100",),
    ).fetchone()
    assert row["ticker"] == "AAPL"
    assert row["security_type"] == "Common Stock"


def test_openfigi_etf_returns_none(conn):
    http = _mock_http_post([
        {"data": [{
            "ticker": "SPY",
            "exchCode": "US",
            "securityType": "ETP",
            "securityType2": "ETF",
        }]},
    ])
    result = cusip_resolver.resolve_cusip(
        "78462F103", conn, http_post=http
    )
    assert result is None

    row = conn.execute(
        "SELECT ticker, security_type FROM cusip_ticker_map "
        "WHERE cusip = ?",
        ("78462F103",),
    ).fetchone()
    assert row is not None
    assert row["ticker"] is None


def test_openfigi_preferred_returns_none(conn):
    http = _mock_http_post([
        {"data": [{
            "ticker": "BAC.PR.K",
            "exchCode": "US",
            "securityType": "Preferred",
            "securityType2": "Preferred Stock",
        }]},
    ])
    result = cusip_resolver.resolve_cusip(
        "060505EE1", conn, http_post=http
    )
    assert result is None


def test_openfigi_depositary_receipt_resolves(conn):
    http = _mock_http_post([
        {"data": [{
            "ticker": "BABA",
            "exchCode": "UN",
            "securityType": "Depositary Receipt",
            "securityType2": "Depositary Receipt",
        }]},
    ])
    result = cusip_resolver.resolve_cusip(
        "01609W102", conn, http_post=http
    )
    assert result == "BABA"


def test_openfigi_mixed_records_picks_common_stock(conn):
    # Real OpenFIGI responses often include multiple exchange listings
    # and instrument variants for the same CUSIP. We must skip ETF/Trust
    # entries and pick the Common Stock even if it isn't first.
    http = _mock_http_post([
        {"data": [
            {
                "ticker": "XYZ",
                "exchCode": "US",
                "securityType": "ETP",
                "securityType2": "ETF",
            },
            {
                "ticker": "XYZ",
                "exchCode": "US",
                "securityType": "Common Stock",
                "securityType2": "Common Stock",
            },
        ]},
    ])
    result = cusip_resolver.resolve_cusip(
        "999999999", conn, http_post=http
    )
    assert result == "XYZ"


def test_openfigi_trust_returns_none(conn):
    # GBTC-style closed-end grantor trust: common on 13F rolls.
    http = _mock_http_post([
        {"data": [{
            "ticker": "GBTC",
            "exchCode": "US",
            "securityType": "Closed-End Fund",
            "securityType2": "Trust",
        }]},
    ])
    result = cusip_resolver.resolve_cusip(
        "389064109", conn, http_post=http
    )
    assert result is None


def test_openfigi_warrant_returns_none(conn):
    http = _mock_http_post([
        {"data": [{
            "ticker": "ACME.WS",
            "exchCode": "US",
            "securityType": "Warrant",
            "securityType2": "Warrant",
        }]},
    ])
    result = cusip_resolver.resolve_cusip(
        "000000001", conn, http_post=http
    )
    assert result is None


def test_cached_result_skips_http(conn):
    # After a successful resolve, a second call must not hit http_post.
    http = _mock_http_post([
        {"data": [{
            "ticker": "MSFT",
            "exchCode": "US",
            "securityType": "Common Stock",
        }]},
    ])
    assert cusip_resolver.resolve_cusip(
        "594918104", conn, http_post=http
    ) == "MSFT"

    def _explode(url, body, headers):
        raise AssertionError("http_post should not be called for cached cusip")

    assert cusip_resolver.resolve_cusip(
        "594918104", conn, http_post=_explode
    ) == "MSFT"
