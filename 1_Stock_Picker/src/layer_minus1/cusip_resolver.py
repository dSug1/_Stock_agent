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


HttpPost = Callable[[str, bytes, dict], bytes]


def _default_http_post(url: str, body: bytes, headers: dict) -> bytes:
    req = urllib.request.Request(
        url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _cached(conn: sqlite3.Connection, cusip: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT cusip, ticker, exchange, resolved_date "
        "FROM cusip_ticker_map WHERE cusip = ?",
        (cusip,),
    ).fetchone()
    if row is None:
        return None
    return {
        "cusip": row["cusip"],
        "ticker": row["ticker"],
        "exchange": row["exchange"],
        "resolved_date": row["resolved_date"],
    }


def _write_cache(
    conn: sqlite3.Connection,
    cusip: str,
    ticker: Optional[str],
    exchange: Optional[str],
) -> None:
    ts = now_iso()
    conn.execute(
        "INSERT INTO cusip_ticker_map "
        "(cusip, ticker, exchange, resolved_date, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(cusip) DO UPDATE SET "
        " ticker = excluded.ticker, "
        " exchange = excluded.exchange, "
        " resolved_date = excluded.resolved_date, "
        " updated_at = excluded.updated_at",
        (cusip, ticker, exchange, ts, ts, ts),
    )
    conn.commit()


def _pick_us_equity(records: list[dict]) -> Optional[dict]:
    for rec in records:
        if rec.get("exchCode") in US_EXCH_CODES:
            return rec
    return None


def resolve_cusip_batch(
    cusip_list: list[str],
    conn: sqlite3.Connection,
    *,
    http_post: Optional[HttpPost] = None,
) -> dict[str, Optional[str]]:
    """Resolve CUSIPs → tickers; checks cache first, batches uncached.

    Returns {cusip: ticker_or_None} for every input CUSIP.
    Unresolved CUSIPs get cached as NULL and logged at WARNING level.
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
                _write_cache(conn, cusip, None, None)
                result[cusip] = None
            time.sleep(OPENFIGI_RATE_LIMIT_SLEEP)
            continue

        for cusip, entry in zip(batch, payload):
            data = entry.get("data") if isinstance(entry, dict) else None
            pick = _pick_us_equity(data) if data else None
            if pick is None:
                log.warning("unresolved CUSIP: %s", cusip)
                _write_cache(conn, cusip, None, None)
                result[cusip] = None
            else:
                ticker = pick.get("ticker")
                _write_cache(conn, cusip, ticker, pick.get("exchCode"))
                result[cusip] = ticker

        time.sleep(OPENFIGI_RATE_LIMIT_SLEEP)

    return result


def resolve_cusip(
    cusip: str,
    conn: sqlite3.Connection,
    *,
    http_post: Optional[HttpPost] = None,
) -> Optional[str]:
    """Return a ticker for the CUSIP, or None if unresolved."""
    hit = _cached(conn, cusip)
    if hit is not None:
        return hit["ticker"]
    return resolve_cusip_batch([cusip], conn, http_post=http_post).get(
        cusip
    )
