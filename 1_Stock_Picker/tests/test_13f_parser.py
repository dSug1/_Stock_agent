from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from database.db import init_db, get_connection, now_iso
from layer_minus1 import holdings_store
from layer_minus1.edgar_13f_parser import (
    ingest_all_institutions,
    parse_13f_xml,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    db_path = tmp_path / "stockpicker.db"
    init_db(db_path)
    return get_connection(db_path)


def _seed_institution_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT id FROM institutions ORDER BY id LIMIT 1"
    ).fetchone()
    return int(row["id"])


_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ACME CORP</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>000360206</cusip>
    <value>123456</value>
    <shrsOrPrnAmt>
      <sshPrnamt>1000</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
    <investmentDiscretion>SOLE</investmentDiscretion>
  </infoTable>
  <infoTable>
    <nameOfIssuer>FOOBAR INC</nameOfIssuer>
    <titleOfClass>BOND</titleOfClass>
    <cusip>111111111</cusip>
    <value>50000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>500</sshPrnamt>
      <sshPrnamtType>PRN</sshPrnamtType>
    </shrsOrPrnAmt>
  </infoTable>
  <infoTable>
    <nameOfIssuer>BAZ LTD</nameOfIssuer>
    <titleOfClass>COM</titleOfClass>
    <cusip>222222222</cusip>
    <value>not-a-number</value>
    <shrsOrPrnAmt>
      <sshPrnamt></sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
  </infoTable>
</informationTable>
"""


# ---------------------------------------------------------------------
# point_in_time enforcement
# ---------------------------------------------------------------------

def test_get_holdings_as_of_earlier_than_filing_date_returns_empty(
    tmp_path: Path,
) -> None:
    conn = _fresh_db(tmp_path)
    try:
        inst_id = _seed_institution_id(conn)
        holdings_store.save_holdings(
            inst_id,
            filing_date="2024-05-15",
            period_of_report="2024-03-31",
            holdings=[{
                "cusip": "000360206",
                "ticker": "ACME",
                "shares": 1000,
                "market_value": 123456,
            }],
            conn=conn,
        )
        result = holdings_store.get_holdings_as_of(
            inst_id, "2024-05-14", conn
        )
        assert result == []
    finally:
        conn.close()


def test_get_holdings_as_of_equal_to_filing_date_returns_rows(
    tmp_path: Path,
) -> None:
    conn = _fresh_db(tmp_path)
    try:
        inst_id = _seed_institution_id(conn)
        holdings_store.save_holdings(
            inst_id,
            filing_date="2024-05-15",
            period_of_report="2024-03-31",
            holdings=[{
                "cusip": "000360206",
                "ticker": "ACME",
                "shares": 1000,
                "market_value": 123456,
            }],
            conn=conn,
        )
        result = holdings_store.get_holdings_as_of(
            inst_id, "2024-05-15", conn
        )
        assert len(result) == 1
        assert result[0]["cusip"] == "000360206"
        assert result[0]["ticker"] == "ACME"
        assert result[0]["shares"] == 1000
    finally:
        conn.close()


def test_holdings_store_source_never_references_period_of_report_in_sql(
) -> None:
    """period_of_report must not appear in any WHERE or ORDER BY clause.

    Searches the holdings_store source for SQL usages that would
    violate the point-in-time survivorship-bias guarantee.
    """
    source_path = (
        Path(__file__).resolve().parent.parent
        / "src" / "layer_minus1" / "holdings_store.py"
    )
    # Strip Python comment lines so regexes only match real code/SQL.
    code_lines = [
        line for line in source_path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    text = "\n".join(code_lines)

    forbidden_patterns = [
        r"WHERE[^\"']*period_of_report",
        r"ORDER\s+BY[^\"']*period_of_report",
        r"AND[^\"']*period_of_report\s*[<>=!]",
    ]
    for pat in forbidden_patterns:
        assert re.search(pat, text, re.IGNORECASE) is None, (
            f"holdings_store.py references period_of_report via "
            f"pattern /{pat}/ — breaks point-in-time guarantee"
        )


def test_prior_quarter_uses_filing_date_strictly_less(
    tmp_path: Path,
) -> None:
    conn = _fresh_db(tmp_path)
    try:
        inst_id = _seed_institution_id(conn)
        holdings_store.save_holdings(
            inst_id, "2024-02-14", "2023-12-31",
            [{"cusip": "AAA", "ticker": "A", "shares": 100,
              "market_value": 1}],
            conn,
        )
        holdings_store.save_holdings(
            inst_id, "2024-05-15", "2024-03-31",
            [{"cusip": "AAA", "ticker": "A", "shares": 200,
              "market_value": 2}],
            conn,
        )
        prior = holdings_store.get_prior_quarter_holdings(
            inst_id, "2024-05-15", conn
        )
        assert len(prior) == 1
        assert prior[0]["shares"] == 100
    finally:
        conn.close()


# ---------------------------------------------------------------------
# parse_13f_xml
# ---------------------------------------------------------------------

def test_parse_13f_xml_extracts_cusip_shares_market_value() -> None:
    holdings = parse_13f_xml(_SAMPLE_XML)
    assert any(
        h["cusip"] == "000360206"
        and h["shares"] == 1000
        and h["market_value"] == 123456
        and h["name_of_issuer"] == "ACME CORP"
        for h in holdings
    )


def test_parse_13f_xml_skips_prn_holdings() -> None:
    holdings = parse_13f_xml(_SAMPLE_XML)
    cusips = [h["cusip"] for h in holdings]
    assert "111111111" not in cusips  # PRN row must be dropped


def test_parse_13f_xml_handles_malformed_values_without_crash() -> None:
    holdings = parse_13f_xml(_SAMPLE_XML)
    baz = next((h for h in holdings if h["cusip"] == "222222222"), None)
    assert baz is not None
    assert baz["market_value"] is None
    assert baz["shares"] is None


def test_parse_13f_xml_returns_empty_on_unrecoverable_xml() -> None:
    assert parse_13f_xml("not xml at all <<<") == []


# ---------------------------------------------------------------------
# ingest_all_institutions — deduplication
# ---------------------------------------------------------------------

_INGEST_XML = """<?xml version="1.0"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable>
    <nameOfIssuer>ACME CORP</nameOfIssuer>
    <cusip>000360206</cusip>
    <value>1000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>100</sshPrnamt>
      <sshPrnamtType>SH</sshPrnamtType>
    </shrsOrPrnAmt>
  </infoTable>
