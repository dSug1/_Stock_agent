#!/usr/bin/env python3
"""
2_stock_visualizer.py
Interactive US stock price chart served via a local Flask web server.

Features
--------
- Accepts ticker symbol OR company legal name (resolved via Yahoo Finance search API)
- Granularity auto-selected based on requested time period
- Per-ticker JSON cache in _outputs/cache/{TICKER}.json
  · metadata refreshed weekly (company name, sector, exchange …)
  · price data refreshed per-interval TTL; every 6 s during market hours
- One static HTML template in _outputs/templates/1_chart_template.html
- Browser chart (TradingView Lightweight Charts) auto-scales axes
- Live poll every 6 s only when US market is open

Usage
-----
    python 2_stock_visualizer.py          # prompts in terminal, opens browser
"""

import os, sys, json, time, datetime, threading, webbrowser
import email.utils
from pathlib import Path

import requests
import yfinance as yf
import pandas as pd
import pytz
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

# Cache TTL per interval (seconds).
# During market hours the 1m/5m/1h intervals use 6 s TTL to drive live updates.
CACHE_TTL: dict[str, int] = {
    "1m":  6,
    "5m":  30,
    "1h":  300,
    "1d":  86_400,
    "1wk": 604_800,
    "1mo": 2_592_000,
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
    """Background thread: sync at startup then every CLOCK_SYNC_INTERVAL seconds."""
    _sync_clock_once()
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
#  Cache helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _cache_file(ticker: str) -> Path:
    return CACHE_DIR / f"{ticker.upper()}.json"


def _load_cache(ticker: str) -> dict:
    p = _cache_file(ticker)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_cache(ticker: str, data: dict) -> None:
    _cache_file(ticker).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _age_seconds(iso_str: str) -> float:
    """Return elapsed seconds since an ISO-8601 UTC datetime string."""
    if not iso_str:
        return float("inf")
    try:
        dt = datetime.datetime.fromisoformat(iso_str.replace("Z", ""))
        return (datetime.datetime.utcnow() - dt).total_seconds()
    except Exception:
        return float("inf")


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


def fetch_prices(ticker: str, period: str, interval: str) -> list[dict]:
    """
    Download OHLCV bars from Yahoo Finance.
    Returns a list of dicts with keys: t (unix epoch), o, h, l, c, v.
    """
    df = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
    df = df.reset_index()

    records: list[dict] = []
    dt_col = "Datetime" if "Datetime" in df.columns else "Date"

    for _, row in df.iterrows():
        raw_ts = row[dt_col]
        try:
            unix_ts = _to_unix(raw_ts)
        except Exception:
            continue
        o = float(row.get("Open",  0) or 0)
        h = float(row.get("High",  0) or 0)
        l = float(row.get("Low",   0) or 0)
        c = float(row.get("Close", 0) or 0)
        v = int(row.get("Volume",  0) or 0)
        if c == 0:
            continue
        records.append({
            "t": unix_ts,
            "o": round(o, 4),
            "h": round(h, 4),
            "l": round(l, 4),
            "c": round(c, 4),
            "v": v,
        })
    return records


def get_chart_data(ticker: str, period: str) -> dict:
    """
    Return chart data for ticker+period, using cache where fresh.

    Cache strategy (trade-off):
      - One JSON file per ticker → fast reuse, O(tickers) files on disk.
      - Multiple period sections per file → single fetch covers many requests.
      - Intraday sections refreshed every 6 s during market hours (live feel).
      - Daily/weekly/monthly sections refreshed by their natural TTL.
    """
    ticker        = ticker.upper()
    interval      = PERIOD_INTERVAL.get(period, "1d")
    base_ttl      = CACHE_TTL.get(interval, 86_400)
    exchange_code = _load_cache(ticker).get("meta", {}).get("exchange", mc.DEFAULT_EXCHANGE) or mc.DEFAULT_EXCHANGE

    # During open market, intraday intervals use 6-second TTL
    effective_ttl = (
        6 if is_market_open(exchange_code) and interval in ("1m", "5m", "1h")
        else base_ttl
    )

    cache    = _load_cache(ticker)
    now_iso  = datetime.datetime.utcnow().isoformat()

    # ── Refresh metadata if stale ──────────────────────────────────────────────
    if _age_seconds(cache.get("meta", {}).get("fetched_at", "")) > META_TTL:
        print(f"  [cache] fetching metadata for {ticker} …")
        cache["meta"] = fetch_meta(ticker)
        _save_cache(ticker, cache)

    # ── Refresh price data if stale ───────────────────────────────────────────
    key    = f"{period}_{interval}"
    prices = cache.get("prices", {})
    sec    = prices.get(key, {})

    if not sec or _age_seconds(sec.get("fetched_at", "")) > effective_ttl:
        print(f"  [cache] fetching {interval} prices for {ticker} ({period}) …")
        records = fetch_prices(ticker, period, interval)
        cache.setdefault("prices", {})[key] = {
            "interval":   interval,
            "period":     period,
            "fetched_at": now_iso,
            "records":    records,
        }
        _save_cache(ticker, cache)

    # Re-read exchange_code in case meta was just refreshed
    exchange_code = cache.get("meta", {}).get("exchange", mc.DEFAULT_EXCHANGE) or mc.DEFAULT_EXCHANGE
    sec = cache["prices"][key]
    return {
        "meta":              cache["meta"],
        "interval":          interval,
        "period":            period,
        "fetched_at":        sec["fetched_at"],
        "records":           sec["records"],
        "market_open":       is_market_open(exchange_code),
        "exchange_schedule": mc.schedule_for_json(exchange_code),
    }


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
        )
        r.raise_for_status()
        quotes = r.json().get("quotes", [])
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
    if not ticker:
        return jsonify({"error": "ticker parameter required"}), 400
    if period not in VALID_PERIODS:
        period = "1d"
    try:
        data = get_chart_data(ticker, period)
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


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
        exchange_code = (_load_cache(ticker).get("meta", {}).get("exchange") or mc.DEFAULT_EXCHANGE)

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


