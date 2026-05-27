"""Ticker → CIK resolver, backed by the SEC's company_tickers.json feed.

Spec §4.3.1 — refresh the cache when it's older than 7 days. Unresolved
tickers (delisted, foreign, ETF, etc.) log a warning and continue rather
than aborting the whole run.

The cache is the ``ticker_cik_map`` table (created by Module 0, schema
§2.7). Storing it in the same SQLite file the rest of the pipeline uses
keeps everything in one place and lets ``last_refreshed`` drive the
TTL check.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

from .edgar_client import http_get

log = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE_TTL_DAYS = 7


def _pad_cik(cik: int | str) -> str:
    digits = "".join(c for c in str(cik) if c.isdigit())
    return digits.zfill(10)


def _cache_is_stale(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT MAX(last_refreshed) FROM ticker_cik_map"
    ).fetchone()
    if row is None or row[0] is None:
        return True
    try:
        last = datetime.fromisoformat(row[0])
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - last) > timedelta(days=CACHE_TTL_DAYS)


def refresh_ticker_cik_cache(conn: sqlite3.Connection, *, force: bool = False) -> int:
    """Fetch SEC's company_tickers.json and upsert into ``ticker_cik_map``.
    Returns the number of (ticker, cik, name) rows refreshed.

    No-op (returns 0) if cache is still fresh and ``force`` is False.
    """
    if not force and not _cache_is_stale(conn):
        return 0

    log.info("refreshing ticker_cik_map from %s", SEC_TICKERS_URL)
    body = http_get(SEC_TICKERS_URL, accept="application/json")
    payload = json.loads(body)
    # SEC's payload shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    n = 0
    with conn:
        for entry in payload.values():
            ticker = (entry.get("ticker") or "").strip().upper()
            cik = entry.get("cik_str")
            name = (entry.get("title") or "").strip() or None
            if not ticker or cik is None:
                continue
            conn.execute(
                """
                INSERT INTO ticker_cik_map (ticker, cik, name, last_refreshed)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    cik = excluded.cik,
                    name = excluded.name,
                    last_refreshed = excluded.last_refreshed
                """,
                (ticker, _pad_cik(cik), name, now),
            )
            n += 1
    log.info("ticker_cik_map refreshed: %d rows", n)
    return n


def resolve_tickers(
    conn: sqlite3.Connection, tickers: list[str], *, allow_refresh: bool = True,
) -> tuple[dict[str, str], list[str]]:
    """Return ({ticker: padded_cik}, [unresolved_tickers]).

    On a stale cache (and ``allow_refresh=True``), refresh first.
    """
    if allow_refresh:
        refresh_ticker_cik_cache(conn)
    out: dict[str, str] = {}
    unresolved: list[str] = []
    upper = [t.strip().upper() for t in tickers if t and t.strip()]
    if not upper:
        return out, []
    qmarks = ",".join("?" * len(upper))
    rows = conn.execute(
        f"SELECT ticker, cik FROM ticker_cik_map WHERE ticker IN ({qmarks})",
        upper,
    ).fetchall()
    found = {r["ticker"]: r["cik"] for r in rows}
    for t in upper:
        if t in found:
            out[t] = found[t]
        else:
            unresolved.append(t)
    if unresolved:
        log.warning("ticker→CIK unresolved (%d): %s",
                    len(unresolved), ",".join(unresolved[:10]) + ("…" if len(unresolved) > 10 else ""))
    return out, unresolved
