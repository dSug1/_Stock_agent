from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.request
from typing import Callable, Optional

from database.db import now_iso

log = logging.getLogger(__name__)

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_BATCH_SIZE = 10
OPENFIGI_RATE_LIMIT_SLEEP = 60.0 / 25.0  # 25 requests/minute → 2.4s
US_EXCH_CODES = frozenset({"US", "UN", "UA", "UW", "UR"})

# Accept only these security types. "Depositary Receipt" covers ADRs.
ACCEPTED_SECURITY_TYPES = frozenset({
    "Common Stock",
    "Depositary Receipt",
})

# Reject any record whose securityType/securityType2 contains any of these
# substrings (case-insensitive). 13F filings routinely include ETFs, closed-end
# trusts, preferreds, warrants, and bond units that trade on equity exchanges
# but are not common-stock holdings we want to score.
REJECTED_SECURITY_SUBSTRINGS = (
    "ETF",
    "ETP",
    "Fund",
    "Trust",
    "Note",
    "Bond",
    "Preferred",
    "Right",
    "Warrant",
    "Unit",
)


HttpPost = Callable[[str, bytes, dict], bytes]


def _default_http_post(url: str, body: bytes, headers: dict) -> bytes:
    req = urllib.request.Request(
        url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _cached(conn: sqlite3.Connection, cusip: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT cusip, ticker, exchange, security_type, resolved_date "
        "FROM cusip_ticker_map WHERE cusip = ?",
        (cusip,),
    ).fetchone()
    if row is None:
        return None
    return {
        "cusip": row["cusip"],
        "ticker": row["ticker"],
        "exchange": row["exchange"],
        "security_type": row["security_type"],
        "resolved_date": row["resolved_date"],
    }


def _write_cache(
    conn: sqlite3.Connection,
    cusip: str,
    ticker: Optional[str],
    exchange: Optional[str],
    security_type: Optional[str],
) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT INTO cusip_ticker_map "
        "(cusip, ticker, exchange, security_type, "
        " resolved_date, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(cusip) DO UPDATE SET "
        " ticker = excluded.ticker, "
        " exchange = excluded.exchange, "
        " security_type = excluded.security_type, "
        " resolved_date = excluded.resolved_date, "
        " updated_at = excluded.updated_at",
        (cusip, ticker, exchange, security_type, ts, ts, ts),
    )
    conn.commit()


def _security_type_is_equity(sec_type: Optional[str]) -> bool:
    """True if the OpenFIGI securityType/securityType2 string should be
    kept. Rejects before accepting so an "ETF Common Stock" style
    composite can't slip through on the ACCEPT check alone.
    """
    if not sec_type:
        return False
    lowered = sec_type.lower()
    for bad in REJECTED_SECURITY_SUBSTRINGS:
        if bad.lower() in lowered:
            return False
    return sec_type in ACCEPTED_SECURITY_TYPES


def _record_is_equity(rec: dict) -> bool:
    t1 = rec.get("securityType")
    t2 = rec.get("securityType2")
    # Reject if either field carries a disallowed substring.
    for sec_type in (t1, t2):
        if not sec_type:
            continue
        lowered = sec_type.lower()
        for bad in REJECTED_SECURITY_SUBSTRINGS:
            if bad.lower() in lowered:
                return False
    # Accept only if at least one field matches the allow list.
    return (
        t1 in ACCEPTED_SECURITY_TYPES
        or t2 in ACCEPTED_SECURITY_TYPES
    )


def _pick_us_equity(records: list[dict]) -> Optional[dict]:
    """Return the first record that is (a) on a US equity exchange and
    (b) has a securityType/securityType2 we accept. Returns None if no
    record survives both filters.
    """
    for rec in records:
        if rec.get("exchCode") not in US_EXCH_CODES:
            continue
        if not _record_is_equity(rec):
            continue
        return rec
    return None


def _picked_security_type(rec: dict) -> Optional[str]:
    t1 = rec.get("securityType")
    if t1 in ACCEPTED_SECURITY_TYPES:
        return t1
    t2 = rec.get("securityType2")
    if t2 in ACCEPTED_SECURITY_TYPES:
        return t2
    return t1 or t2


def resolve_cusip_batch(
    cusip_list: list[str],
    conn: sqlite3.Connection,
    *,
    http_post: Optional[HttpPost] = None,
) -> dict[str, Optional[str]]:
    """Resolve CUSIPs → tickers; checks cache first, batches uncached.

    Returns {cusip: ticker_or_None} for every input CUSIP.
    Unresolved CUSIPs (no match, or match was a non-equity instrument like
    an ETF / preferred / bond) are cached as NULL and logged at DEBUG —
    13F filings legitimately contain such holdings so a WARNING would be
    noise.
    """
    http_post = http_post or _default_http_post
    result: dict[str, Optional[str]] = {}
    uncached: list[str] = []
    for cusip in cusip_list:
        hit = _cached(conn, cusip)
        if hit is not None:
            result[cusip] = hit["ticker"]
        else:
            uncached.append(cusip)

    for start in range(0, len(uncached), OPENFIGI_BATCH_SIZE):
        batch = uncached[start:start + OPENFIGI_BATCH_SIZE]
        body = json.dumps(
            [{"idType": "ID_CUSIP", "idValue": c} for c in batch]
        ).encode("utf-8")
        try:
            raw = http_post(
                OPENFIGI_URL,
                body,
                {"Content-Type": "application/json"},
            )
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("OpenFIGI batch failed: %s", exc)
            for cusip in batch:
                _write_cache(conn, cusip, None, None, None)
                result[cusip] = None
            time.sleep(OPENFIGI_RATE_LIMIT_SLEEP)
            continue

        for cusip, entry in zip(batch, payload):
            data = entry.get("data") if isinstance(entry, dict) else None
            pick = _pick_us_equity(data) if data else None
            if pick is None:
                log.debug(
                    "cusip %s filtered: no US-equity common-stock match",
                    cusip,
                )
                _write_cache(conn, cusip, None, None, None)
                result[cusip] = None
            else:
                ticker = pick.get("ticker")
                sec_type = _picked_security_type(pick)
                _write_cache(
                    conn, cusip, ticker,
                    pick.get("exchCode"), sec_type,
                )
                result[cusip] = ticker

        time.sleep(OPENFIGI_RATE_LIMIT_SLEEP)

    return result


def resolve_cusip(
    cusip: str,
    conn: sqlite3.Connection,
    *,
    http_post: Optional[HttpPost] = None,
) -> Optional[str]:
    """Return a ticker for the CUSIP, or None if unresolved / non-equity."""
    hit = _cached(conn, cusip)
    if hit is not None:
        return hit["ticker"]
    return resolve_cusip_batch([cusip], conn, http_post=http_post).get(
        cusip
    )
