"""Module 2 — Form 4 XML parser, offline.

Uses a real SEC Form 4 XML saved to ``tests/fixtures/form4_sample.xml``
(a CRBP filing fetched during dev). The fixture is deliberately checked
into the repo so this test runs without network access — spec §4.5
acceptance: "A test fixture (a real Form 4 XML saved to tests/fixtures/)
is parsed correctly offline by pytest."

Plus 5 synthetic-XML tests that pin specific parsing edge cases:
non-derivative + derivative coexistence (derivatives dropped), missing
transaction_date (row skipped), unknown transaction code (still
ingested with generic meaning), indirect ownership preserved, the
``is_open_market`` derivation following the code.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_2.form4_parser import Form4ParseError, parse_form4_xml  # noqa: E402


SAMPLE_XML = PROJECT_ROOT / "tests" / "fixtures" / "form4_sample.xml"


# ===== real-data fixture (spec §4.5) =====================================

@pytest.mark.skipif(not SAMPLE_XML.exists(),
                    reason="tests/fixtures/form4_sample.xml not present")
def test_real_form4_fixture_parses(tmp_path):
    xml = SAMPLE_XML.read_bytes()
    filing, txns = parse_form4_xml(
        xml,
        accession_number="0001193125-26-237103",
        filed_date=date(2026, 5, 22),
    )
    # Filing-level: issuer block intact
    assert filing.cik_issuer == "0001595097"
    assert filing.issuer_name == "Corbus Pharmaceuticals Holdings, Inc."
    assert filing.issuer_trading_symbol == "CRBP"
    # Reporting owner: CBO grant filing
    assert filing.reporting_owner_name == "Saxena Nishant C"
    assert filing.is_officer is True
    assert filing.is_director is False
    assert filing.is_ten_percent_owner is False
    assert filing.officer_title == "Chief Business Officer"
    assert filing.accession_number == "0001193125-26-237103"
    assert filing.filed_date == date(2026, 5, 22)
    # Transactions: one grant (code A)
    assert len(txns) == 1
    t = txns[0]
    assert t.transaction_code == "A"
    assert t.is_open_market is False           # grants are NOT open-market
    assert "grant" in t.transaction_code_meaning.lower()
    assert t.shares == 58300.0
    assert t.price_per_share == 0.0
    assert t.shares_owned_following == 61800
    assert t.direct_or_indirect == "D"
    assert t.transaction_date == date(2026, 5, 21)


# ===== synthetic-XML edge cases ===========================================

def _wrap_xml(issuer_cik: str = "0000001234",
              issuer_name: str = "Test Corp",
              issuer_sym: str = "TEST",
              owner_cik: str = "0000005555",
              owner_name: str = "Alice Anderson",
              is_director: str = "0",
              is_officer: str = "1",
              is_ten_percent: str = "0",
              officer_title: str = "CEO",
              non_deriv_xml: str = "",
              deriv_xml: str = "") -> bytes:
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <issuer>
    <issuerCik>{issuer_cik}</issuerCik>
    <issuerName>{issuer_name}</issuerName>
    <issuerTradingSymbol>{issuer_sym}</issuerTradingSymbol>
  </issuer>
  <reportingOwner>
    <reportingOwnerId>
      <rptOwnerCik>{owner_cik}</rptOwnerCik>
      <rptOwnerName>{owner_name}</rptOwnerName>
    </reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>{is_director}</isDirector>
      <isOfficer>{is_officer}</isOfficer>
      <isTenPercentOwner>{is_ten_percent}</isTenPercentOwner>
      <officerTitle>{officer_title}</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    {non_deriv_xml}
  </nonDerivativeTable>
  <derivativeTable>
    {deriv_xml}
  </derivativeTable>
</ownershipDocument>
""".encode("utf-8")