# Hardcoded tickers and period used when the user picks Debug mode at startup.
DEBUG_TICKERS = ["TCRX", "BCYC", "TTE", "AAPL", "GOOGL", "MCD"]
DEBUG_PERIOD  = "3mo"


def _prefetch(tickers: list[str], period: str) -> None:
    """Resolve + pre-fetch data for each ticker, printing progress."""
    for t in tickers:
        print(f"  Pre-fetching {period} data for {t} …", end="", flush=True)
        try:
            get_chart_data(t, period)
            print(" done (cached).")
        except Exception as exc:
            print(f" WARNING: {exc}")


def _prompt_mode() -> str:
    """
    Ask the user whether to run in Debug or Production mode.
    Returns 'debug' or 'production'.  Defaults to 'production' on blank input.
    """
    print()
    print("╔══════════════════════════════════════════════════════╗")
    print("║      Stock Visualizer — 3D Scene  v1.3               ║")
    print("╠══════════════════════════════════════════════════════╣")
    print("║  Run mode:                                           ║")
    print("║    [1] Production  (interactive — prompt for input)  ║")
    print("║    [2] Debug       (hardcoded TCRX,BCYC,TTE,AAPL,    ║")
    print("║                     GOOGL,MCD  @ 3mo)                ║")
    print("╚══════════════════════════════════════════════════════╝")
    raw = input("  Mode [1/2] (↵=1): ").strip()
    return "debug" if raw == "2" else "production"


def _prompt_and_prefetch() -> tuple[list[str], str]:
    """
    Production-mode interactive prompt.

    Returns (tickers, period):
      tickers — up to 6 resolved symbols that fill the ring billboards
                in index.html (the central object is a placeholder,
                not a ticker chart).
      period  — one time window applied to every chart

    Both values are passed to the browser as URL query params so the
    3D scene (index.html) and each embedded chart iframe start with
    the correct data.
    """
    print()
    print("  Enter up to 6 tickers / company names, comma-separated.")
    print("  Leave blank for an all-empty scene.")
    print("  e.g.  AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA")
    print()

    raw_tickers = input("  Tickers (comma-separated): ").strip()
    raw_period  = input("  Period [1d 5d 1mo 3mo 6mo 1y 2y 5y max] (↵=1mo): ").strip()

    period = raw_period if raw_period in VALID_PERIODS else "1mo"

    tickers: list[str] = []
    if raw_tickers:
        print()
        for token in raw_tickers.split(","):
            token = token.strip()
            if not token:
                continue
            print(f"  Resolving '{token}' …", end="", flush=True)
            resolved = resolve_ticker(token)
            print(f" → {resolved}")
            if resolved and resolved not in tickers:
                tickers.append(resolved)
            if len(tickers) >= 6:
                break

        print()
        _prefetch(tickers, period)

    return tickers, period


def _debug_prefetch() -> tuple[list[str], str]:
    """Debug mode: skip prompts, use hardcoded tickers + period."""
    print()
    print(f"  [debug] tickers={DEBUG_TICKERS}  period={DEBUG_PERIOD}")
    print()
    _prefetch(DEBUG_TICKERS, DEBUG_PERIOD)
    return list(DEBUG_TICKERS), DEBUG_PERIOD


if __name__ == "__main__":
    # Start internet clock calibration immediately (blocking first sync so
    # is_market_open() is accurate before we pre-fetch any data).
    print("  [clock] calibrating against internet time …", end="", flush=True)
    _sync_clock_once()
    # Then keep re-syncing every 30 min in the background
    threading.Thread(target=lambda: (time.sleep(CLOCK_SYNC_INTERVAL), _clock_sync_loop()),
                     daemon=True).start()

    mode = _prompt_mode()
    if mode == "debug":
        tickers, period = _debug_prefetch()
    else:
        tickers, period = _prompt_and_prefetch()

    url = f"http://localhost:{PORT}/"
    qs  = [f"period={period}"]
    if tickers:
        qs.insert(0, "tickers=" + ",".join(tickers))
    url += "?" + "&".join(qs)

    print()
    print(f"  Starting server on http://localhost:{PORT}")
    print("  Press Ctrl+C to stop.\n")

    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
