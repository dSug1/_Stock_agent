from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from typing import Callable, Optional

try:
    # defusedxml guards against entity-expansion (billion-laughs) DoS on
    # untrusted EDGAR XML. Fall back to stdlib if it isn't installed.
    from defusedxml.ElementTree import fromstring as _xml_fromstring
    from defusedxml.common import DefusedXmlException as _DefusedXmlException
    _XML_PARSE_ERRORS: tuple = (ET.ParseError, _DefusedXmlException)
except ImportError:  # pragma: no cover - defusedxml is in requirements.txt
    _xml_fromstring = ET.fromstring
    _XML_PARSE_ERRORS = (ET.ParseError,)

from database.db import MARKET_VALUE_RAW_USD_CUTOFF

log = logging.getLogger(__name__)

# Hard cap on any single HTTP response body. 13F info tables are well under
# this; the cap exists to stop a hostile/MITM'd response from exhausting
# memory. We read one byte past the cap so overflow is detectable.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def _read_capped(resp) -> bytes:
    """Read a response body, rejecting anything over MAX_RESPONSE_BYTES."""
    body = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeds {MAX_RESPONSE_BYTES} byte cap")
    return body


def _normalize_market_value(
    filing_date: str, market_value: Optional[int]
) -> Optional[int]:
    """Return market_value in whole USD regardless of filing era.

    SEC Form 13F amendment (2023-01-03) changed the reported unit from
    thousands of USD to whole USD. Pre-cutoff values are multiplied by
    1000 so downstream code can treat market_value as raw USD uniformly.
    """
    if market_value is None:
        return None
    if filing_date < MARKET_VALUE_RAW_USD_CUTOFF:
        return market_value * 1000
    return market_value

EDGAR_USER_AGENT = "StockPicker contact@stockpicker.local"
EDGAR_RATE_LIMIT_SLEEP = 0.11
EDGAR_SUBMISSIONS_URL = (
    "https://data.sec.gov/submissions/CIK{cik}.json"
)
EDGAR_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"


HttpGet = Callable[[str], bytes]


def _default_http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": EDGAR_USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = _read_capped(resp)
    time.sleep(EDGAR_RATE_LIMIT_SLEEP)
    return body


def _pad_cik(cik: str) -> str:
    digits = "".join(c for c in cik if c.isdigit())
    return digits.zfill(10)


