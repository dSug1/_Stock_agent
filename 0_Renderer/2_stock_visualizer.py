#!/usr/bin/env python3
"""
2_stock_visualizer.py
Interactive US stock price chart served via a local Flask web server.

Features
--------
- Accepts ticker symbol OR company legal name (resolved via Yahoo Finance search API)
- Granularity auto-selected based on requested time period
- SQLite cache in _outputs/cache/prices.db (tables: meta, bars, period_fetch)
  · metadata refreshed weekly (company name, sector, exchange …)
  · price data refreshed per-interval TTL; every 6 s during market hours
  · bars keyed by (ticker, interval, t) and shared across periods at the same interval
- One static HTML template in _outputs/templates/1_chart_template.html
- Browser chart (TradingView Lightweight Charts) auto-scales axes
- Live poll every 6 s only when the exchange is open

Usage
-----
    python 2_stock_visualizer.py          # prompts in terminal, opens browser
"""

import time, datetime, threading, webbrowser, sqlite3, json
import email.utils
from pathlib import Path
from contextlib import contextmanager

import requests
import yfinance as yf
import pandas as pd
from flask import Flask, jsonify, render_template, request, send_from_directory
import market_calendars as mc

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT      = Path(__file__).parent
OUT_DIR   = ROOT / "_outputs"
CACHE_DIR = OUT_DIR / "cache"
TMPL_DIR  = OUT_DIR / "templates"

for _d in [CACHE_DIR, TMPL_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── Period → interval mapping ─────────────────────────────────────────────────
PERIOD_INTERVAL: dict[str, str] = {
    "1d":  "1m",
    "5d":  "5m",
    "1mo": "1h",
    "3mo": "1d",
    "6mo": "1d",
    "1y":  "1d",
    "2y":  "1wk",
    "5y":  "1wk",
    "max": "1mo",
}

VALID_PERIODS = list(PERIOD_INTERVAL.keys())

# Market-closed fallback. yfinance frequently returns an EMPTY frame for an
# intraday request whose period is "today" (e.g. period="1d", interval="1m")
# when the market is closed / pre-open — there is no current session to return.
# When that happens we retry with a wider window so the most recent *prior*
# session's bars are still captured. The frontend anchors on the last session
# and shows its final bar as the price, i.e. the previous market close.
INTRADAY_FALLBACK_PERIOD: dict[str, str] = {
    "1m":  "5d",    # 1m history is limited to ~7 days upstream
    "5m":  "1mo",
    "1h":  "3mo",
}

# Cache TTL per interval (seconds).
# During market hours the 1m/5m/1h intervals use 6 s TTL to drive live updates.
# 1wk/1mo are 1 day (not 7/30 days) so the trailing, still-forming week/month
# bar refreshes daily and the coarse charts stay current — older bars in those
# series never change, so re-fetching daily costs one cheap call (D20).
CACHE_TTL: dict[str, int] = {
    "1m":  6,
    "5m":  30,
    "1h":  300,
    "1d":  86_400,
    "1wk": 86_400,
    "1mo": 86_400,
}

# Meta TTL: refresh company info once per week
META_TTL = 604_800

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__, template_folder=str(TMPL_DIR))


# ═══════════════════════════════════════════════════════════════════════════════
#  Internet clock calibration
#
#  Strategy: fire a HEAD request to a reliable HTTPS host and read the RFC 2822
#  "Date:" response header, which carries the server's UTC time.  We measure the
#  round-trip and subtract half of it so the offset is centred on the actual
#  server timestamp.  The result is stored as a float (seconds) and refreshed
#  every CLOCK_SYNC_INTERVAL seconds in a background thread.
#
#  Fallback order: Google → Yahoo Finance → (skip, use local clock, offset = 0)
# ═══════════════════════════════════════════════════════════════════════════════

_CLOCK_SYNC_HOSTS = [
    "https://www.google.com",
    "https://finance.yahoo.com",
]
CLOCK_SYNC_INTERVAL = 1800   # re-calibrate every 30 minutes

# _clock_offset_s: add this to datetime.utcnow() to get true UTC
_clock_offset_s: float = 0.0
_clock_sync_lock = threading.Lock()
_clock_sync_source: str = "local (not yet synced)"


