"""Fallback name -> ticker resolver using SEC's company_tickers.json.

Used when OpenFIGI returns no US-equity match for a CUSIP but the
filing provides a `name_of_issuer`. Matches are produced by
case-insensitive, punctuation-stripped, suffix-stripped equality
against the SEC's ~10k active-filer list.

Important limitations:
- SEC's list contains only currently-active tickered filers. Delisted
  issuers (acquired, renamed, or taken private) will not match.
- Non-common-stock CUSIPs (preferred, warrants, bonds, ETFs) will
  match the common-stock ticker of the same issuer, which is
  technically a different security. Callers that care should inspect
  `ticker_source='sec_name'` rather than trusting the ticker blindly.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_USER_AGENT = "StockPicker contact@stockpicker.local"
SEC_RATE_LIMIT_SLEEP = 0.11
DEFAULT_CACHE_MAX_AGE_DAYS = 7

# OOM guard: cap any single SEC response read into memory.
MAX_RESPONSE_BYTES = 64 * 1024 * 1024

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = (
    PROJECT_ROOT / "Outputs" / "sec_company_tickers.json"
)

# Entity suffixes stripped before comparison. Order matters: longer
# multi-word forms first so "Holdings Inc" doesn't leave "Holdings"
# when "Inc" runs alone. All lowercase; matched after lowercasing.
_SUFFIX_PATTERNS = [
    r"\b(?:incorporated|corporation|corp|inc|llc|lp|l\.p\.|"
    r"ltd|limited|plc|n\.v\.|nv|s\.a\.|sa|ag|co|company|"
    r"holdings?|group|trust|fund|partners|"
    r"pharmaceuticals?|therapeutics|biosciences|biotech|bio|"
    r"technologies|technology|systems|solutions|"
    r"international|global|worldwide|usa|us)\b",
]

_PUNCTUATION = re.compile(r"[.,&/'\"\\()\[\]]+")
_WHITESPACE = re.compile(r"\s+")


HttpGet = Callable[[str], bytes]


def _read_capped(resp, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read up to `max_bytes`; raise if the body exceeds the cap."""
    body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes} byte cap")
    return body


def _default_http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": SEC_USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = _read_capped(resp)
    time.sleep(SEC_RATE_LIMIT_SLEEP)
    return body


def _normalize(name: str) -> str:
    s = name.lower()
    s = _PUNCTUATION.sub(" ", s)
    for pat in _SUFFIX_PATTERNS:
        s = re.sub(pat, " ", s)
    s = _WHITESPACE.sub(" ", s).strip()
    return s


def download_sec_tickers(
    cache_path: Path = DEFAULT_CACHE_PATH,
    *,
    http_get: Optional[HttpGet] = None,
    max_age_days: int = DEFAULT_CACHE_MAX_AGE_DAYS,
    force: bool = False,
) -> Path:
    """Fetch company_tickers.json to cache_path, respecting max_age_days.

    Returns the path to the (freshly or already) cached JSON file.
    """
    http_get = http_get or _default_http_get
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists() and not force:
        age_days = (time.time() - cache_path.stat().st_mtime) / 86400.0
        if age_days < max_age_days:
            log.debug(
                "sec_tickers cache fresh (%.1f days old)", age_days
            )
            return cache_path

    log.info("downloading SEC company_tickers.json -> %s", cache_path)
    body = http_get(SEC_TICKERS_URL)
    cache_path.write_bytes(body)
    return cache_path


def load_sec_name_index(
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> dict[str, dict]:
    """Parse the SEC ticker file and return {normalized_name: record}.

    Records carry the fields {ticker, cik, title}. When two SEC entries
    normalise to the same key (a handful of edge cases with near-
    identical titles), the first one wins and subsequent collisions are
    dropped — an ambiguous match is safer than a wrong one.
    """
    if not cache_path.exists():
        return {}
    raw = json.loads(cache_path.read_text(encoding="utf-8"))
    # SEC format: {"0": {"cik_str": 320193, "ticker": "AAPL",
    #                    "title": "Apple Inc."}, ...}
    index: dict[str, dict] = {}
    collisions: set[str] = set()
    for entry in raw.values():
        title = entry.get("title")
        ticker = entry.get("ticker")
        cik = entry.get("cik_str")
        if not title or not ticker:
            continue
        key = _normalize(title)
        if not key:
            continue
        if key in index and index[key]["ticker"] != ticker:
            collisions.add(key)
            continue
        index[key] = {
            "ticker": ticker,
            "cik": str(cik).zfill(10) if cik is not None else None,
            "title": title,
        }
    for key in collisions:
        index.pop(key, None)
    return index


def resolve_name(
    name: str, name_index: dict[str, dict]
) -> Optional[dict]:
    """Return {ticker, cik, title} for name, or None if no match."""
    if not name:
        return None
    return name_index.get(_normalize(name))
