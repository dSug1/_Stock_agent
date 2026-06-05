"""Module 7 — live share-price fetcher (D25).

In-memory TTL cache around yfinance for intraday price refresh. Mirrors
the 0_Renderer 2_stock_visualizer pattern but with a longer TTL (M7's
deep-dive ranking doesn't need 6-second granularity — 60s is enough).

Used by:
  • `scripts/3_7_serve_selection.py` — `/api/live_price` endpoint that
    the HTML JS polls during market hours.
  • `scripts/3_7_deep_dive.py` — before pack-building, refresh the
    price for each dispatched ticker so Claude sees the **latest** price.

The function is intentionally narrow: one batched call per request,
falls back to per-ticker on batch failure.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import io
import logging
import sys
import threading
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

try:
    import yfinance as yf
except ImportError:                                              # pragma: no cover
    yf = None                                                    # type: ignore


_DEFAULT_TTL_S = 60.0     # 60s — same scale 0_Renderer uses for its slowest pollers
_FAILURE_TTL_S = 1800.0   # D29 — cache a fetch failure for 30 min so delisted
                          # tickers (e.g., DVAX) don't get retried every poll.


class _SilenceYfinance:
    """Context manager that swallows yfinance's noisy stderr complaints
    about delisted / 404 tickers so the bat/console output stays readable.

    yfinance prints multi-line failure traces on stderr for every batch
    that contains a delisted ticker — those are diagnostic only; we already
    capture the failure in `LivePrice.error`.
    """
    def __enter__(self):
        self._buf = io.StringIO()
        self._cm = contextlib.redirect_stderr(self._buf)
        self._cm.__enter__()
        # yfinance also uses Python logging — turn it off temporarily.
        self._prev_level = logging.getLogger("yfinance").level
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        return self

    def __exit__(self, *exc):
        self._cm.__exit__(*exc)
        logging.getLogger("yfinance").setLevel(self._prev_level)


@dataclass
class LivePrice:
    ticker: str
    price_usd: Optional[float]
    fetched_at_utc: str
    source: str = "yfinance"
    error: Optional[str] = None


@dataclass
class _CacheEntry:
    price: LivePrice
    inserted_monotonic: float


_LOCK = threading.Lock()
_CACHE: dict[str, _CacheEntry] = {}


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _now_mono() -> float:
    import time
    return time.monotonic()


def _fresh(entry: _CacheEntry, ttl_s: float) -> bool:
    # D29 — successful fetches expire at ttl_s; failures (no price_usd) get
    # the longer _FAILURE_TTL_S so delisted tickers (DVAX) aren't retried
    # every poll cycle.
    age = _now_mono() - entry.inserted_monotonic
    if entry.price.price_usd is None:
        return age < _FAILURE_TTL_S
    return age < ttl_s


def _fetch_one(ticker: str) -> LivePrice:
    """Single-ticker fetch. Uses yfinance .fast_info when available (cheap),
    falls back to .history(period='1d') if needed."""
    if yf is None:
        return LivePrice(ticker, None, _now_iso(), error="yfinance not installed")
    try:
        with _SilenceYfinance():
            tk = yf.Ticker(ticker)
            try:
                fast = tk.fast_info
                px = float(getattr(fast, "last_price", None)
                           or getattr(fast, "regular_market_price", None)
                           or fast.get("lastPrice")
                           or 0.0)
                if px > 0:
                    return LivePrice(ticker, px, _now_iso())
            except Exception:                                    # noqa: BLE001
                pass
            df = tk.history(period="1d", auto_adjust=True)
        if df is None or df.empty or "Close" not in df.columns:
            return LivePrice(ticker, None, _now_iso(), error="empty history")
        closes = df["Close"].dropna()
        if closes.empty:
            return LivePrice(ticker, None, _now_iso(), error="all-NaN history")
        return LivePrice(ticker, float(closes.iloc[-1]), _now_iso())
    except Exception as e:                                       # noqa: BLE001
        return LivePrice(ticker, None, _now_iso(),
                         error=f"{type(e).__name__}: {e}")


def _fetch_batch(tickers: list[str]) -> dict[str, LivePrice]:
    """yf.download(period='1d') for many tickers in one HTTP call."""
    if not tickers:
        return {}
    if yf is None:
        return {t: LivePrice(t, None, _now_iso(), error="yfinance not installed")
                for t in tickers}
    if len(tickers) == 1:
        return {tickers[0]: _fetch_one(tickers[0])}
    try:
        with _SilenceYfinance():
            df = yf.download(
                tickers=" ".join(tickers),
                period="1d", auto_adjust=True,
                group_by="ticker", progress=False, threads=True,
            )
    except Exception as e:                                       # noqa: BLE001
        log.warning("batch yfinance failed: %s; falling back per-ticker", e)
        return {t: _fetch_one(t) for t in tickers}

    out: dict[str, LivePrice] = {}
    fetched = _now_iso()
    for t in tickers:
        try:
            sub = df[t].dropna(how="all")
            closes = sub["Close"].dropna() if "Close" in sub.columns else None
            if closes is None or closes.empty:
                out[t] = _fetch_one(t)      # per-ticker fallback for empties
                continue
            out[t] = LivePrice(t, float(closes.iloc[-1]), fetched)
        except Exception as e:                                   # noqa: BLE001
            out[t] = LivePrice(t, None, fetched,
                               error=f"batch decode: {type(e).__name__}: {e}")
    return out


def get_live_prices(
    tickers: list[str],
    *,
    ttl_s: float = _DEFAULT_TTL_S,
    force: bool = False,
) -> dict[str, LivePrice]:
    """Return live prices for `tickers`. Uses an in-memory TTL cache.

    Tickers whose cache entry is still fresh return immediately; the rest
    are batched into one yfinance call.
    """
    tickers = [t.upper().strip() for t in tickers if t and t.strip()]
    if not tickers:
        return {}

    out: dict[str, LivePrice] = {}
    to_fetch: list[str] = []
    with _LOCK:
        for t in tickers:
            entry = _CACHE.get(t)
            if entry is not None and not force and _fresh(entry, ttl_s):
                out[t] = entry.price
            else:
                to_fetch.append(t)

    if to_fetch:
        fresh = _fetch_batch(to_fetch)
        with _LOCK:
            mono = _now_mono()
            for t, lp in fresh.items():
                _CACHE[t] = _CacheEntry(price=lp, inserted_monotonic=mono)
                out[t] = lp
    return out


def get_live_price(ticker: str, *, ttl_s: float = _DEFAULT_TTL_S,
                   force: bool = False) -> LivePrice:
    return get_live_prices([ticker], ttl_s=ttl_s, force=force).get(
        ticker.upper().strip(),
        LivePrice(ticker, None, _now_iso(), error="no result"),
    )


def clear_cache() -> None:
    """Test/debug helper — wipe the in-memory TTL cache."""
    with _LOCK:
        _CACHE.clear()


def prewarm_from_disk(disk_cache_path) -> tuple[int, int]:
    """D38 follow-up (2026-06-04) — populate the in-memory cache from the
    renderer's disk price cache so the live-price server doesn't pay a
    cold-cache penalty (~50-60s for ~300 tickers) on the first
    `/api/live_price` poll after `run_3_Biopharm_render.bat` launches.

    The renderer's disk cache (`data/render_price_cache.json`) is written
    by `scripts/3_6_render_scores.py::_fetch_render_time_prices` and has
    the shape:

        {
          "TICKER": {
            "price_usd":       18.56,           # null for cached failures
            "fetched_at_utc":  "2026-06-04...",
            "cached_at_epoch": 1748497398.45
          }, ...
        }

    Successes prewarm the in-memory _CACHE with `inserted_monotonic = now`,
    so they're fresh for the next `_DEFAULT_TTL_S` (60s). Failures prewarm
    too — they'll be considered fresh for the longer `_FAILURE_TTL_S`
    (30 min), avoiding re-probes of delisted/illiquid tickers.

    Returns ``(n_successes, n_failures)`` prewarmed.
    """
    import json
    from pathlib import Path
    p = Path(disk_cache_path)
    if not p.exists():
        return (0, 0)
    try:
        cache = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return (0, 0)
    if not isinstance(cache, dict):
        return (0, 0)
    now_mono = _now_mono()
    n_ok = 0
    n_fail = 0
    with _LOCK:
        for ticker, entry in cache.items():
            if not isinstance(entry, dict):
                continue
            t = str(ticker).strip().upper()
            if not t:
                continue
            price = entry.get("price_usd")
            fetched_at = entry.get("fetched_at_utc") or _now_iso()
            if price:
                try:
                    px = float(price)
                except (TypeError, ValueError):
                    continue
                lp = LivePrice(ticker=t, price_usd=px, fetched_at_utc=fetched_at)
                _CACHE[t] = _CacheEntry(price=lp, inserted_monotonic=now_mono)
                n_ok += 1
            else:
                # Cached failure — extends to _FAILURE_TTL_S window.
                lp = LivePrice(ticker=t, price_usd=None,
                               fetched_at_utc=fetched_at,
                               error="prewarmed failure (disk cache)")
                _CACHE[t] = _CacheEntry(price=lp, inserted_monotonic=now_mono)
                n_fail += 1
    return (n_ok, n_fail)