def _fetch_clock_offset() -> tuple[float, str]:
    """
    Attempt a HEAD request to each fallback host in order.
    Returns (offset_seconds, source_description).
    offset_seconds = internet_utc_epoch − local_utc_epoch  (may be negative).
    """
    for url in _CLOCK_SYNC_HOSTS:
        try:
            t_before = time.time()
            r = requests.head(url, timeout=4, allow_redirects=True,
                              headers={"User-Agent": "Mozilla/5.0"})
            t_after = time.time()
            date_hdr = r.headers.get("Date", "")
            if not date_hdr:
                continue
            # RFC 2822 → datetime (always UTC)
            internet_dt  = email.utils.parsedate_to_datetime(date_hdr)
            internet_ts  = internet_dt.timestamp()
            # Estimate the true request midpoint to reduce latency bias
            local_mid_ts = (t_before + t_after) / 2.0
            offset       = internet_ts - local_mid_ts
            rtt_ms       = (t_after - t_before) * 1000
            source = f"{url.split('/')[2]}  (RTT {rtt_ms:.0f} ms, offset {offset:+.2f} s)"
            return offset, source
        except Exception:
            continue
    return 0.0, "local (sync failed)"


def _sync_clock_once() -> None:
    global _clock_offset_s, _clock_sync_source
    offset, source = _fetch_clock_offset()
    with _clock_sync_lock:
        _clock_offset_s   = offset
        _clock_sync_source = source
    print(f"  [clock] synced → {source}")


def _clock_sync_loop() -> None:
    """Background thread: re-sync every CLOCK_SYNC_INTERVAL seconds. The first
    sync is done synchronously in main() before this thread starts."""
    while True:
        time.sleep(CLOCK_SYNC_INTERVAL)
        _sync_clock_once()


def calibrated_utcnow() -> datetime.datetime:
    """Return current UTC time corrected by the internet-measured clock offset."""
    with _clock_sync_lock:
        offset = _clock_offset_s
    return datetime.datetime.utcnow() + datetime.timedelta(seconds=offset)


def is_market_open(exchange_code: str = mc.DEFAULT_EXCHANGE) -> bool:
    """
    True if the given exchange is currently open for regular trading.
    Uses calibrated_utcnow() so the result is independent of the local
    system clock setting or timezone misconfiguration.
    Delegates to market_calendars.is_exchange_open() which handles:
      - weekend detection
      - public holiday exclusion (algorithmic for US/EU/GB/CA/AU; approximate for others)
      - correct local open/close times per exchange
      - midday lunch breaks (JP, HK, CN, SG)
    """
    try:
        return mc.is_exchange_open(exchange_code, calibrated_utcnow())
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  SQLite cache
#
#  One file `_outputs/cache/prices.db` stores three tables:
#    meta(ticker, …, fetched_at)         – company info, weekly TTL
#    bars(ticker, interval, t, o…v)      – OHLCV rows, deduped via PK
#    period_fetch(ticker, period, …)     – per-period TTL bookkeeping
#
#  Bars are shared across periods that use the same interval, so fetching the
#  longer period (1y @ 1d) automatically warms the shorter ones (3mo, 6mo).
# ═══════════════════════════════════════════════════════════════════════════════

DB_PATH = CACHE_DIR / "prices.db"

# Approximate window per period — used as a lower bound when serving bars.
# Generous buffers absorb yfinance's slightly inclusive date semantics.
PERIOD_WINDOW_DAYS: dict[str, float | None] = {
    "1d":  5,    # ≥ a 3-day holiday weekend, so the last session is always in
                 # range when launching during a market-closed stretch. The
                 # frontend anchors on the last bar's session, so the extra
                 # trailing days never change what's drawn — they only keep the
                 # previous close reachable.
    "5d":  10,
    "1mo": 40,
    "3mo": 100,
    "6mo": 200,
    "1y":  400,
    "2y":  750,
    "5y":  1900,
    "max": None,
}