def _txn_xml(date_str: str, code: str, ad: str, shares: str,
             price: str, owned: str, direct_or_indirect: str = "D") -> str:
    return f"""
    <nonDerivativeTransaction>
      <transactionDate><value>{date_str}</value></transactionDate>
      <transactionCoding>
        <transactionCode>{code}</transactionCode>
      </transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>{owned}</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
      <ownershipNature>
        <directOrIndirectOwnership><value>{direct_or_indirect}</value></directOrIndirectOwnership>
      </ownershipNature>
    </nonDerivativeTransaction>"""


def test_open_market_purchase_p_flagged_true():
    xml = _wrap_xml(non_deriv_xml=_txn_xml("2026-04-15", "P", "A", "1000", "12.50", "150000"))
    filing, txns = parse_form4_xml(xml, accession_number="X1", filed_date=date(2026, 4, 16))
    assert len(txns) == 1
    assert txns[0].transaction_code == "P"
    assert txns[0].is_open_market is True
    assert txns[0].acquired_disposed == "A"
    assert txns[0].shares == 1000
    assert txns[0].price_per_share == 12.50


def test_option_exercise_m_flagged_false():
    xml = _wrap_xml(non_deriv_xml=_txn_xml("2026-04-15", "M", "A", "5000", "0", "155000"))
    filing, txns = parse_form4_xml(xml, accession_number="X2", filed_date=date(2026, 4, 16))
    assert txns[0].transaction_code == "M"
    assert txns[0].is_open_market is False


def test_derivative_transactions_dropped():
    # A filing with ONLY derivative transactions should produce a filing
    # row + zero transaction rows (spec §4.6 edge case).
    deriv = """
    <derivativeTransaction>
      <transactionDate><value>2026-04-15</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1000</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </derivativeTransaction>"""
    xml = _wrap_xml(non_deriv_xml="", deriv_xml=deriv)
    filing, txns = parse_form4_xml(xml, accession_number="X3", filed_date=date(2026, 4, 16))
    assert filing.cik_issuer == "0000001234"
    assert txns == []


def test_indirect_ownership_preserved():
    xml = _wrap_xml(
        non_deriv_xml=_txn_xml("2026-04-15", "P", "A", "200", "10", "5000",
                               direct_or_indirect="I"),
    )
    filing, txns = parse_form4_xml(xml, accession_number="X4", filed_date=date(2026, 4, 16))
    assert txns[0].direct_or_indirect == "I"


def test_missing_transaction_date_skipped():
    # Per spec §2.3 transaction_date is NOT NULL — the parser drops the
    # offending row instead of crashing the whole filing.
    bad_txn = """
    <nonDerivativeTransaction>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>100</value></transactionShares>
      </transactionAmounts>
    </nonDerivativeTransaction>"""
    good_txn = _txn_xml("2026-04-15", "S", "D", "500", "15.0", "0")
    xml = _wrap_xml(non_deriv_xml=bad_txn + good_txn)
    filing, txns = parse_form4_xml(xml, accession_number="X5", filed_date=date(2026, 4, 16))
    # One bad, one good → one parsed
    assert len(txns) == 1
    assert txns[0].transaction_code == "S"


def test_missing_issuer_raises():
    bad = b"<?xml version='1.0'?><ownershipDocument></ownershipDocument>"
    with pytest.raises(Form4ParseError, match="issuer"):
        parse_form4_xml(bad, accession_number="X6", filed_date=date(2026, 4, 16))


def test_malformed_xml_raises():
    with pytest.raises(Form4ParseError, match="xml parse"):
        parse_form4_xml(b"<not><valid", accession_number="X7", filed_date=date(2026, 4, 16))


def test_unknown_transaction_code_still_parsed_with_generic_meaning():
    # A code we don't have in our table (e.g., 'Q' isn't standard)
    # should still parse — the meaning becomes 'Other (Q)' and the row
    # is not open-market.
    xml = _wrap_xml(non_deriv_xml=_txn_xml("2026-04-15", "Q", "A", "1000", "5", "10000"))
    filing, txns = parse_form4_xml(xml, accession_number="X8", filed_date=date(2026, 4, 16))
    assert len(txns) == 1
    assert txns[0].transaction_code == "Q"
    assert txns[0].transaction_code_meaning == "Other (Q)"
    assert txns[0].is_open_market is False
