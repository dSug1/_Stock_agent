"""EDGAR 13F-HR fetcher + parser + ingester for 2_Funds_parser.

Ported from 1_not_used/src/layer_minus1/edgar_13f_parser.py and
simplified: no tier/multiplier/classification/TWOS. Just:

    EDGAR submissions API -> list 13F-HR filings filtered by filing_date
    -> locate info-table XML -> parse rows -> resolve CUSIP -> store
    in `holdings`, log filing in `filings_log`.

Dedup is at the (fund_id, accession_number) level via filings_log.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Callable, Optional

# Prefer defusedxml (blocks entity-expansion / external-entity attacks on the
# untrusted EDGAR XML we parse); fall back to the stdlib parser if it's absent.
try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:  # pragma: no cover - defusedxml is a declared dependency
    from xml.etree.ElementTree import fromstring as _xml_fromstring

from database.db import MARKET_VALUE_RAW_USD_CUTOFF

log = logging.getLogger(__name__)

# Cap on any single EDGAR response read into memory (OOM guard against a
# malfunctioning / hostile upstream). 64 MiB is well above the largest real
# 13F info-table XML or submissions JSON.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024

EDGAR_USER_AGENT = "StockPicker contact@stockpicker.local"
# SEC fair-use is 10 req/sec per IP. We target 9.5 to stay safely under
# the cap while leaving a thin margin for clock jitter / sibling scripts.
EDGAR_RATE_PER_SEC = 9.5
EDGAR_MAX_WORKERS = 5
EDGAR_SUBMISSIONS_URL = (
    "https://data.sec.gov/submissions/CIK{cik}.json"
)
EDGAR_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

DEFAULT_FROM_DATE = "2025-01-01"


HttpGet = Callable[[str], bytes]


class _RateLimiter:
    """Process-global token bucket for SEC EDGAR requests.

    Gates every worker thread through a single lock so the aggregate
    request rate does not exceed `rate_per_sec`, regardless of how
    many threads are calling concurrently.
    """

    def __init__(self, rate_per_sec: float) -> None:
        self._interval = 1.0 / rate_per_sec
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait <= 0:
                self._next = now + self._interval
                wait = 0.0
            else:
                self._next += self._interval
        if wait > 0:
            time.sleep(wait)


_EDGAR_LIMITER = _RateLimiter(EDGAR_RATE_PER_SEC)


def _read_capped(resp, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read up to `max_bytes`; raise if the body exceeds the cap.

    Reads one byte past the limit so a body that is exactly `max_bytes` is
    accepted while anything larger is rejected before it is fully buffered.
    """
    body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} byte cap")
    return body