</informationTable>
"""


def _build_mock_http_get():
    submissions = {
        "filings": {
            "recent": {
                "form": ["13F-HR"],
                "filingDate": ["2025-02-14"],
                "reportDate": ["2024-12-31"],
                "accessionNumber": ["0001234567-25-000001"],
                "primaryDocument": ["primary_doc.xml"],
            }
        }
    }
    index = {
        "directory": {
            "item": [
                {"name": "infotable.xml"},
                {"name": "primary_doc.xml"},
            ]
        }
    }

    def mock(url: str) -> bytes:
        if "/submissions/CIK" in url:
            return json.dumps(submissions).encode("utf-8")
        if url.endswith("index.json"):
            return json.dumps(index).encode("utf-8")
        if url.endswith(".xml"):
            return _INGEST_XML.encode("utf-8")
        raise AssertionError(f"unexpected mock URL: {url}")

    return mock


def test_ingest_deduplication(tmp_path: Path) -> None:
    conn = _fresh_db(tmp_path)
    try:
        ts = now_iso()
        conn.execute(
            "INSERT INTO cusip_ticker_map "
            "(cusip, ticker, exchange, resolved_date, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("000360206", "ACME", "US", ts, ts, ts),
        )
        conn.commit()

        mock_get = _build_mock_http_get()

        summary1 = ingest_all_institutions(
            conn,
            from_date="2025-01-01",
            to_date="2025-06-01",
            http_get=mock_get,
        )
        count1 = conn.execute(
            "SELECT COUNT(*) AS c FROM institution_holdings"
        ).fetchone()["c"]
        assert count1 > 0
        assert sum(s["filings_processed"] for s in summary1.values()) > 0

        summary2 = ingest_all_institutions(
            conn,
            from_date="2025-01-01",
            to_date="2025-06-01",
            http_get=mock_get,
        )
        count2 = conn.execute(
            "SELECT COUNT(*) AS c FROM institution_holdings"
        ).fetchone()["c"]
        assert count2 == count1
        # Second pass dedups every filing at the filing-level check.
        assert all(
            s["filings_processed"] == 0 for s in summary2.values()
        )
    finally:
        conn.close()


def test_filing_level_dedup(tmp_path: Path) -> None:
    """filings_log owns the dedup key: once an accession is logged, a
    re-run never re-downloads the filing — not even if
    institution_holdings is cleared.
    """
    conn = _fresh_db(tmp_path)
    try:
        ts = now_iso()
        conn.execute(
            "INSERT INTO cusip_ticker_map "
            "(cusip, ticker, exchange, resolved_date, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("000360206", "ACME", "US", ts, ts, ts),
        )
        conn.commit()

        mock_get = _build_mock_http_get()

        # First run processes the filing and writes filings_log.
        ingest_all_institutions(
            conn,
            from_date="2025-01-01",
            to_date="2025-06-01",
            http_get=mock_get,
        )

        logged = conn.execute(
            "SELECT accession_number, parse_status, holdings_count "
            "FROM filings_log"
        ).fetchall()
        assert any(
            r["accession_number"] == "0001234567-25-000001"
            and r["parse_status"] == "success"
            and r["holdings_count"] == 1
            for r in logged
        )

        # Simulate downstream wipe of holdings. filings_log must still
        # suppress a re-download.
        conn.execute("DELETE FROM institution_holdings")
        conn.commit()

        # Trace the second run to confirm no per-filing fetches.
        calls: list[str] = []

        def traced(url: str) -> bytes:
            calls.append(url)
            return mock_get(url)

        summary2 = ingest_all_institutions(
            conn,
            from_date="2025-01-01",
            to_date="2025-06-01",
            http_get=traced,
        )

        for url in calls:
            assert not url.endswith(".xml"), (
                f"refetched xml after dedup: {url}"
            )
            assert "index.json" not in url, (
                f"refetched index.json after dedup: {url}"
            )

        assert sum(s["filings_skipped"] for s in summary2.values()) >= 1
        assert all(
            s["filings_processed"] == 0 for s in summary2.values()
        )

        # Holdings stay cleared — nothing was re-processed.
        count_after = conn.execute(
            "SELECT COUNT(*) AS c FROM institution_holdings"
        ).fetchone()["c"]
        assert count_after == 0
    finally:
        conn.close()