def fetch_institution_filings(
    cik: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    *,
    http_get: Optional[HttpGet] = None,
) -> list[dict]:
    """Return list of 13F-HR filings for a CIK.

    from_date / to_date are inclusive ISO date strings; filters by
    filing_date. period_of_report is captured but never filtered on.
    """
    http_get = http_get or _default_http_get
    padded = _pad_cik(cik)
    url = EDGAR_SUBMISSIONS_URL.format(cik=padded)
    payload = json.loads(http_get(url).decode("utf-8"))
    recent = payload.get("filings", {}).get("recent", {})

    forms = recent.get("form", [])
    filing_dates = recent.get("filingDate", [])
    report_dates = recent.get("reportDate", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    out: list[dict] = []
    for i, form in enumerate(forms):
        if form != "13F-HR":
            continue
        fdate = filing_dates[i] if i < len(filing_dates) else None
        if fdate is None:
            continue
        if from_date is not None and fdate < from_date:
            continue
        if to_date is not None and fdate > to_date:
            continue
        accession = accessions[i] if i < len(accessions) else None
        if accession is None:
            continue
        accession_nodashes = accession.replace("-", "")
        index_url = (
            f"{EDGAR_ARCHIVE_BASE}/{int(padded)}/"
            f"{accession_nodashes}/index.json"
        )
        out.append({
            "cik": padded,
            "filing_date": fdate,
            "period_of_report":
                report_dates[i] if i < len(report_dates) else None,
            "accession_number": accession,
            "primary_document":
                primary_docs[i] if i < len(primary_docs) else None,
            "index_url": index_url,
            "archive_dir":
                f"{EDGAR_ARCHIVE_BASE}/{int(padded)}/"
                f"{accession_nodashes}",
        })
    return out


def find_information_table_url(
    filing: dict, *, http_get: Optional[HttpGet] = None
) -> Optional[str]:
    """Locate the 13F information-table XML document URL inside a filing."""
    http_get = http_get or _default_http_get
    body = http_get(filing["index_url"])
    try:
        index = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        log.warning(
            "failed to parse EDGAR index %s: %s",
            filing["index_url"], exc,
        )
        return None
    archive_dir = filing["archive_dir"]
    items = index.get("directory", {}).get("item", [])
    for item in items:
        name = item.get("name", "")
        if name.lower().endswith(".xml") and "primary_doc" not in name:
            return f"{archive_dir}/{name}"
    for item in items:
        name = item.get("name", "")
        if name.lower().endswith(".xml"):
            return f"{archive_dir}/{name}"
    return None


def download_13f_document(
    document_url: str, *, http_get: Optional[HttpGet] = None
) -> str:
    http_get = http_get or _default_http_get
    return http_get(document_url).decode("utf-8", errors="replace")


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _safe_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_13f_xml(xml_content: str) -> list[dict]:
    """Parse a 13F information table into a list of holdings.

    Returns only SH (share) holdings; PRN (principal) rows are skipped.
    Never raises on malformed per-row data — only on unrecoverable XML.
    """
    try:
        root = _xml_fromstring(xml_content)
    except _XML_PARSE_ERRORS as exc:
        # ET.ParseError covers malformed XML; defusedxml raises a
        # DefusedXmlException (not a ParseError) on an entity bomb. Both
        # are unrecoverable input, so fail open with [] either way.
        log.warning("malformed or unsafe 13F XML: %s", exc)
        return []

    holdings: list[dict] = []
    for elem in root.iter():
        if _local(elem.tag) != "infoTable":
            continue
        rec: dict = {}
        shares_type: Optional[str] = None
        for child in elem:
            name = _local(child.tag)
            if name == "nameOfIssuer":
                rec["name_of_issuer"] = (child.text or "").strip() or None
            elif name == "cusip":
                rec["cusip"] = (child.text or "").strip() or None
            elif name == "value":
                rec["market_value"] = _safe_int(child.text)
            elif name == "shrsOrPrnAmt":
                for sub in child:
                    sub_name = _local(sub.tag)
                    if sub_name == "sshPrnamt":
                        rec["shares"] = _safe_int(sub.text)
                    elif sub_name == "sshPrnamtType":
                        shares_type = (sub.text or "").strip() or None
        if shares_type != "SH":
            continue
        if not rec.get("cusip"):
            continue
        holdings.append({
            "name_of_issuer": rec.get("name_of_issuer"),
            "cusip": rec["cusip"],
            "shares": rec.get("shares"),
            "market_value": rec.get("market_value"),
        })
    return holdings


def ingest_all_institutions(
    conn: sqlite3.Connection,
    from_date: str = "2025-01-01",
    to_date: Optional[str] = None,
    *,
    http_get: Optional[HttpGet] = None,
) -> dict:
    """Download and persist every 13F-HR filing for every tracked
    institution in [from_date, to_date].

    Dedup is at the filing level: if institution_holdings already
    contains any row for (institution_id, filing_date), the filing
    is skipped without re-downloading.

    Returns per-institution summary dict.
    """
    # Late imports to avoid cycles at module load.
    from database.db import now_iso
    from layer_minus1 import cusip_resolver

    if to_date is None:
        to_date = date.today().isoformat()
    http_get = http_get or _default_http_get

    institutions = conn.execute(
        "SELECT id, name, cik FROM institutions "
        "WHERE cik IS NOT NULL ORDER BY id"
    ).fetchall()

    summary: dict = {}

    for inst in institutions:
        inst_id = inst["id"]
        inst_name = inst["name"]
        cik = inst["cik"]
        stats = {
            "filings_processed": 0,
            "holdings_inserted": 0,
            "cusips_unresolved": 0,
            "filings_skipped": 0,
            "filings_empty": 0,
            "filings_error": 0,
        }
        summary[inst_name] = stats

        try:
            filings = fetch_institution_filings(
                cik, from_date, to_date, http_get=http_get,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "submissions fetch failed for %s (CIK %s): %s",
                inst_name, cik, exc,
            )
            continue

        for filing in filings:
            filing_date = filing["filing_date"]
            accession = filing["accession_number"]

            existing = conn.execute(
                "SELECT 1 FROM filings_log "
                "WHERE institution_id = ? AND accession_number = ? "
                "LIMIT 1",
                (inst_id, accession),
            ).fetchone()
            if existing is not None:
                stats["filings_skipped"] += 1
                log.debug(
                    "skip %s %s %s (already logged)",
                    inst_name, filing_date, accession,
                )
                continue

            # Download phase: any failure here leaves no filings_log
            # row so a future run retries.
            doc_url: Optional[str] = None
            try:
                doc_url = find_information_table_url(
                    filing, http_get=http_get,
                )
                if doc_url is None:
                    stats["filings_error"] += 1
                    log.warning(
                        "no info table for %s %s",
                        inst_name, filing_date,
                    )
                    continue
                xml_content = download_13f_document(
                    doc_url, http_get=http_get,
                )
            except Exception as exc:  # noqa: BLE001
                stats["filings_error"] += 1
                log.warning(
                    "download failed %s %s: %s",
                    inst_name, filing_date, exc,
                )
                continue

            # Parse phase: parse_13f_xml catches ET.ParseError internally
            # and returns []. A raised exception here is something else
            # (programmer error, unexpected content) and gets logged as
            # parse_error so we don't retry it every run.
            parse_status: str
            try:
                holdings = parse_13f_xml(xml_content)
                parse_status = "success" if holdings else "empty"
            except Exception as exc:  # noqa: BLE001
                holdings = []
                parse_status = "parse_error"
                log.warning(
                    "parse failed %s %s: %s",
                    inst_name, filing_date, exc,
                )

            ts = now_iso()
            before_insert = conn.total_changes
            for h in holdings:
                cusip = h.get("cusip")
                if not cusip:
                    continue
                ticker = cusip_resolver.resolve_cusip(cusip, conn)
                if ticker is None:
                    stats["cusips_unresolved"] += 1
                conn.execute(
                    "INSERT OR IGNORE INTO institution_holdings "
                    "(institution_id, filing_date, period_of_report, "
                    " ticker, cusip, shares, market_value, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        inst_id, filing_date,
                        filing["period_of_report"],
                        ticker, cusip,
                        h.get("shares"),
                        _normalize_market_value(
                            filing_date, h.get("market_value")
                        ),
                        ts, ts,
                    ),
                )
            inserted = conn.total_changes - before_insert

            conn.execute(
                "INSERT INTO filings_log "
                "(institution_id, filing_date, period_of_report, "
                " accession_number, document_url, holdings_count, "
                " parse_status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    inst_id, filing_date,
                    filing["period_of_report"],
                    accession, doc_url, len(holdings),
                    parse_status, ts, ts,
                ),
            )
            conn.commit()

            if parse_status == "success":
                stats["filings_processed"] += 1
                stats["holdings_inserted"] += inserted
                log.info(
                    "ingested %s %s: %d holdings",
                    inst_name, filing_date, inserted,
                )
            elif parse_status == "empty":
                stats["filings_empty"] += 1
                log.info(
                    "empty filing %s %s",
                    inst_name, filing_date,
                )
            else:
                stats["filings_error"] += 1

    return summary