def _default_http_get(url: str) -> bytes:
    _EDGAR_LIMITER.acquire()
    req = urllib.request.Request(
        url, headers={"User-Agent": EDGAR_USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return _read_capped(resp)


def _pad_cik(cik: str) -> str:
    digits = "".join(c for c in cik if c.isdigit())
    return digits.zfill(10)


def _normalize_market_value(
    filing_date: str, market_value: Optional[int]
) -> Optional[int]:
    if market_value is None:
        return None
    if filing_date < MARKET_VALUE_RAW_USD_CUTOFF:
        return market_value * 1000
    return market_value


def fetch_fund_filings(
    cik: str,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    *,
    http_get: Optional[HttpGet] = None,
) -> list[dict]:
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
    try:
        root = _xml_fromstring(xml_content)
    except Exception as exc:  # ET.ParseError or defusedxml entity-attack guard
        log.warning("malformed/unsafe 13F XML: %s", exc)
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
            elif name == "titleOfClass":
                rec["title_of_class"] = (child.text or "").strip() or None
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
            elif name == "putCall":
                rec["put_call"] = (child.text or "").strip() or None
        if shares_type != "SH":
            continue
        if not rec.get("cusip"):
            continue
        holdings.append({
            "name_of_issuer": rec.get("name_of_issuer"),
            "cusip": rec["cusip"],
            "shares": rec.get("shares"),
            "market_value": rec.get("market_value"),
            "title_of_class": rec.get("title_of_class"),
            "put_call": rec.get("put_call"),
        })
    return holdings


def _fetch_and_parse_filing(
    filing: dict, http_get: HttpGet,
) -> dict:
    """Fetch the info-table XML for one filing and parse it.

    Returns a dict with keys: filing, doc_url, holdings, parse_status,
    error. Designed to be called from a thread pool — all network I/O
    happens here, the caller writes the DB serially.
    """
    try:
        doc_url = find_information_table_url(filing, http_get=http_get)
        if doc_url is None:
            return {
                "filing": filing, "doc_url": None, "holdings": [],
                "parse_status": "no_info_table", "error": None,
            }
        xml_content = download_13f_document(doc_url, http_get=http_get)
    except Exception as exc:  # noqa: BLE001
        return {
            "filing": filing, "doc_url": None, "holdings": [],
            "parse_status": "download_error", "error": exc,
        }
    try:
        holdings = parse_13f_xml(xml_content)
        parse_status = "success" if holdings else "empty"
    except Exception as exc:  # noqa: BLE001
        holdings = []
        parse_status = "parse_error"
    return {
        "filing": filing, "doc_url": doc_url, "holdings": holdings,
        "parse_status": parse_status, "error": None,
    }


def backfill_missing_issuer_names(
    conn: sqlite3.Connection,
    *,
    http_get: Optional[HttpGet] = None,
) -> dict:
    """Fill in `holdings.name_of_issuer` for filings that already have a
    filings_log row but were ingested before `name_of_issuer` existed
    (e.g., pre-seeded from 1_not_used). Re-downloads the info-table
    XML once per affected filing and UPDATEs matching rows by CUSIP.

    Idempotent: once a filing's rows are filled, it no longer matches
    the "IS NULL" guard and is skipped on subsequent runs.
    """
    from database.db import now_iso

    http_get = http_get or _default_http_get

    rows = conn.execute(
        "SELECT DISTINCT fl.id AS filing_id, fl.fund_id, fl.filing_date, "
        "       fl.document_url, f.name AS fund_name "
        "FROM filings_log fl "
        "JOIN funds f ON f.id = fl.fund_id "
        "JOIN holdings h "
        "  ON h.fund_id = fl.fund_id AND h.filing_date = fl.filing_date "
        "WHERE h.name_of_issuer IS NULL "
        "  AND fl.document_url IS NOT NULL "
        "ORDER BY fl.fund_id, fl.filing_date"
    ).fetchall()

    stats = {"filings_scanned": len(rows), "rows_updated": 0, "errors": 0}
    if not rows:
        return stats

    log.info(
        "name_of_issuer backfill: %d filings to rescan (%d workers)",
        len(rows), EDGAR_MAX_WORKERS,
    )

    def _fetch(row) -> tuple:
        try:
            xml_content = download_13f_document(
                row["document_url"], http_get=http_get,
            )
            return (row, parse_13f_xml(xml_content), None)
        except Exception as exc:  # noqa: BLE001
            return (row, None, exc)

    # Parallel fetches gated by the global SEC rate limiter. DB writes
    # stay on this thread because SQLite has a single writer.
    with ThreadPoolExecutor(max_workers=EDGAR_MAX_WORKERS) as pool:
        futures = [pool.submit(_fetch, r) for r in rows]
        for future in as_completed(futures):
            row, parsed, err = future.result()
            if err is not None:
                stats["errors"] += 1
                log.warning(
                    "backfill download failed %s %s: %s",
                    row["fund_name"], row["filing_date"], err,
                )
                continue
            ts = now_iso()
            before = conn.total_changes
            for h in parsed:
                name = h.get("name_of_issuer")
                cusip = h.get("cusip")
                if not name or not cusip:
                    continue
                conn.execute(
                    "UPDATE holdings SET name_of_issuer = ?, "
                    "       updated_at = ? "
                    "WHERE fund_id = ? AND filing_date = ? AND cusip = ? "
                    "  AND name_of_issuer IS NULL",
                    (name, ts, row["fund_id"], row["filing_date"], cusip),
                )
            conn.commit()
            stats["rows_updated"] += conn.total_changes - before

    return stats


def backfill_share_type_fields(
    conn: sqlite3.Connection,
    *,
    http_get: Optional[HttpGet] = None,
) -> dict:
    """Fill in `holdings.title_of_class` and `holdings.put_call` for
    filings ingested before those columns existed. Re-downloads each
    affected filing's info-table XML once and UPDATEs rows by CUSIP.

    Idempotent: once all rows for a filing have `title_of_class`
    populated, the filing no longer matches the "IS NULL" guard and
    is skipped on subsequent runs. Rows whose filings have no XML
    available (document_url NULL) are left as-is and logged.

    Note: `put_call` stays NULL for the overwhelming majority of
    rows (common stock has no putCall element). We key the guard on
    `title_of_class IS NULL` alone — if title_of_class is populated,
    put_call has been considered for that row regardless of value.
    """
    from database.db import now_iso

    http_get = http_get or _default_http_get

    rows = conn.execute(
        "SELECT DISTINCT fl.id AS filing_id, fl.fund_id, fl.filing_date, "
        "       fl.document_url, f.name AS fund_name "
        "FROM filings_log fl "
        "JOIN funds f ON f.id = fl.fund_id "
        "JOIN holdings h "
        "  ON h.fund_id = fl.fund_id AND h.filing_date = fl.filing_date "
        "WHERE h.title_of_class IS NULL "
        "  AND fl.document_url IS NOT NULL "
        "ORDER BY fl.fund_id, fl.filing_date"
    ).fetchall()

    stats = {"filings_scanned": len(rows), "rows_updated": 0, "errors": 0}
    if not rows:
        return stats

    log.info(
        "share-type backfill: %d filings to rescan (%d workers)",
        len(rows), EDGAR_MAX_WORKERS,
    )

    def _fetch(row) -> tuple:
        try:
            xml_content = download_13f_document(
                row["document_url"], http_get=http_get,
            )
            return (row, parse_13f_xml(xml_content), None)
        except Exception as exc:  # noqa: BLE001
            return (row, None, exc)

    with ThreadPoolExecutor(max_workers=EDGAR_MAX_WORKERS) as pool:
        futures = [pool.submit(_fetch, r) for r in rows]
        for future in as_completed(futures):
            row, parsed, err = future.result()
            if err is not None:
                stats["errors"] += 1
                log.warning(
                    "share-type backfill download failed %s %s: %s",
                    row["fund_name"], row["filing_date"], err,
                )
                continue
            ts = now_iso()
            before = conn.total_changes
            for h in parsed:
                cusip = h.get("cusip")
                if not cusip:
                    continue
                conn.execute(
                    "UPDATE holdings SET title_of_class = ?, "
                    "       put_call = ?, updated_at = ? "
                    "WHERE fund_id = ? AND filing_date = ? AND cusip = ? "
                    "  AND title_of_class IS NULL",
                    (
                        h.get("title_of_class"),
                        h.get("put_call"),
                        ts,
                        row["fund_id"],
                        row["filing_date"],
                        cusip,
                    ),
                )
            conn.commit()
            stats["rows_updated"] += conn.total_changes - before

    return stats


def backfill_tickers_by_sec_name(
    conn: sqlite3.Connection,
    *,
    http_get: Optional[HttpGet] = None,
) -> dict:
    """For holdings with NULL ticker but a known name_of_issuer, try
    to resolve the ticker via SEC's company_tickers.json. Updates
    every matching row and stamps `ticker_source='sec_name'` so the
    provenance stays separable from OpenFIGI-derived tickers.

    Runs every invocation; fast after the first time because the SEC
    file is cached locally and unmatched names are not retried until
    a new name_of_issuer appears in the table.
    """
    from database.db import now_iso
    from layer_1 import sec_ticker_resolver

    stats = {
        "distinct_names": 0,
        "names_matched": 0,
        "rows_updated": 0,
    }

    # Distinct names needing resolution.
    names = [
        row["name_of_issuer"]
        for row in conn.execute(
            "SELECT DISTINCT name_of_issuer FROM holdings "
            "WHERE ticker IS NULL AND name_of_issuer IS NOT NULL "
            "  AND name_of_issuer != ''"
        ).fetchall()
    ]
    stats["distinct_names"] = len(names)
    if not names:
        return stats

    try:
        sec_ticker_resolver.download_sec_tickers(http_get=http_get)
    except Exception as exc:  # noqa: BLE001
        log.warning("SEC company_tickers.json download failed: %s", exc)
        return stats

    name_index = sec_ticker_resolver.load_sec_name_index()
    if not name_index:
        log.warning("SEC name index is empty; skipping name-match backfill")
        return stats

    ts = now_iso()
    for name in names:
        match = sec_ticker_resolver.resolve_name(name, name_index)
        if match is None:
            continue
        ticker = match["ticker"]
        if not ticker:
            continue
        before = conn.total_changes
        conn.execute(
            "UPDATE holdings SET ticker = ?, ticker_source = 'sec_name', "
            "       updated_at = ? "
            "WHERE ticker IS NULL AND name_of_issuer = ?",
            (ticker, ts, name),
        )
        updated = conn.total_changes - before
        if updated:
            stats["names_matched"] += 1
            stats["rows_updated"] += updated
    conn.commit()
    return stats


def ingest_all_funds(
    conn: sqlite3.Connection,
    from_date: str = DEFAULT_FROM_DATE,
    to_date: Optional[str] = None,
    *,
    http_get: Optional[HttpGet] = None,
) -> dict:
    """Download every 13F-HR for every fund in [from_date, to_date].

    Dedup by (fund_id, accession_number) via filings_log. Before the
    main loop, a backfill pass re-fetches the XML of any filing whose
    holdings are missing `name_of_issuer` (typically pre-seeded rows).
    """
    from database.db import now_iso
    from layer_1 import cusip_resolver

    if to_date is None:
        to_date = date.today().isoformat()
    http_get = http_get or _default_http_get

    backfill_stats = backfill_missing_issuer_names(conn, http_get=http_get)
    if backfill_stats["filings_scanned"]:
        log.info(
            "name_of_issuer backfill: %d filings scanned, %d rows updated, "
            "%d errors",
            backfill_stats["filings_scanned"],
            backfill_stats["rows_updated"],
            backfill_stats["errors"],
        )

    share_type_backfill = backfill_share_type_fields(conn, http_get=http_get)
    if share_type_backfill["filings_scanned"]:
        log.info(
            "share-type backfill: %d filings scanned, %d rows updated, "
            "%d errors",
            share_type_backfill["filings_scanned"],
            share_type_backfill["rows_updated"],
            share_type_backfill["errors"],
        )

    ticker_backfill = backfill_tickers_by_sec_name(conn, http_get=http_get)
    if ticker_backfill["distinct_names"]:
        log.info(
            "sec-name ticker backfill: %d distinct names, "
            "%d matched, %d rows updated",
            ticker_backfill["distinct_names"],
            ticker_backfill["names_matched"],
            ticker_backfill["rows_updated"],
        )

    funds = conn.execute(
        "SELECT id, name, cik FROM funds "
        "WHERE cik IS NOT NULL ORDER BY id"
    ).fetchall()

    summary: dict = {
        "_backfill": backfill_stats,
        "_share_type_backfill": share_type_backfill,
        "_ticker_backfill": ticker_backfill,
    }

    for fund in funds:
        fund_id = fund["id"]
        fund_name = fund["name"]
        cik = fund["cik"]
        stats = {
            "filings_processed": 0,
            "holdings_inserted": 0,
            "cusips_unresolved": 0,
            "filings_skipped": 0,
            "filings_empty": 0,
            "filings_error": 0,
        }
        summary[fund_name] = stats

        try:
            filings = fetch_fund_filings(
                cik, from_date, to_date, http_get=http_get,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "submissions fetch failed for %s (CIK %s): %s",
                fund_name, cik, exc,
            )
            continue

        pending = []
        for filing in filings:
            accession = filing["accession_number"]
            existing = conn.execute(
                "SELECT 1 FROM filings_log "
                "WHERE fund_id = ? AND accession_number = ? LIMIT 1",
                (fund_id, accession),
            ).fetchone()
            if existing is not None:
                stats["filings_skipped"] += 1
                continue
            pending.append(filing)

        # Parallel EDGAR fetches (index.json + info-table XML) for this
        # fund's pending filings. Workers share the global rate limiter
        # so the aggregate request rate stays below SEC's ceiling.
        fetch_results: list[dict] = []
        if pending:
            with ThreadPoolExecutor(max_workers=EDGAR_MAX_WORKERS) as pool:
                futures = [
                    pool.submit(_fetch_and_parse_filing, f, http_get)
                    for f in pending
                ]
                for future in as_completed(futures):
                    fetch_results.append(future.result())

        for result in fetch_results:
            filing = result["filing"]
            filing_date = filing["filing_date"]
            accession = filing["accession_number"]
            doc_url = result.get("doc_url")

            if result["error"] is not None:
                stats["filings_error"] += 1
                log.warning(
                    "download failed %s %s: %s",
                    fund_name, filing_date, result["error"],
                )
                continue
            if doc_url is None:
                stats["filings_error"] += 1
                log.warning(
                    "no info table for %s %s",
                    fund_name, filing_date,
                )
                continue

            holdings = result["holdings"]
            parse_status = result["parse_status"]
            if parse_status == "parse_error":
                log.warning(
                    "parse failed %s %s",
                    fund_name, filing_date,
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
                ticker_source = "openfigi" if ticker else None
                conn.execute(
                    "INSERT OR IGNORE INTO holdings "
                    "(fund_id, filing_date, period_of_report, "
                    " name_of_issuer, ticker, ticker_source, cusip, "
                    " shares, market_value, title_of_class, put_call, "
                    " created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fund_id, filing_date,
                        filing["period_of_report"],
                        h.get("name_of_issuer"),
                        ticker, ticker_source, cusip,
                        h.get("shares"),
                        _normalize_market_value(
                            filing_date, h.get("market_value")
                        ),
                        h.get("title_of_class"),
                        h.get("put_call"),
                        ts, ts,
                    ),
                )
            inserted = conn.total_changes - before_insert

            conn.execute(
                "INSERT INTO filings_log "
                "(fund_id, filing_date, period_of_report, "
                " accession_number, document_url, holdings_count, "
                " parse_status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    fund_id, filing_date,
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
                    fund_name, filing_date, inserted,
                )
            elif parse_status == "empty":
                stats["filings_empty"] += 1
            else:
                stats["filings_error"] += 1

    return summary
