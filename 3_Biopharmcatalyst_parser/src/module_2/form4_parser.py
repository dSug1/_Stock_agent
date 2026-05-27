"""Form 4 XML parser — splits one filing into filing-level metadata + a
list of non-derivative transaction rows.

Spec §4.3.4 + §2.2 + §2.3. Adapted from
``2_Funds_parser/src/module_4c/edgar_client.py::_parse_form4_xml``;
v1 differences:
  * Returns a (Form4Filing, list[Form4Txn]) tuple instead of one flat list.
  * Adds `direct_or_indirect` and `shares_owned_following` per spec §2.3.
  * Adds `transaction_code_meaning` via codes.meaning_for() so the column
    in `edgar_form4_transactions` is human-readable without a JOIN.
  * Derivative transactions are deliberately dropped (spec §4.3.4 +
    §4.6 — derivative-only filings produce a filing row with zero
    transaction rows; that's correct).
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Optional

from .codes import is_open_market, meaning_for

log = logging.getLogger(__name__)


class Form4ParseError(Exception):
    """Raised when the XML is malformed or missing required elements."""


@dataclass(frozen=True)
class Form4Filing:
    accession_number: str
    cik_issuer: str               # zero-padded 10-digit
    issuer_name: str
    issuer_trading_symbol: Optional[str]
    reporting_owner_cik: Optional[str]
    reporting_owner_name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    officer_title: Optional[str]
    filed_date: date              # the SEC-stamped filing date (caller supplies)


@dataclass(frozen=True)
class Form4Txn:
    transaction_date: date
    transaction_code: str
    transaction_code_meaning: str
    acquired_disposed: Optional[str]            # 'A' or 'D' (rarely missing)
    shares: Optional[float]
    price_per_share: Optional[float]
    shares_owned_following: Optional[int]
    is_open_market: bool
    direct_or_indirect: Optional[str]           # 'D' or 'I'


def _bool_from_xml(s: Optional[str]) -> bool:
    return (s or "").strip() in ("1", "true", "True")


def _pad_cik(cik: Optional[str]) -> Optional[str]:
    if cik is None:
        return None
    digits = "".join(c for c in str(cik) if c.isdigit())
    return digits.zfill(10) if digits else None


def _opt_float(s: Optional[str]) -> Optional[float]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _opt_int(s: Optional[str]) -> Optional[int]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _opt_date(s: Optional[str]) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def parse_form4_xml(
    xml_bytes: bytes,
    *,
    accession_number: str,
    filed_date: date,
) -> tuple[Form4Filing, list[Form4Txn]]:
    """Parse one Form 4 XML.

    Caller supplies ``accession_number`` and ``filed_date`` from the
    submissions index because those aren't reliably inside the XML body
    (the XML carries `periodOfReport` and `documentType`, not the filing
    accession or the SEC-side filed date).

    Raises Form4ParseError on malformed XML or a missing `issuer` element.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise Form4ParseError(f"xml parse: {e}") from e

    # --- issuer block ---
    issuer = root.find("issuer")
    if issuer is None:
        raise Form4ParseError("missing <issuer> element")
    cik_issuer = _pad_cik(issuer.findtext("issuerCik"))
    if not cik_issuer:
        raise Form4ParseError("missing issuerCik")
    issuer_name = (issuer.findtext("issuerName") or "").strip()
    issuer_trading_symbol = (issuer.findtext("issuerTradingSymbol") or "").strip() or None

    # --- reporting owner block (may have multiple in joint filings; take the first) ---
    rep = root.find("reportingOwner")
    insider_name = ""
    insider_cik: Optional[str] = None
    is_director = is_officer = is_ten_percent = False
    officer_title: Optional[str] = None
    if rep is not None:
        rid = rep.find("reportingOwnerId")
        if rid is not None:
            insider_name = (rid.findtext("rptOwnerName") or "").strip()
            insider_cik = _pad_cik(rid.findtext("rptOwnerCik"))
        rrel = rep.find("reportingOwnerRelationship")
        if rrel is not None:
            is_director = _bool_from_xml(rrel.findtext("isDirector"))
            is_officer = _bool_from_xml(rrel.findtext("isOfficer"))
            is_ten_percent = _bool_from_xml(rrel.findtext("isTenPercentOwner"))
            officer_title = (rrel.findtext("officerTitle") or "").strip() or None

    filing = Form4Filing(
        accession_number=accession_number,
        cik_issuer=cik_issuer,
        issuer_name=issuer_name,
        issuer_trading_symbol=issuer_trading_symbol,
        reporting_owner_cik=insider_cik,
        reporting_owner_name=insider_name,
        is_director=is_director,
        is_officer=is_officer,
        is_ten_percent_owner=is_ten_percent,
        officer_title=officer_title,
        filed_date=filed_date,
    )

    # --- non-derivative transactions ---
    txns: list[Form4Txn] = []
    for txn in root.iterfind("nonDerivativeTable/nonDerivativeTransaction"):
        txn_date = _opt_date(txn.findtext("transactionDate/value"))
        if txn_date is None:
            # Spec §4.5 acceptance requires `transaction_date NOT NULL`.
            # Skip the row with a warning rather than crashing the filing.
            log.warning("[%s] nonDerivativeTransaction missing transactionDate, skipped",
                        accession_number)
            continue
        code = (txn.findtext("transactionCoding/transactionCode") or "").strip().upper()
        ad = (txn.findtext("transactionAmounts/transactionAcquiredDisposedCode/value")
              or "").strip().upper() or None
        shares = _opt_float(txn.findtext("transactionAmounts/transactionShares/value"))
        price = _opt_float(txn.findtext("transactionAmounts/transactionPricePerShare/value"))
        owned_following = _opt_int(
            txn.findtext("postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        )
        nature = (
            (txn.findtext("ownershipNature/directOrIndirectOwnership/value") or "")
            .strip().upper() or None
        )
        if nature not in ("D", "I", None):
            nature = None  # tolerate weird casing/values; treat as unknown

        txns.append(Form4Txn(
            transaction_date=txn_date,
            transaction_code=code,
            transaction_code_meaning=meaning_for(code),
            acquired_disposed=ad,
            shares=shares,
            price_per_share=price,
            shares_owned_following=owned_following,
            is_open_market=is_open_market(code),
            direct_or_indirect=nature,
        ))

    return filing, txns