# Period order (shortest → longest). Used to mark equal-or-shorter periods at
# the same interval as fresh after a longer fetch covers them.
PERIOD_ORDER = ["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "max"]


def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as cx:
        cx.executescript("""
            CREATE TABLE IF NOT EXISTS meta (
                ticker     TEXT PRIMARY KEY,
                short_name TEXT, long_name TEXT,
                sector     TEXT, industry  TEXT,
                currency   TEXT, exchange  TEXT, website TEXT,
                cik        TEXT,
                fetched_at TEXT
            );
            CREATE TABLE IF NOT EXISTS bars (
                ticker   TEXT,
                interval TEXT,
                t        INTEGER,
                o REAL, h REAL, l REAL, c REAL, v INTEGER,
                PRIMARY KEY(ticker, interval, t)
            );
            CREATE INDEX IF NOT EXISTS idx_bars_lookup
                ON bars(ticker, interval, t);
            CREATE TABLE IF NOT EXISTS period_fetch (
                ticker     TEXT,
                period     TEXT,
                fetched_at TEXT,
                PRIMARY KEY(ticker, period)
            );
            PRAGMA journal_mode = WAL;
        """)


@contextmanager
def _db():
    cx = sqlite3.connect(DB_PATH, timeout=10)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


def db_get_meta(ticker: str) -> dict:
    with _db() as cx:
        row = cx.execute("SELECT * FROM meta WHERE ticker = ?", (ticker,)).fetchone()
    return dict(row) if row else {}


def db_set_meta(meta: dict) -> None:
    with _db() as cx:
        cx.execute("""
            INSERT INTO meta(ticker, short_name, long_name, sector, industry,
                             currency, exchange, website, cik, fetched_at)
            VALUES(:ticker, :short_name, :long_name, :sector, :industry,
                   :currency, :exchange, :website, :cik, :fetched_at)
            ON CONFLICT(ticker) DO UPDATE SET
                short_name=excluded.short_name, long_name=excluded.long_name,
                sector=excluded.sector,         industry=excluded.industry,
                currency=excluded.currency,     exchange=excluded.exchange,
                website=excluded.website,       cik=excluded.cik,
                fetched_at=excluded.fetched_at
        """, {
            "ticker":     meta.get("ticker", "").upper(),
            "short_name": meta.get("short_name", ""),
            "long_name":  meta.get("long_name", ""),
            "sector":     meta.get("sector", ""),
            "industry":   meta.get("industry", ""),
            "currency":   meta.get("currency", "USD"),
            "exchange":   meta.get("exchange", ""),
            "website":    meta.get("website", ""),
            "cik":        meta.get("cik", ""),
            "fetched_at": meta.get("fetched_at", ""),
        })


def db_get_bars(ticker: str, interval: str, since_unix: int | None) -> list[dict]:
    # `c IS NOT NULL` skips any NaN/NULL bars that an older build may have
    # written before _df_to_records dropped them — those would crash the
    # chart's last.c.toFixed() on the client. (See D18.)
    sql = "SELECT t, o, h, l, c, v FROM bars WHERE ticker=? AND interval=? AND c IS NOT NULL"
    args: list = [ticker, interval]
    if since_unix is not None:
        sql += " AND t >= ?"
        args.append(since_unix)
    sql += " ORDER BY t"
    with _db() as cx:
        rows = cx.execute(sql, args).fetchall()
    return [{"t": r["t"], "o": r["o"], "h": r["h"], "l": r["l"],
             "c": r["c"], "v": r["v"]} for r in rows]


def db_get_last_close(ticker: str, interval: str) -> dict | None:
    """Most recent non-null bar for (ticker, interval) as {t, c}, or None."""
    with _db() as cx:
        row = cx.execute(
            "SELECT t, c FROM bars WHERE ticker=? AND interval=? AND c IS NOT NULL "
            "ORDER BY t DESC LIMIT 1", (ticker, interval)
        ).fetchone()
    return {"t": row["t"], "c": row["c"]} if row else None


def db_upsert_bars(ticker: str, interval: str, records: list[dict]) -> None:
    if not records:
        return
    with _db() as cx:
        cx.executemany("""
            INSERT INTO bars(ticker, interval, t, o, h, l, c, v)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, interval, t) DO UPDATE SET
                o=excluded.o, h=excluded.h, l=excluded.l,
                c=excluded.c, v=excluded.v
        """, [(ticker, interval, r["t"], r["o"], r["h"], r["l"], r["c"], r["v"])
              for r in records])


def db_get_period_fetched_at(ticker: str, period: str) -> str:
    with _db() as cx:
        row = cx.execute(
            "SELECT fetched_at FROM period_fetch WHERE ticker=? AND period=?",
            (ticker, period)
        ).fetchone()
    return row["fetched_at"] if row else ""


def db_mark_period_fresh(ticker: str, period: str, when_iso: str) -> None:
    """
    Mark `period` as fetched at `when_iso`, AND mark every shorter-or-equal
    period that shares the same interval — fetching 1y @ 1d covers 6mo and 3mo
    too, so they don't need their own roundtrips.
    """
    interval = PERIOD_INTERVAL.get(period, "1d")
    covered = [p for p in PERIOD_ORDER
               if PERIOD_INTERVAL.get(p) == interval
               and PERIOD_ORDER.index(p) <= PERIOD_ORDER.index(period)]
    with _db() as cx:
        cx.executemany("""
            INSERT INTO period_fetch(ticker, period, fetched_at)
            VALUES(?, ?, ?)
            ON CONFLICT(ticker, period) DO UPDATE SET fetched_at=excluded.fetched_at
        """, [(ticker, p, when_iso) for p in covered])


def _age_seconds(iso_str: str) -> float:
    """Return elapsed seconds since an ISO-8601 UTC datetime string."""
    if not iso_str:
        return float("inf")
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", ""))
        return (datetime.datetime.utcnow() - dt).total_seconds()
    except Exception:
        return float("inf")


def _period_since_unix(period: str) -> int | None:
    """Lower bound for bar timestamps when serving a period, or None for max."""
    days = PERIOD_WINDOW_DAYS.get(period)
    if days is None:
        return None
    return int(time.time() - days * 86_400)


# ═══════════════════════════════════════════════════════════════════════════════
#  Data fetching
# ═══════════════════════════════════════════════════════════════════════════════

def _to_unix(ts) -> int:
    """Convert a pandas Timestamp (tz-aware or naive) to a Unix epoch integer."""
    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
        # Convert to UTC then strip tz
        ts = ts.tz_convert("UTC").replace(tzinfo=None)
    return int(pd.Timestamp(ts).timestamp())


def fetch_meta(ticker: str) -> dict:
    """Fetch company metadata from Yahoo Finance (yfinance .info dict)."""
    try:
        info = yf.Ticker(ticker).info
        raw_exchange = info.get("exchange", "") or ""
        # Normalise to the code used in EXCHANGE_SCHEDULES; keep raw if unknown
        exchange_code = raw_exchange if raw_exchange in mc.EXCHANGE_SCHEDULES else raw_exchange
        return {
            "ticker":     ticker.upper(),
            "short_name": info.get("shortName", ticker),
            "long_name":  info.get("longName",  ticker),
            "sector":     info.get("sector",    ""),
            "industry":   info.get("industry",  ""),
            "currency":   info.get("currency",  "USD"),
            "exchange":   exchange_code,
            "website":    info.get("website",   ""),
            # CIK is not exposed by yfinance; would need SEC EDGAR /company-search
            "cik":        "",
            "fetched_at": datetime.datetime.utcnow().isoformat(),
        }
    except Exception:
        return {
            "ticker": ticker.upper(), "short_name": ticker,
            "long_name": ticker, "fetched_at": "",
        }


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert a single-ticker yfinance DataFrame to OHLCV record dicts."""
    df = df.reset_index()
    dt_col = "Datetime" if "Datetime" in df.columns else "Date"
    records: list[dict] = []
    for _, row in df.iterrows():
        raw_ts = row[dt_col]
        try:
            unix_ts = _to_unix(raw_ts)
        except Exception:
            continue
        o = row.get("Open"); h = row.get("High")
        l = row.get("Low");  c = row.get("Close")
        # Drop rows with no real trade. yfinance emits a trailing row for the
        # current (incomplete) daily/weekly/monthly period with NaN OHLC when
        # the market is closed. NaN closes used to slip through (NaN != 0), get
        # stored as SQLite NULL, and crash the chart's last.c.toFixed(). Skip
        # any row whose OHLC isn't fully finite.
        if any(pd.isna(x) for x in (o, h, l, c)):
            continue
        o = float(o); h = float(h); l = float(l); c = float(c)
        if c == 0:
            continue
        raw_v = row.get("Volume", 0)
        v = 0 if pd.isna(raw_v) else int(raw_v)
        records.append({"t": unix_ts,
                        "o": round(o, 4), "h": round(h, 4),
                        "l": round(l, 4), "c": round(c, 4),
                        "v": v})
    return records


def fetch_prices(ticker: str, period: str, interval: str) -> list[dict]:
    """Download OHLCV bars for one ticker. Returns record dicts.

    If an intraday request comes back empty (market closed / pre-open), retry
    once with a wider window (INTRADAY_FALLBACK_PERIOD) so the last session's
    bars are still returned — otherwise the chart has nothing to show and the
    price reads 0.00 until the user manually switches periods.
    """
    recs = _df_to_records(yf.Ticker(ticker).history(
        period=period, interval=interval, auto_adjust=True
    ))
    if not recs:
        fb = INTRADAY_FALLBACK_PERIOD.get(interval)
        if fb and fb != period:
            recs = _df_to_records(yf.Ticker(ticker).history(
                period=fb, interval=interval, auto_adjust=True
            ))
    return recs


def fetch_prices_batch(tickers: list[str], period: str, interval: str) -> dict[str, list[dict]]:
    """
    Download OHLCV for multiple tickers in a SINGLE yfinance call. yfinance
    issues one HTTP request per batch (with internal threading), which is the
    main reason a multi-ticker period change drops from ~2 s → ~300 ms.
    """
    if not tickers:
        return {}
    if len(tickers) == 1:
        return {tickers[0]: fetch_prices(tickers[0], period, interval)}
    df = yf.download(
        tickers=" ".join(tickers),
        period=period, interval=interval,
        auto_adjust=True, group_by="ticker",
        progress=False, threads=True,
    )
    out: dict[str, list[dict]] = {}
    for t in tickers:
        try:
            out[t] = _df_to_records(df[t].dropna(how="all"))
        except Exception:
            out[t] = []
    # Market-closed fallback (mirrors fetch_prices): any ticker that returned
    # no intraday bars is re-fetched over a wider window so the last session is
    # still captured. Recurses with the wider period, which terminates because
    # the fallback period maps to itself (fb == period → no further retry).
    fb = INTRADAY_FALLBACK_PERIOD.get(interval)
    if fb and fb != period:
        empties = [t for t in tickers if not out.get(t)]
        if empties:
            out.update(fetch_prices_batch(empties, fb, interval))
    return out


def _ensure_meta_fresh(ticker: str) -> dict:
    """Return current meta row, refreshing from yfinance if older than META_TTL."""
    meta = db_get_meta(ticker)
    if _age_seconds(meta.get("fetched_at", "")) > META_TTL:
        print(f"  [cache] fetching metadata for {ticker} …")
        meta = fetch_meta(ticker)
        db_set_meta(meta)
    return meta


def _build_payload(ticker: str, period: str, interval: str, meta: dict) -> dict:
    """Assemble the JSON payload returned by /api/data and /api/data_batch."""
    exchange_code = meta.get("exchange") or mc.DEFAULT_EXCHANGE
    # Canonical "current price" (D19): sourced from ONE interval per ticker so it
    # is identical no matter which period the user views — the selected period's
    # records only drive the chart shape and the per-window gain/loss baseline.
    last    = db_get_last_close(ticker, _price_interval(exchange_code))
    records = db_get_bars(ticker, interval, _period_since_unix(period))

    # Pin the trailing bar's close to the canonical price (D20) so the chart's
    # right edge equals the headline. Only when the canonical price is NEWER than
    # the last bar — i.e. a coarse 1wk/1mo bar (or a still-forming daily bar) that
    # hasn't caught up, or a live tick during market hours. The timestamp guard
    # means an intraday series, whose last bar is already the freshest point, is
    # never rewritten. High/low are widened so the candle stays valid.
    if last and records and last["t"] > records[-1]["t"]:
        px   = last["c"]
        tail = records[-1]
        tail["c"] = px
        if px > tail["h"]:
            tail["h"] = px
        if px < tail["l"]:
            tail["l"] = px

    return {
        "meta":              meta,
        "interval":          interval,
        "period":            period,
        "fetched_at":        db_get_period_fetched_at(ticker, period),
        "records":           records,
        "market_open":       is_market_open(exchange_code),
        "exchange_schedule": mc.schedule_for_json(exchange_code),
        "last_price":        last["c"] if last else None,
        "last_price_at":     last["t"] if last else None,
    }


def _effective_ttl(interval: str, exchange_code: str) -> int:
    base = CACHE_TTL.get(interval, 86_400)
    if interval in ("1m", "5m", "1h") and is_market_open(exchange_code):
        return 6
    return base


# Canonical "current price" source (D19). The headline price must be ONE value
# per ticker, identical regardless of the selected period — only the per-window
# gain/loss should differ. So it is read from a single interval rather than from
# the selected period's last bar (whose coarse-interval trailing bar can be a
# stale month/week-to-date snapshot). During market hours that source is the 1m
# feed (live); when closed it is the daily close (the official last-session
# price). PRICE_INTERVAL_ANCHOR maps that interval → the period used to fetch it.
PRICE_INTERVAL_ANCHOR: dict[str, str] = {"1m": "1d", "1d": "6mo"}


def _price_interval(exchange_code: str) -> str:
    return "1m" if is_market_open(exchange_code) else "1d"


def _ensure_price_interval_fresh(ticker: str, exchange_code: str) -> None:
    """Refresh the canonical price interval if its cache is older than its TTL.
    No-op when the data is already warm (the common case) or when the caller's
    own period fetch already covers that interval."""
    iv = _price_interval(exchange_code)
    anchor = PRICE_INTERVAL_ANCHOR[iv]
    if _age_seconds(db_get_period_fetched_at(ticker, anchor)) <= _effective_ttl(iv, exchange_code):
        return
    print(f"  [cache] refreshing canonical {iv} price for {ticker} …")
    recs = fetch_prices(ticker, anchor, iv)
    db_upsert_bars(ticker, iv, recs)
    db_mark_period_fresh(ticker, anchor, datetime.datetime.utcnow().isoformat())


def get_chart_data(ticker: str, period: str, mode: str = "fresh") -> dict | None:
    """
    Return chart data for ticker+period.

    mode="fresh" (default): refresh from yfinance if cache row is older than TTL.
    mode="swr":             ZERO network calls. Returns whatever's in the cache,
                            with `stale: true` if it's older than the TTL.
                            Returns None if nothing is cached for this row —
                            caller is expected to retry with mode="fresh".
    """
    ticker   = ticker.upper()
    interval = PERIOD_INTERVAL.get(period, "1d")

    if mode == "swr":
        meta       = db_get_meta(ticker)
        fetched_at = db_get_period_fetched_at(ticker, period)
        if not meta or not fetched_at:
            return None
        payload          = _build_payload(ticker, period, interval, meta)
        ttl              = _effective_ttl(interval, meta.get("exchange") or mc.DEFAULT_EXCHANGE)
        payload["stale"] = _age_seconds(fetched_at) > ttl
        return payload

    meta     = _ensure_meta_fresh(ticker)
    exchange = meta.get("exchange") or mc.DEFAULT_EXCHANGE
    ttl      = _effective_ttl(interval, exchange)
    age      = _age_seconds(db_get_period_fetched_at(ticker, period))

    if age > ttl:
        print(f"  [cache] fetching {interval} prices for {ticker} ({period}) …")
        records = fetch_prices(ticker, period, interval)
        db_upsert_bars(ticker, interval, records)
        db_mark_period_fresh(ticker, period, datetime.datetime.utcnow().isoformat())

    # Keep the canonical current-price source fresh so the headline price is the
    # same on every period (D19). Skipped when the requested interval already IS
    # the canonical one (the fetch above covered it).
    if _price_interval(exchange) != interval:
        _ensure_price_interval_fresh(ticker, exchange)

    payload          = _build_payload(ticker, period, interval, meta)
    payload["stale"] = False
    return payload


def get_chart_data_batch(tickers: list[str], period: str, mode: str = "fresh") -> dict[str, dict]:
    """
    Batched equivalent of get_chart_data().

    mode="fresh" (default): refresh stale tickers in a SINGLE yf.download() call.
    mode="swr":             ZERO network calls. Tickers with no cache are simply
                            omitted from the response — the frontend will request
                            them in a follow-up fresh call.
    """
    tickers = [t.upper() for t in tickers if t]
    if not tickers:
        return {}
    interval = PERIOD_INTERVAL.get(period, "1d")

    if mode == "swr":
        out: dict[str, dict] = {}
        for t in tickers:
            meta       = db_get_meta(t)
            fetched_at = db_get_period_fetched_at(t, period)
            if not meta or not fetched_at:
                continue
            payload          = _build_payload(t, period, interval, meta)
            ttl              = _effective_ttl(interval, meta.get("exchange") or mc.DEFAULT_EXCHANGE)
            payload["stale"] = _age_seconds(fetched_at) > ttl
            out[t] = payload
        return out

    metas = {t: _ensure_meta_fresh(t) for t in tickers}

    stale: list[str] = []
    for t in tickers:
        exchange = metas[t].get("exchange") or mc.DEFAULT_EXCHANGE
        ttl      = _effective_ttl(interval, exchange)
        if _age_seconds(db_get_period_fetched_at(t, period)) > ttl:
            stale.append(t)

    if stale:
        print(f"  [cache] batch fetching {interval} prices for {stale} ({period}) …")
        now_iso = datetime.datetime.utcnow().isoformat()
        try:
            batch = fetch_prices_batch(stale, period, interval)
        except Exception as exc:
            print(f"  [cache] batch fetch failed ({exc}) — falling back to per-ticker")
            batch = {t: fetch_prices(t, period, interval) for t in stale}
        for t, recs in batch.items():
            db_upsert_bars(t, interval, recs)
            db_mark_period_fresh(t, period, now_iso)

    # Keep each ticker's canonical current-price source fresh (D19), grouped by
    # (interval, anchor-period) so same-state tickers refresh in one batched call.
    # Tickers whose requested interval already IS the canonical one are skipped.
    price_targets: dict[tuple[str, str], list[str]] = {}
    for t in tickers:
        exch = metas[t].get("exchange") or mc.DEFAULT_EXCHANGE
        piv  = _price_interval(exch)
        if piv == interval:
            continue
        anchor = PRICE_INTERVAL_ANCHOR[piv]
        if _age_seconds(db_get_period_fetched_at(t, anchor)) > _effective_ttl(piv, exch):
            price_targets.setdefault((piv, anchor), []).append(t)
    for (piv, anchor), ts in price_targets.items():
        now_iso2 = datetime.datetime.utcnow().isoformat()
        print(f"  [cache] refreshing canonical {piv} price for {ts} …")
        try:
            pbatch = fetch_prices_batch(ts, anchor, piv)
        except Exception:
            pbatch = {t: fetch_prices(t, anchor, piv) for t in ts}
        for t, recs in pbatch.items():
            db_upsert_bars(t, piv, recs)
            db_mark_period_fresh(t, anchor, now_iso2)

    out = {}
    for t in tickers:
        p          = _build_payload(t, period, interval, metas[t])
        p["stale"] = False
        out[t] = p
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  Ticker / company name resolution
# ═══════════════════════════════════════════════════════════════════════════════

_YF_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; StockVisualizer/1.0)"}


def search_tickers(query: str, n: int = 8) -> list[dict]:
    """Return up to n {symbol, name, type} dicts from Yahoo Finance search."""
    try:
        r = requests.get(
            _YF_SEARCH_URL,
            params={"q": query, "quotesCount": n, "newsCount": 0},
            headers=_HEADERS,
            timeout=6,
            stream=True,
        )
        r.raise_for_status()
        # Bound the response so a hostile/oversized upstream body can't be
        # buffered unboundedly into memory: read at most ~5 MB, then parse.
        _MAX_BYTES = 5 * 1024 * 1024
        body = r.raw.read(_MAX_BYTES + 1, decode_content=True)
        if len(body) > _MAX_BYTES:
            return []
        quotes = json.loads(body).get("quotes", [])
        return [
            {
                "symbol": q["symbol"],
                "name":   q.get("longname") or q.get("shortname", ""),
                "type":   q.get("quoteType", ""),
            }
            for q in quotes
            if q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND", "INDEX")
        ]
    except Exception:
        return []


def resolve_ticker(query: str) -> str:
    """
    Best-effort: return a ticker symbol for a query string.
    If query already looks like a ticker (≤5 alpha chars), return it directly.
    Otherwise call Yahoo Finance search API.
    """
    q = query.strip()
    if len(q) <= 5 and q.replace(".", "").replace("-", "").isalpha():
        return q.upper()
    results = search_tickers(q, n=5)
    if results:
        return results[0]["symbol"]
    return q.upper()


# ═══════════════════════════════════════════════════════════════════════════════
#  Flask routes
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    # Root page is now the Three.js 3D scene (index.html at project root).
    # The scene embeds multiple <iframe>s, each of which loads the chart
    # template via the /_outputs/templates/... route defined below.
    return send_from_directory(str(ROOT), "index.html")


@app.route("/utilities/renderer/<path:filename>")
def utilities_renderer(filename):
    # Serves static assets (e.g. the background PNG) from Utilities/Renderer/.
    # Used by index.html to load the scene background image.
    return send_from_directory(str(ROOT / "Utilities" / "Renderer"), filename)


@app.route("/_outputs/templates/1_chart_template.html")
def chart_template():
    # Served under its on-disk path so the iframe src in index.html
    # (CHART_URL = "_outputs/templates/1_chart_template.html") resolves
    # against http://localhost:PORT/ without any URL rewriting.
    return render_template("1_chart_template.html")


@app.route("/api/data")
def api_data():
    ticker = (request.args.get("ticker") or "").upper().strip()
    period = (request.args.get("period") or "1d").strip()
    mode   = (request.args.get("mode")   or "fresh").strip()
    if not ticker:
        return jsonify({"error": "ticker parameter required"}), 400
    if period not in VALID_PERIODS:
        period = "1d"
    if mode not in ("fresh", "swr"):
        mode = "fresh"
    try:
        data = get_chart_data(ticker, period, mode)
        if data is None:
            # SWR cache miss — return 204 so the frontend knows to retry fresh.
            return ("", 204)
        return jsonify(data)
    except Exception as exc:
        app.logger.exception("api_data failed")
        return jsonify({"error": "internal error"}), 500


@app.route("/api/data_batch")
def api_data_batch():
    """
    Batched chart data for multiple tickers at one period.

    Query params:
      tickers — comma-separated symbols, e.g. "AAPL,MSFT,NVDA"
      period  — one of VALID_PERIODS
      mode    — "fresh" (default) refreshes stale tickers from yfinance;
                "swr" returns only what's already cached, with no network calls

    Response: { "period": "...", "tickers": { "AAPL": {…same shape as /api/data…}, … } }
              Each per-ticker payload includes `stale: true|false`. In swr mode,
              tickers without any cache row are omitted entirely.
    """
    raw    = (request.args.get("tickers") or "").strip()
    period = (request.args.get("period")  or "1d").strip()
    mode   = (request.args.get("mode")    or "fresh").strip()
    if not raw:
        return jsonify({"error": "tickers parameter required"}), 400
    if period not in VALID_PERIODS:
        period = "1d"
    if mode not in ("fresh", "swr"):
        mode = "fresh"
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    # Reject implausibly long lists outright, then cap to bound the outbound
    # yfinance fan-out (one round-trip per uncached symbol) and DB writes.
    if len(tickers) > 200:
        return jsonify({"error": "too many tickers"}), 400
    tickers = tickers[:25]
    try:
        return jsonify({"period": period, "tickers": get_chart_data_batch(tickers, period, mode)})
    except Exception as exc:
        app.logger.exception("api_data_batch failed")
        return jsonify({"error": "internal error"}), 500


@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify([])
    results = search_tickers(q)
    return jsonify(results)


@app.route("/api/market_status")
def api_market_status():
    return jsonify({"open": is_market_open()})


@app.route("/api/time_info")
def api_time_info():
    """
    Return clock calibration data + exchange schedule for the browser.

    Query params:
      ticker  (optional) — used to look up the exchange from the local cache.
                           Falls back to NYSE if not provided or not cached.

    Fields returned:
      internet_utc_ms    – calibrated UTC epoch ms (JS Date-compatible)
      local_utc_ms       – raw local clock UTC epoch ms
      offset_ms          – (internet − local) ms; add to Date.now() in JS
      market_open        – server-computed open/closed for the exchange
      sync_source        – description of the sync host used
      exchange_schedule  – {code, name, tz, open, close, break_start, break_end, approximate}
    """
    with _clock_sync_lock:
        offset_s = _clock_offset_s
        source   = _clock_sync_source

    ticker        = (request.args.get("ticker") or "").upper().strip()
    exchange_code = mc.DEFAULT_EXCHANGE
    if ticker:
        exchange_code = (db_get_meta(ticker).get("exchange") or mc.DEFAULT_EXCHANGE)

    local_utc_ms    = int(time.time() * 1000)
    internet_utc_ms = int((time.time() + offset_s) * 1000)
    return jsonify({
        "internet_utc_ms":   internet_utc_ms,
        "local_utc_ms":      local_utc_ms,
        "offset_ms":         int(offset_s * 1000),
        "market_open":       is_market_open(exchange_code),
        "sync_source":       source,
        "exchange_schedule": mc.schedule_for_json(exchange_code),
    })


@app.route("/api/resolve")
def api_resolve():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "q parameter required"}), 400
    return jsonify({"ticker": resolve_ticker(q)})


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════

PORT = 5000


def _open_browser(url: str) -> None:
    time.sleep(1.4)
    webbrowser.open(url)


if __name__ == "__main__":
    _init_db()

    # Start internet clock calibration immediately (blocking first sync so
    # is_market_open() is accurate before serving any data).
    print("  [clock] calibrating against internet time …", end="", flush=True)
    _sync_clock_once()
    threading.Thread(target=_clock_sync_loop, daemon=True).start()

    # Non-interactive launch. The browser opens with no URL query params, and
    # index.html restores tickers / selected period / marker texts / ring order
    # from its own localStorage cache. On the very first launch the cache is
    # empty and the HTML falls back to 6 blank billboards @ 1d. Financial data
    # is refreshed automatically by /api/data_batch when the page loads.
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║      Stock Visualizer — 3D Scene  v1.3               ║")
    print("╚══════════════════════════════════════════════════════╝")
    print("  Launching browser — session will be restored from cache.")
    url = f"http://localhost:{PORT}/"

    print()
    print(f"  Starting server on http://localhost:{PORT}")
    print("  Press Ctrl+C to stop.\n")

    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    # threaded=True lets Flask serve the per-iframe live-poll requests
    # concurrently instead of serialising them on a single worker.
    # Loopback-only: the UI opens http://localhost:PORT/, so binding to
    # 127.0.0.1 fully serves the single-user desktop use while keeping the
    # no-auth API off the LAN.
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False, threaded=True)
