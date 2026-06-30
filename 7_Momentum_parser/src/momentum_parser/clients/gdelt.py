"""GDELT news provider — real Stage-2 media dimension (article volume + tone).

GDELT DOC 2.0 API (TimelineVolRaw = article counts, TimelineTone = average tone). Ported from
5_Hype_parser's `ingest/gdelt.py` and **hardened against HTTP 429** — which GDELT throws aggressively when
queried fast across many tickers. The 429-avoidance discipline (the 5_Hype lesson, extended):

  1. **Same-day disk cache** — the biggest lever; a re-run never re-hits GDELT for a query already fetched
     today (TTL `cache_ttl_s`). 2× modes × N tickers collapses to one fetch each per day.
  2. **Proactive inter-call spacing** — a global minimum interval (`min_interval_s`, default 5s) between
     *any* two GDELT calls, so we never burst.
  3. **Retry with backoff, honoring `Retry-After`** on 429, then **fail open** (return empty — a signal we
     can't fetch is not negative evidence).

Security: hardcoded HTTPS endpoint (no SSRF — ticker only enters query params), 64MiB capped read, no creds.
Matches the `news` client interface (`daily_counts`/`daily_tone`) so it drops into Stage 2. Tunables +
test hooks come from the module-global `OPTS` (set by `stage2_harvest`)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

API = "https://api.gdeltproject.org/api/v2/doc/doc"
MAX_BYTES = 64 * 1024 * 1024
_DEFAULT_UA = "MomentumParser/0.1 (research; local)"

# Set by stage2_harvest from cfg['gdelt']; may also carry test hooks (http_get, cache_dir).
OPTS: dict = {}
_last_call = [0.0]


def _opt(k, default=None):
    return OPTS.get(k, default)


# --------------------------------------------------------------------------- HTTP (isolated)
def _capped_read(resp) -> bytes:
    data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("gdelt response exceeds 64MiB cap")
    return data


def _default_http_get(url: str, timeout: int = 30) -> str:
    import os
    ua = os.environ.get("USER_AGENT") or _DEFAULT_UA
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _capped_read(resp).decode("utf-8", "replace")


def _throttle() -> None:
    iv = float(_opt("min_interval_s", 5.0))
    if iv <= 0:
        return
    wait = iv - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


# --------------------------------------------------------------------------- cache (same-day)
def _cache_dir() -> Path:
    d = Path(_opt("cache_dir") or "data/gdelt_cache")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(query: str, mode: str, start: str, end: str) -> str:
    import hashlib
    return hashlib.sha256(f"{query}|{mode}|{start}|{end}".encode()).hexdigest()[:24]


def _cache_get(key: str) -> str | None:
    p = _cache_dir() / f"{key}.json"
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > float(_opt("cache_ttl_s", 86400)):
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _cache_put(key: str, payload: str) -> None:
    try:
        (_cache_dir() / f"{key}.json").write_text(payload, encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- fetch (429-safe)
def _fetch(query: str, mode: str, start: str, end: str) -> str:
    key = _cache_key(query, mode, start, end)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    http_get = _opt("http_get") or _default_http_get
    url = f"{API}?" + urllib.parse.urlencode(
        {"query": query, "mode": mode, "format": "json", "timelinesmooth": "0",
         "startdatetime": start, "enddatetime": end})
    retries = int(_opt("retries", 2))
    backoff = float(_opt("backoff_s", 5.0))
    for attempt in range(retries + 1):
        try:
            _throttle()
            payload = http_get(url)
            _cache_put(key, payload)
            return payload
        except urllib.error.HTTPError as e:           # includes 429
            if attempt < retries:
                ra = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
                try:
                    delay = float(ra) if ra else backoff * (attempt + 1)
                except (TypeError, ValueError):
                    delay = backoff * (attempt + 1)
                time.sleep(delay)
                continue
            return ""                                  # fail open
        except Exception:
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            return ""
    return ""


# --------------------------------------------------------------------------- parse (pure)
def parse_timeline_daily(payload: str) -> dict:
    """Parse a GDELT timeline JSON into {YYYY-MM-DD: value} (summed per day)."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return {}
    daily: dict = defaultdict(float)
    for series in data.get("timeline", []):
        for pt in series.get("data", []):
            ds = str(pt.get("date", ""))
            if len(ds) >= 8 and ds[:8].isdigit():
                day = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]}"
            elif len(ds) >= 10 and ds[4] == "-" and ds[7] == "-":
                day = ds[:10]
            else:
                continue
            try:
                daily[day] += float(pt.get("value", 0) or 0)
            except (TypeError, ValueError):
                continue
    return dict(daily)


def _window(asof: str | None, lookback: int) -> tuple[str, str, date]:
    end_d = date.fromisoformat(asof) if asof else date.today()
    start_d = end_d - timedelta(days=lookback)
    return (start_d.strftime("%Y%m%d000000"), end_d.strftime("%Y%m%d235959"), end_d)


def _series(daymap: dict, end_d: date, lookback: int, transform=lambda v: v) -> list[float]:
    """Daily values over [end-lookback .. end] ascending; missing days -> 0."""
    out = []
    for i in range(lookback, -1, -1):
        d = (end_d - timedelta(days=i)).isoformat()
        out.append(transform(daymap.get(d, 0.0)))
    return out


def _query(ticker: str) -> str:
    """Prefer a company-NAME phrase query (precise; e.g. "Tesla" → 277-581 articles/day) over a
    ticker-phrase ("GME stock" → near-zero). Names come from discovery via OPTS['names']."""
    name = (_opt("names", {}) or {}).get(ticker)
    if name:
        return str(_opt("name_query_template", '"{name}"')).format(name=name)
    return str(_opt("query_template", "{ticker} stock")).format(ticker=ticker)


# --------------------------------------------------------------------------- public (news interface)
def daily_counts(ticker: str, asof: str | None = None, lookback: int | None = None) -> list[float]:
    lb = int(lookback or _opt("lookback", 60))
    start, end, end_d = _window(asof, lb)
    daymap = parse_timeline_daily(_fetch(_query(ticker), "timelinevolraw", start, end))
    return _series(daymap, end_d, lb) if daymap else []


def daily_tone(ticker: str, asof: str | None = None, lookback: int | None = None) -> list[float]:
    lb = int(lookback or _opt("lookback", 60))
    start, end, end_d = _window(asof, lb)
    daymap = parse_timeline_daily(_fetch(_query(ticker), "timelinetone", start, end))
    # GDELT tone ~ -100..100 (usually -10..10) -> normalize to [-1, 1] for features.media_features
    norm = lambda v: max(-1.0, min(1.0, v / 10.0))
    return _series(daymap, end_d, lb, transform=norm) if daymap else []
