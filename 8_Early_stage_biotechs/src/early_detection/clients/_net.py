"""Shared HTTP plumbing for universe providers (copied/adapted from platform_discoverer/clients/_net.py).

Security baseline (repo SECURITY_AUDIT.md): 64 MiB capped reads (no OOM on a hostile body), the repo
``USER_AGENT`` on every request, ``defusedxml`` for XML (no entity-expansion), per-source rate
limiting. Endpoints are hard-coded public APIs, so there is no user-supplied-URL / SSRF surface.
Fetchers raise; the ``safe_*`` wrappers degrade to None so callers stay fail-open (a dead source →
empty, never a crash).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from threading import Lock
from typing import Any, Optional

try:  # entity-expansion-safe XML (repo dep); fall back to stdlib if absent
    import defusedxml.ElementTree as _ET
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as _ET

log = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 64 * 1024 * 1024

# Best-effort load of the repo-root .env so USER_AGENT (SEC fair-access compliance) is available even
# when a client is imported outside a script entry point. Idempotent; silent if python-dotenv absent.
try:  # pragma: no cover - env wiring
    from pathlib import Path as _Path

    from dotenv import load_dotenv as _load_dotenv

    for _cand in (_Path(__file__).resolve().parents[4] / ".env",
                  _Path(__file__).resolve().parents[3] / ".env"):
        if _cand.exists():
            _load_dotenv(_cand)
            break
except Exception:  # noqa: BLE001
    pass

_FALLBACK_UA = "early-detection/0.1 (biotech screener; set USER_AGENT in .env for SEC compliance)"


def user_agent() -> str:
    """Read USER_AGENT lazily so a .env loaded after import still applies. SEC's cgi-bin rejects a
    UA without a contact, so a properly-set .env value (name + email) is required for US enumeration.
    """
    return os.getenv("USER_AGENT", "").strip() or _FALLBACK_UA


def capped_read(resp, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes}-byte cap")
    return data


class RateLimiter:
    """Minimum-interval throttle (thread-safe). One per source/host."""

    def __init__(self, per_sec: float):
        self.min_interval = 1.0 / per_sec if per_sec > 0 else 0.0
        self._lock = Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            gap = self.min_interval - (now - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


def get_bytes(url: str, *, accept: Optional[str] = None, timeout: float = 30,
              limiter: Optional[RateLimiter] = None, data: Optional[bytes] = None,
              extra_headers: Optional[dict] = None) -> bytes:
    """GET (or POST if ``data``) → raw body, size-capped. Raises on HTTP/network error."""
    if limiter is not None:
        limiter.wait()
    headers = {"User-Agent": user_agent(), "Accept-Encoding": "identity"}
    if accept:
        headers["Accept"] = accept
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return capped_read(resp)


def get_json(url: str, **kw: Any) -> Any:
    return json.loads(get_bytes(url, accept=kw.pop("accept", "application/json"), **kw))


def get_xml(url: str, **kw: Any):
    """Return a parsed (defused) XML root element."""
    return _ET.fromstring(get_bytes(url, **kw))


def get_text(url: str, **kw: Any) -> str:
    return get_bytes(url, **kw).decode("utf-8", "replace")


# ── retrying JSON GET (transient 429/5xx with Retry-After + backoff) ─────────

RETRY_STATUSES = (429, 500, 502, 503, 504)


def _notify_headers(cb: Optional[Any], headers: Any) -> None:
    """Best-effort header observation for a caller-supplied callback; never raises into the fetch path."""
    if cb is None or headers is None:
        return
    try:
        cb(headers)
    except Exception:  # noqa: BLE001 — observation must never break the request
        log.debug("on_headers callback raised; ignoring", exc_info=True)


def get_json_retry(url: str, *, accept: str = "application/json", timeout: float = 30,
                   limiter: Optional[RateLimiter] = None, retries: int = 4,
                   retry_statuses: tuple = RETRY_STATUSES, max_delay: float = 30.0,
                   extra_headers: Optional[dict] = None, data: Optional[bytes] = None,
                   on_headers: Optional[Any] = None) -> Any:
    """GET JSON, retrying transient ``retry_statuses`` (429 rate-limit, 5xx) with backoff that HONORS the
    server's ``Retry-After`` header. Raises on a non-retryable status or once ``retries`` is exhausted.

    This is the reusable version of the per-client retry ``edgar_fts._get_json`` already relies on — a
    bare 429 with no retry silently becomes "no results" (see ``safe_json``), which for OpenAlex dropped
    a whole founder's citation signal and (worse) let the independence refinement stamp it as score-0.

    ``on_headers``, if given, is called with the response headers (an object supporting ``.get(name)``)
    on BOTH success and a retryable HTTPError — so a caller can observe quota headers (e.g. OpenAlex's
    ``X-RateLimit-Remaining`` credit count) without changing the JSON return contract. Header-observation
    failures never break the fetch."""
    for attempt in range(retries + 1):
        if limiter is not None:
            limiter.wait()
        try:
            headers = {"User-Agent": user_agent(), "Accept": accept, "Accept-Encoding": "identity"}
            if extra_headers:
                headers.update(extra_headers)
            req = urllib.request.Request(url, headers=headers, data=data)  # data → POST
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = capped_read(resp)
                _notify_headers(on_headers, getattr(resp, "headers", None))
                return json.loads(body)
        except urllib.error.HTTPError as e:
            _notify_headers(on_headers, getattr(e, "headers", None))
            if e.code in retry_statuses and attempt < retries:
                ra = (e.headers.get("Retry-After") if e.headers else None)
                # Retry-After may be seconds (int) or an HTTP-date; use it when numeric, else backoff.
                delay = float(ra) if (ra and str(ra).strip().replace(".", "", 1).isdigit()) \
                    else 1.5 ** (attempt + 1)
                log.debug("HTTP %s for %s; retry %d in %.1fs", e.code, url, attempt + 1, delay)
                time.sleep(min(delay, max_delay))
                continue
            raise
    raise RuntimeError(f"get_json_retry exhausted retries for {url}")  # pragma: no cover


# ── fail-open wrappers ──────────────────────────────────────────────────────

def safe_json(url: str, **kw: Any) -> Optional[Any]:
    try:
        return get_json(url, **kw)
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        log.warning("safe_json failed for %s: %s", url, exc)
        return None


def safe_json_retry(url: str, **kw: Any) -> Optional[Any]:
    """Fail-open wrapper over :func:`get_json_retry` — returns None only after retries are exhausted, so
    a transient 429/5xx is recovered rather than silently dropped on the first hit. Forwards ``on_headers``
    (quota-header observation) through ``**kw``."""
    try:
        return get_json_retry(url, **kw)
    except Exception as exc:  # noqa: BLE001 — fail-open after retries
        log.warning("safe_json_retry gave up for %s: %s", url, exc)
        return None


def safe_xml(url: str, **kw: Any):
    try:
        return get_xml(url, **kw)
    except Exception as exc:  # noqa: BLE001
        log.warning("safe_xml failed for %s: %s", url, exc)
        return None


def localname(tag: str) -> str:
    """Strip an XML namespace: '{ns}entry' -> 'entry'. For namespace-agnostic parsing."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag
