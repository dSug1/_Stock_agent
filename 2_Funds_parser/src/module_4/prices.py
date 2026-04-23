"""SQLite-backed price + snapshot cache for Module 4.

Owns three tables in `data/prices.db`:

- ticker_snapshot   : per-ticker metadata + ADV (Module 4a; 7-day TTL)
- prices            : daily OHLCV with adjusted_close (Module 4b)
- price_fetch_log   : per-ticker incremental-fetch bookkeeping (Module 4b)

All Yahoo Finance traffic flows through this module. Throughput plan
(spec/module_4_spec.md § Yahoo Finance throughput):

- Single shared curl_cffi.requests.Session(impersonate="chrome") so Yahoo
  treats us like Chrome, not a default `requests` client.
- Bars batched via yf.download(tickers=" ".join(batch), threads=True).
- .info parallelised via ThreadPoolExecutor under a process-global
  token-bucket rate limiter (5 req/s default; tunable in YAML).
- Exponential-backoff retry: 1s, 2s, 4s, max 2 retries.

Code patterns (SQLite WAL, _db() context manager, batched yf.download with
per-ticker fallback, _age_seconds TTL) are adapted from
0_Renderer/2_stock_visualizer.py. The two pipelines share NO data — separate
SQLite files, separate caches.
"""
from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import yfinance as yf

try:
    from curl_cffi import requests as curl_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

log = logging.getLogger(__name__)


# ─── Shared HTTP session (curl_cffi if available; else default requests) ─────

_SESSION_LOCK = threading.Lock()
_YF_SESSION = None  # lazy-initialised


def get_yf_session():
    """Process-global Yahoo Finance HTTP session.

    curl_cffi impersonates Chrome's TLS fingerprint, dramatically reducing
    Yahoo's bot-detection throttling vs the default `requests` backend.
    """
    global _YF_SESSION
    if _YF_SESSION is not None:
        return _YF_SESSION
    with _SESSION_LOCK:
        if _YF_SESSION is None:
            if _HAS_CURL_CFFI:
                _YF_SESSION = curl_requests.Session(impersonate="chrome")
                log.info("Yahoo session: curl_cffi (impersonate=chrome)")
            else:
                import requests
                _YF_SESSION = requests.Session()
                log.warning(
                    "curl_cffi not installed; falling back to requests.Session(). "
                    "Throughput WILL be lower (Yahoo bot-detection)."
                )
    return _YF_SESSION


def reset_yf_session() -> None:
    """Dispose the current session. Next get_yf_session() builds a fresh one
    (and re-fetches Yahoo's anti-CSRF crumb)."""
    global _YF_SESSION
    with _SESSION_LOCK:
        try:
            if _YF_SESSION is not None and hasattr(_YF_SESSION, "close"):
                _YF_SESSION.close()
        except Exception:
            pass
        _YF_SESSION = None


def _yahoo_symbol(ticker: str) -> str:
    """Translate SEC-style ticker to Yahoo's format.

    SEC writes share-class separators as '/' (e.g. MOG/A, BRK/A); Yahoo
    uses '-' (MOG-A, BRK-A). Single '.' separators (BRK.A) are also
    Yahoo-friendly via '-'. Empty string passes through.
    """
    if not ticker:
        return ticker
    return ticker.replace("/", "-")


# ─── Process-global token-bucket rate limiter ────────────────────────────────

class _TokenBucket:
    """Module-scope rate limiter. Same shape as layer_1/edgar_13f._RateLimiter."""

    def __init__(self, rate_per_s: float) -> None:
        self.rate = float(rate_per_s)
        self.allowance = self.rate
        self.last = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.allowance = min(self.rate, self.allowance + (now - self.last) * self.rate)
            self.last = now
            if self.allowance < 1.0:
                wait = (1.0 - self.allowance) / self.rate
                time.sleep(wait)
                self.allowance = 0.0
            else:
                self.allowance -= 1.0


# Global limiters; tuned at first use via configure_rate_limits().
_INFO_LIMITER = _TokenBucket(5.0)
_BARS_LIMITER = _TokenBucket(5.0)


def configure_rate_limits(info_rate_per_s: float, bars_rate_per_s: float) -> None:
    """Reset the global limiters with config-driven rates. Call once per run."""
    global _INFO_LIMITER, _BARS_LIMITER
    _INFO_LIMITER = _TokenBucket(info_rate_per_s)
    _BARS_LIMITER = _TokenBucket(bars_rate_per_s)


# ─── SQLite schema + connection helpers ───────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ticker_snapshot (
    ticker        TEXT PRIMARY KEY,
    short_name    TEXT,
    long_name     TEXT,
    sector        TEXT,
    industry      TEXT,
    exchange      TEXT,
    currency      TEXT,
    market_cap    INTEGER,
    shares_out    INTEGER,
    last_close    REAL,
    adv_30d       REAL,
    fetched_at    TEXT,
    fetch_status  TEXT,
    fetch_error   TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    ticker          TEXT NOT NULL,
    date            TEXT NOT NULL,
    close           REAL NOT NULL,
    adjusted_close  REAL NOT NULL,
    volume          INTEGER,
    PRIMARY KEY (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_prices_ticker_date ON prices(ticker, date DESC);

CREATE TABLE IF NOT EXISTS price_fetch_log (
    ticker          TEXT PRIMARY KEY,
    first_date      TEXT,
    last_date       TEXT,
    last_fetched_at TEXT,
    source          TEXT DEFAULT 'yfinance',
    fetch_status    TEXT,
    fetch_error     TEXT
);
"""


def init_prices_db(db_path: Path) -> None:
    """CREATE TABLE IF NOT EXISTS for snapshot, prices, fetch log. Sets WAL."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=10) as cx:
        cx.executescript(_SCHEMA_SQL)
        cx.execute("PRAGMA journal_mode=WAL")
        cx.commit()


@contextmanager
def db_connect(db_path: Path):
    cx = sqlite3.connect(db_path, timeout=10)
    cx.row_factory = sqlite3.Row
    try:
        yield cx
        cx.commit()
    finally:
        cx.close()


# ─── Snapshot read/write ─────────────────────────────────────────────────────

def get_snapshots(db_path: Path, tickers: Iterable[str]) -> dict[str, dict]:
    """Bulk-read snapshot rows for the given tickers. Missing keys absent."""
    tickers = list({t for t in tickers if t})
    if not tickers:
        return {}
    out: dict[str, dict] = {}
    placeholders = ",".join("?" * len(tickers))
    with db_connect(db_path) as cx:
        rows = cx.execute(
            f"SELECT * FROM ticker_snapshot WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
    for r in rows:
        out[r["ticker"]] = dict(r)
    return out


def upsert_snapshot(db_path: Path, snap: dict) -> None:
    with db_connect(db_path) as cx:
        cx.execute(
            """
            INSERT INTO ticker_snapshot(
                ticker, short_name, long_name, sector, industry,
                exchange, currency, market_cap, shares_out, last_close,
                adv_30d, fetched_at, fetch_status, fetch_error
            ) VALUES(
                :ticker, :short_name, :long_name, :sector, :industry,
                :exchange, :currency, :market_cap, :shares_out, :last_close,
                :adv_30d, :fetched_at, :fetch_status, :fetch_error
            )
            ON CONFLICT(ticker) DO UPDATE SET
                short_name=excluded.short_name,
                long_name=excluded.long_name,
                sector=excluded.sector,
                industry=excluded.industry,
                exchange=excluded.exchange,
                currency=excluded.currency,
                market_cap=excluded.market_cap,
                shares_out=excluded.shares_out,
                last_close=excluded.last_close,
                adv_30d=excluded.adv_30d,
                fetched_at=excluded.fetched_at,
                fetch_status=excluded.fetch_status,
                fetch_error=excluded.fetch_error
            """,
            snap,
        )


# ─── Bars upsert + fetch log ─────────────────────────────────────────────────

def upsert_bars(db_path: Path, ticker: str, bars: list[dict]) -> None:
    if not bars:
        return
    with db_connect(db_path) as cx:
        cx.executemany(
            """
            INSERT INTO prices(ticker, date, close, adjusted_close, volume)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(ticker, date) DO UPDATE SET
                close=excluded.close,
                adjusted_close=excluded.adjusted_close,
                volume=excluded.volume
            """,
            [(ticker, b["date"], b["close"], b["adjusted_close"], b.get("volume"))
             for b in bars],
        )


def update_fetch_log(db_path: Path, ticker: str, *, status: str,
                     error: Optional[str] = None) -> None:
    """Refresh first/last/last_fetched_at for a ticker. Pulls min/max from prices."""
    now_iso = dt.datetime.utcnow().isoformat()
    with db_connect(db_path) as cx:
        row = cx.execute(
            "SELECT MIN(date), MAX(date) FROM prices WHERE ticker=?",
            (ticker,),
        ).fetchone()
        first_date, last_date = (row[0], row[1]) if row else (None, None)
        cx.execute(
            """
            INSERT INTO price_fetch_log(ticker, first_date, last_date,
                                        last_fetched_at, source, fetch_status, fetch_error)
            VALUES(?, ?, ?, ?, 'yfinance', ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                first_date=excluded.first_date,
                last_date=excluded.last_date,
                last_fetched_at=excluded.last_fetched_at,
                fetch_status=excluded.fetch_status,
                fetch_error=excluded.fetch_error
            """,
            (ticker, first_date, last_date, now_iso, status, error),
        )


def get_fetch_log(db_path: Path, ticker: str) -> Optional[dict]:
    with db_connect(db_path) as cx:
        row = cx.execute(
            "SELECT * FROM price_fetch_log WHERE ticker=?", (ticker,)
        ).fetchone()
    return dict(row) if row else None


# ─── TTL helpers ─────────────────────────────────────────────────────────────

def age_seconds(iso_str: Optional[str]) -> float:
    if not iso_str:
        return float("inf")
    try:
        d = dt.datetime.fromisoformat(iso_str.replace("Z", ""))
    except Exception:
        return float("inf")
    return (dt.datetime.utcnow() - d).total_seconds()


def is_today_utc(iso_str: Optional[str]) -> bool:
    if not iso_str:
        return False
    try:
        d = dt.datetime.fromisoformat(iso_str.replace("Z", ""))
    except Exception:
        return False
    return d.date() == dt.datetime.utcnow().date()


# ─── Yahoo Finance fetchers (info + bars) ────────────────────────────────────

_CRUMB_ERROR_TOKENS = ("invalid crumb", "unauthorized", " 401")
_RATE_LIMIT_TOKENS = ("too many requests", "rate limit", "yfratelimiterror")


class YahooThrottled(Exception):
    """Yahoo has IP-rate-limited us. Caller should stop submitting work."""


_THROTTLED_AT: Optional[float] = None
_THROTTLE_LOCK = threading.Lock()


def is_throttled() -> bool:
    """Has the process detected an IP-level Yahoo throttle this run?"""
    with _THROTTLE_LOCK:
        return _THROTTLED_AT is not None


def mark_throttled() -> None:
    """Set the process-global throttle flag. Called from _retry on
    YFRateLimitError; subsequent yfinance calls in this run should abort."""
    global _THROTTLED_AT
    with _THROTTLE_LOCK:
        if _THROTTLED_AT is None:
            _THROTTLED_AT = time.monotonic()
            log.warning(
                "Yahoo Finance IP throttle detected. Aborting further fetches "
                "this run; cached data is preserved. Wait 30-60 min before retry."
            )


def reset_throttle() -> None:
    """For tests + manual recovery — clear the throttle flag."""
    global _THROTTLED_AT
    with _THROTTLE_LOCK:
        _THROTTLED_AT = None


def _retry(func, *, retries: int, backoff_s: list[float], label: str = ""):
    """Call func() with exponential backoff. Returns func()'s result on success;
    re-raises on final failure. backoff_s is indexed by retry attempt.

    Detects two error families:
      - Crumb-invalidation (401 / "Invalid Crumb"): force session reset and retry.
      - IP rate-limit (YFRateLimitError / "Too Many Requests"): set the global
        throttle flag and raise YahooThrottled IMMEDIATELY without retrying —
        retrying within minutes only deepens the throttle.
    """
    if is_throttled():
        raise YahooThrottled(f"throttled before {label}")

    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            return func()
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()
            if any(tok in err_str for tok in _RATE_LIMIT_TOKENS):
                mark_throttled()
                raise YahooThrottled(f"{label}: {e}") from e
            if any(tok in err_str for tok in _CRUMB_ERROR_TOKENS):
                log.debug("crumb invalidation on %s; resetting session", label)
                reset_yf_session()
            if attempt >= retries:
                break
            wait = backoff_s[min(attempt, len(backoff_s) - 1)]
            log.debug("retry %s in %.1fs (attempt %d/%d): %s",
                      label, wait, attempt + 1, retries, e)
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def fetch_info(ticker: str, *, retries: int = 2,
               backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0)) -> dict:
    """yfinance .info for one ticker (descriptive fields).

    Uses the crumb-protected `quoteSummary` endpoint. May fail with
    'Invalid Crumb' under load — the retry layer triggers a session reset
    in that case.
    """
    bo = list(backoff_s)

    def _call():
        _INFO_LIMITER.acquire()
        # session pulled fresh each call so a reset_yf_session() between
        # retries actually takes effect.
        session = get_yf_session()
        return yf.Ticker(ticker, session=session).info or {}

    return _retry(_call, retries=retries, backoff_s=bo, label=f"info[{ticker}]")


def fetch_fast_info(ticker: str, *, retries: int = 2,
                    backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0)) -> dict:
    """yfinance fast_info for one ticker (numeric fields).

    Uses the chart endpoint (no crumb required), so this is the *reliable*
    source for market_cap, shares_outstanding, exchange, currency, and
    last_price. Much faster than .info too.

    Returns a plain dict so the caller can treat fast_info and .info
    uniformly.
    """
    bo = list(backoff_s)

    def _call():
        _INFO_LIMITER.acquire()
        session = get_yf_session()
        fi = yf.Ticker(ticker, session=session).fast_info
        # FastInfo is a lazy proxy; access each attribute to materialise it.
        # getattr with default tolerates missing/changing attributes across
        # yfinance versions.
        return {
            "market_cap": getattr(fi, "market_cap", None),
            "shares": getattr(fi, "shares", None),
            "currency": getattr(fi, "currency", None),
            "exchange": getattr(fi, "exchange", None),
            "last_price": getattr(fi, "last_price", None),
            "quote_type": getattr(fi, "quote_type", None),
        }

    return _retry(_call, retries=retries, backoff_s=bo, label=f"fast_info[{ticker}]")


def fetch_bars_batch(
    tickers: list[str],
    *,
    period: str = "60d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    retries: int = 1,
    backoff_s: tuple[float, ...] = (2.0, 4.0),
) -> dict[str, pd.DataFrame]:
    """One yf.download() call for many tickers. Returns dict[ticker -> DataFrame].

    Uses auto_adjust=False so both Close and Adj Close are returned (spec D8).
    Empty/missing tickers return an empty DataFrame in the output dict.
    """
    tickers = [t for t in tickers if t]
    if not tickers:
        return {}
    session = get_yf_session()
    bo = list(backoff_s)

    def _call():
        _BARS_LIMITER.acquire()
        kwargs: dict = dict(
            tickers=" ".join(tickers),
            interval="1d",
            auto_adjust=False,
            actions=False,
            group_by="ticker",
            threads=True,
            progress=False,
            session=session,
        )
        if start is not None:
            kwargs["start"] = start
            if end is not None:
                kwargs["end"] = end
        else:
            kwargs["period"] = period
        return yf.download(**kwargs)

    df = _retry(_call, retries=retries, backoff_s=bo, label="bars_batch")
    if df is None or df.empty:
        return {t: pd.DataFrame() for t in tickers}
    return _split_batch_df(df, tickers)


def _split_batch_df(df: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """yf.download with multiple tickers returns a column-MultiIndex frame."""
    out: dict[str, pd.DataFrame] = {}
    if len(tickers) == 1:
        out[tickers[0]] = df.dropna(how="all")
        return out
    for t in tickers:
        try:
            sub = df[t]
        except KeyError:
            out[t] = pd.DataFrame()
            continue
        out[t] = sub.dropna(how="all")
    return out


def fetch_bars_one(
    ticker: str,
    *,
    period: str = "60d",
    start: Optional[str] = None,
    end: Optional[str] = None,
    retries: int = 2,
    backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0),
) -> pd.DataFrame:
    """Single-ticker bars fetch — used as fallback when batch returns partial data."""
    session = get_yf_session()
    bo = list(backoff_s)

    def _call():
        _BARS_LIMITER.acquire()
        t = yf.Ticker(ticker, session=session)
        if start is not None:
            return t.history(start=start, end=end, interval="1d", auto_adjust=False)
        return t.history(period=period, interval="1d", auto_adjust=False)

    df = _retry(_call, retries=retries, backoff_s=bo, label=f"bars_one[{ticker}]")
    return df if df is not None else pd.DataFrame()


# ─── DataFrame -> bar records ────────────────────────────────────────────────

def df_to_bar_records(df: pd.DataFrame) -> list[dict]:
    """Yahoo bar DataFrame -> list of dicts keyed by ISO date.

    Skips rows where Close or Adj Close is NaN (typical of holidays and
    pre-listing dates). Returns at most one record per date.
    """
    if df is None or df.empty:
        return []
    df = df.reset_index()
    date_col = "Date" if "Date" in df.columns else "Datetime"
    if date_col not in df.columns:
        return []
    out: list[dict] = []
    for _, row in df.iterrows():
        raw_date = row[date_col]
        try:
            ts = pd.Timestamp(raw_date)
        except Exception:
            continue
        if pd.isna(ts):
            continue
        iso_date = ts.strftime("%Y-%m-%d")
        close = row.get("Close")
        adj = row.get("Adj Close", close)
        vol = row.get("Volume")
        if pd.isna(close) or pd.isna(adj):
            continue
        out.append({
            "date": iso_date,
            "close": float(close),
            "adjusted_close": float(adj),
            "volume": int(vol) if pd.notna(vol) else None,
        })
    return out


# ─── Module 4b public entry point: incremental fetch ────────────────────────

def fetch_incremental_prices(
    tickers: list[str],
    db_path: Path,
    *,
    cold_start_period: str = "400d",
    batch_size: int = 100,
    retries: int = 2,
    backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0),
) -> dict[str, str]:
    """Incremental price fetch into prices.db. Returns {ticker: status}.

    For each ticker:
      - if last_fetched_at is today (UTC), skip (idempotent same-day re-runs).
      - if no log row, cold-start: fetch ~cold_start_period (~400 trading days).
      - if log row exists, fetch from last_date+1 to today.

    Cold-starts and deltas are batched together within each batch_size group.
    """
    init_prices_db(db_path)
    statuses: dict[str, str] = {}

    cold: list[str] = []
    delta: dict[str, str] = {}  # ticker -> start ISO date
    today_utc = dt.datetime.utcnow().date()

    for t in tickers:
        if not t:
            continue
        log_row = get_fetch_log(db_path, t)
        if log_row and is_today_utc(log_row.get("last_fetched_at")):
            statuses[t] = log_row.get("fetch_status") or "ok"
            continue
        if not log_row or not log_row.get("last_date"):
            cold.append(t)
        else:
            try:
                last = dt.date.fromisoformat(log_row["last_date"])
            except Exception:
                cold.append(t)
                continue
            if last >= today_utc:
                statuses[t] = log_row.get("fetch_status") or "ok"
                continue
            start_iso = (last + dt.timedelta(days=1)).isoformat()
            delta[t] = start_iso

    # 1. cold-start batches (period mode). Yahoo expects '-' separators.
    for i in range(0, len(cold), batch_size):
        batch = cold[i:i + batch_size]
        yahoo_to_orig = {_yahoo_symbol(t): t for t in batch}
        try:
            results = fetch_bars_batch(
                list(yahoo_to_orig.keys()), period=cold_start_period,
                retries=retries, backoff_s=backoff_s,
            )
        except Exception as e:
            log.warning("cold-start batch failed (%d tickers): %s", len(batch), e)
            for t in batch:
                _persist_one(db_path, t, pd.DataFrame(), error=str(e))
                statuses[t] = "failed"
            continue
        for ysym, orig in yahoo_to_orig.items():
            df = results.get(ysym, pd.DataFrame())
            if df.empty:
                try:
                    df = fetch_bars_one(ysym, period=cold_start_period,
                                        retries=retries, backoff_s=backoff_s)
                except Exception as e:
                    _persist_one(db_path, orig, pd.DataFrame(), error=str(e))
                    statuses[orig] = "failed"
                    continue
            statuses[orig] = _persist_one(db_path, orig, df)

    # 2. delta batches grouped by start-date (rare for >1 group in practice)
    by_start: dict[str, list[str]] = {}
    for t, s in delta.items():
        by_start.setdefault(s, []).append(t)
    today_iso = (today_utc + dt.timedelta(days=1)).isoformat()  # yfinance end is exclusive
    for start_iso, group in by_start.items():
        for i in range(0, len(group), batch_size):
            batch = group[i:i + batch_size]
            yahoo_to_orig = {_yahoo_symbol(t): t for t in batch}
            try:
                results = fetch_bars_batch(
                    list(yahoo_to_orig.keys()), start=start_iso, end=today_iso,
                    retries=retries, backoff_s=backoff_s,
                )
            except Exception as e:
                log.warning("delta batch failed (%d tickers): %s", len(batch), e)
                for t in batch:
                    update_fetch_log(db_path, t, status="failed", error=str(e))
                    statuses[t] = "failed"
                continue
            for ysym, orig in yahoo_to_orig.items():
                df = results.get(ysym, pd.DataFrame())
                if df.empty:
                    update_fetch_log(db_path, orig, status="ok")
                    statuses[orig] = "ok"
                    continue
                statuses[orig] = _persist_one(db_path, orig, df)

    return statuses


def _persist_one(db_path: Path, ticker: str, df: pd.DataFrame,
                 error: Optional[str] = None) -> str:
    """Persist one ticker's bars. Returns status: ok/partial/failed."""
    bars = df_to_bar_records(df)
    if not bars:
        update_fetch_log(db_path, ticker, status="failed",
                         error=error or "empty bars")
        return "failed"
    upsert_bars(db_path, ticker, bars)
    status = "ok"
    update_fetch_log(db_path, ticker, status=status)
    return status


# ─── Price lookup with trading-day tolerance ─────────────────────────────────

def get_price_on_date(
    conn: sqlite3.Connection,
    ticker: str,
    target_date: pd.Timestamp,
    tolerance_trading_days: int = 3,
) -> tuple[Optional[float], Optional[pd.Timestamp]]:
    """Return (adjusted_close, actual_date) closest to target_date within tolerance.

    Tolerance is in TRADING days, but we approximate by querying ±(tol*1.6)
    calendar days (covers weekends/holidays comfortably) and picking the
    nearest available row in the result.
    """
    cal_window = max(int(tolerance_trading_days * 1.6), tolerance_trading_days + 2)
    lo = (target_date - pd.Timedelta(days=cal_window)).strftime("%Y-%m-%d")
    hi = (target_date + pd.Timedelta(days=cal_window)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT date, adjusted_close FROM prices "
        "WHERE ticker=? AND date BETWEEN ? AND ? ORDER BY date",
        (ticker, lo, hi),
    ).fetchall()
    if not rows:
        return None, None
    target = target_date.normalize()
    best_row = min(
        rows,
        key=lambda r: abs((pd.Timestamp(r["date"]) - target).days),
    )
    return float(best_row["adjusted_close"]), pd.Timestamp(best_row["date"])


# ─── Snapshot fetch (used by Module 4a) ──────────────────────────────────────

def _compute_price_derived(bars_for_adv: pd.DataFrame) -> tuple[Optional[float], Optional[float]]:
    """Returns (last_close, adv_30d) from bars. Free; no HTTP."""
    try:
        bar_records = df_to_bar_records(bars_for_adv)
    except Exception:
        return None, None
    if not bar_records:
        return None, None
    last30 = bar_records[-30:]
    closes = [b["close"] for b in last30]
    volumes = [b["volume"] or 0 for b in last30]
    if not closes:
        return None, None
    last_close = closes[-1]
    adv_30d = float(sum(v * c for v, c in zip(volumes, closes)) / max(len(closes), 1))
    return last_close, adv_30d


def fetch_snapshot_one(
    ticker: str,
    *,
    bars_for_adv: pd.DataFrame,
    cached_static: Optional[dict] = None,
    static_is_fresh: bool = False,
    fetch_descriptive_info: bool = False,
    retries: int = 2,
    backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0),
) -> dict:
    """One ticker's snapshot. Layered freshness:

    Always recomputed (cheap, free):
      - last_close, adv_30d  (from bars_for_adv, already fetched in batch)
      - market_cap            (= shares_out × last_close)

    Static fields (long TTL, refetched only when stale):
      - shares_out, exchange, currency  (via fast_info)
      - sector, industry, short/long names  (via .info, only if fetch_descriptive_info=True)

    When `static_is_fresh=True`, no yfinance HTTP at all — pure recomputation
    from `cached_static` + bars. This is the daily-run fast path.

    Status semantics:
      - "ok"      : market_cap present AND (descriptive_info disabled OR sector present)
      - "partial" : market_cap present but some optional field missing
      - "failed"  : no market_cap and no last_close — ticker unusable
    """
    now_iso = dt.datetime.utcnow().isoformat()
    yahoo_sym = _yahoo_symbol(ticker)
    snap: dict = {
        "ticker": ticker,
        "short_name": None, "long_name": None,
        "sector": None, "industry": None,
        "exchange": None, "currency": None,
        "market_cap": None, "shares_out": None,
        "last_close": None, "adv_30d": None,
        "fetched_at": now_iso,
        "fetch_status": "failed",
        "fetch_error": None,
    }
    errors: list[str] = []

    # 1. Always-fresh: price-derived fields from current bars
    last_close, adv_30d = _compute_price_derived(bars_for_adv)
    snap["last_close"] = last_close
    snap["adv_30d"] = adv_30d

    # 2. Static fields: reuse cache if fresh, else fetch
    if static_is_fresh and cached_static is not None:
        for k in ("shares_out", "exchange", "currency",
                  "sector", "industry", "short_name", "long_name"):
            v = cached_static.get(k)
            if v is not None:
                snap[k] = v
        # preserve original fetched_at when only refreshing price-derived fields
        if cached_static.get("fetched_at"):
            snap["fetched_at"] = cached_static["fetched_at"]
    else:
        # 2a. fast_info — primary source for shares_out + exchange/currency
        try:
            fi = fetch_fast_info(yahoo_sym, retries=retries, backoff_s=backoff_s)
            if fi:
                so = fi.get("shares")
                lp = fi.get("last_price")
                snap["shares_out"] = int(so) if so not in (None, 0) else None
                if fi.get("exchange"):
                    snap["exchange"] = fi["exchange"]
                if fi.get("currency"):
                    snap["currency"] = fi["currency"]
                if snap["last_close"] is None and lp not in (None, 0):
                    snap["last_close"] = float(lp)
        except YahooThrottled:
            raise
        except Exception as e:
            errors.append(f"fast_info: {str(e)[:200]}")

        # 2b. .info — descriptive fields, opt-in only
        if fetch_descriptive_info:
            try:
                info = fetch_info(yahoo_sym, retries=retries, backoff_s=backoff_s)
                if info:
                    snap.update({
                        "short_name": info.get("shortName") or info.get("symbol") or ticker,
                        "long_name": info.get("longName") or info.get("shortName") or ticker,
                        "sector": info.get("sector") or snap["sector"],
                        "industry": info.get("industry") or snap["industry"],
                    })
                    if snap["shares_out"] is None and info.get("sharesOutstanding"):
                        snap["shares_out"] = int(info["sharesOutstanding"])
                    if not snap["exchange"] and info.get("exchange"):
                        snap["exchange"] = info["exchange"]
                    if not snap["currency"] and info.get("currency"):
                        snap["currency"] = info["currency"]
            except YahooThrottled:
                raise
            except Exception as e:
                errors.append(f"info: {str(e)[:200]}")

    # 3. Derive market_cap from shares_out × last_close
    if snap["shares_out"] is not None and snap["last_close"] is not None:
        snap["market_cap"] = int(snap["shares_out"] * snap["last_close"])

    # 4. Status determination
    has_cap = snap["market_cap"] is not None
    has_price = snap["last_close"] is not None
    descriptive_required = fetch_descriptive_info
    has_descriptive = bool(snap["short_name"]) and bool(snap["sector"])
    if has_cap and (not descriptive_required or has_descriptive):
        snap["fetch_status"] = "ok"
    elif has_cap or has_price:
        snap["fetch_status"] = "partial"
    else:
        snap["fetch_status"] = "failed"
    if errors:
        snap["fetch_error"] = " | ".join(errors)[:500]

    return snap


def fetch_snapshots_parallel(
    tickers: list[str],
    db_path: Path,
    *,
    cached_static: Optional[dict[str, dict]] = None,
    fresh_static: Optional[set[str]] = None,
    fetch_descriptive_info: bool = False,
    bars_batch_size: int = 150,
    info_max_workers: int = 4,
    retries: int = 2,
    backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0),
) -> dict[str, dict]:
    """Fetch + persist snapshots for all tickers. Returns {ticker -> snapshot dict}.

    Strategy:
      1. Bars: one batched yf.download() per group of bars_batch_size tickers
         (period='60d', enough for 30 trading-day ADV).
      2. For each ticker:
         - last_close, adv_30d: derived from bars (free).
         - shares_out, exchange, etc.: from `cached_static` if ticker is in
           `fresh_static`, otherwise via fast_info.
         - sector/industry/names: only fetched when fetch_descriptive_info=True
           AND the ticker is not in fresh_static.
         - market_cap = shares_out × last_close.
      3. On YahooThrottled, abort cleanly: persist whatever's done, mark
         remaining tickers as 'partial' (with no fetch_error so they retry next run).
    """
    init_prices_db(db_path)
    cached_static = cached_static or {}
    fresh_static = fresh_static or set()
    out: dict[str, dict] = {}

    # 1. Fetch all bars in groups (one HTTP call per group).
    bars_by_ticker: dict[str, pd.DataFrame] = {}
    for i in range(0, len(tickers), bars_batch_size):
        batch = tickers[i:i + bars_batch_size]
        yahoo_to_orig = {_yahoo_symbol(t): t for t in batch}
        log.info("snapshot bars batch %d/%d (%d tickers)",
                 i // bars_batch_size + 1,
                 (len(tickers) + bars_batch_size - 1) // bars_batch_size,
                 len(batch))
        try:
            res = fetch_bars_batch(
                list(yahoo_to_orig.keys()), period="60d",
                retries=retries, backoff_s=backoff_s,
            )
            for ysym, df in res.items():
                bars_by_ticker[yahoo_to_orig.get(ysym, ysym)] = df
        except YahooThrottled:
            log.warning("bars batch hit throttle; skipping remaining bars batches")
            for t in batch:
                bars_by_ticker.setdefault(t, pd.DataFrame())
            break
        except Exception as e:
            log.warning("snapshot bars batch failed (%d tickers): %s", len(batch), e)
            for t in batch:
                bars_by_ticker.setdefault(t, pd.DataFrame())

    # 2. Per-ticker assembly. Throttle-aware: as soon as the global throttle
    # flag flips, all subsequent calls return immediately via _retry's pre-check.
    def _one(ticker: str) -> dict:
        return fetch_snapshot_one(
            ticker,
            bars_for_adv=bars_by_ticker.get(ticker, pd.DataFrame()),
            cached_static=cached_static.get(ticker),
            static_is_fresh=ticker in fresh_static,
            fetch_descriptive_info=fetch_descriptive_info,
            retries=retries, backoff_s=backoff_s,
        )

    completed = 0
    throttled_count = 0
    total = len(tickers)
    with ThreadPoolExecutor(max_workers=info_max_workers) as pool:
        futures = {pool.submit(_one, t): t for t in tickers}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                snap = fut.result()
            except YahooThrottled as e:
                throttled_count += 1
                snap = _stub_snapshot(t, status="partial",
                                      error=f"throttled: {str(e)[:200]}")
            except Exception as e:
                snap = _stub_snapshot(t, status="failed", error=str(e)[:500])
            upsert_snapshot(db_path, snap)
            out[t] = snap
            completed += 1
            if completed % 50 == 0 or completed == total:
                log.info("snapshot %d/%d (last=%s status=%s, throttled=%d)",
                         completed, total, t, snap["fetch_status"], throttled_count)

    if throttled_count:
        log.warning(
            "Yahoo throttle: %d/%d tickers deferred. Cached snapshots intact; "
            "rerun later to refresh deferred rows.",
            throttled_count, total,
        )
    return out


def _stub_snapshot(ticker: str, *, status: str, error: Optional[str]) -> dict:
    return {
        "ticker": ticker,
        "short_name": None, "long_name": None,
        "sector": None, "industry": None,
        "exchange": None, "currency": None,
        "market_cap": None, "shares_out": None,
        "last_close": None, "adv_30d": None,
        "fetched_at": dt.datetime.utcnow().isoformat(),
        "fetch_status": status,
        "fetch_error": error,
    }
