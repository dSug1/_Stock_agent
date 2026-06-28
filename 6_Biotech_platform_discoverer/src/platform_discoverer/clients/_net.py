"""Shared HTTP plumbing for the directory/evidence clients.

Security baseline (repo SECURITY_AUDIT.md): 64 MiB capped reads (no OOM on a hostile body), the repo
`USER_AGENT` on every request, `defusedxml` for XML (no entity-expansion), per-source rate limiting.
Endpoints are hard-coded public APIs, so there is no user-supplied-URL / SSRF surface. Fetchers raise;
the `safe_*` wrappers degrade to None so callers stay fail-open (a dead source → empty, never a crash).
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

_FALLBACK_UA = "platform-discoverer/0.1 (biotech screener; set USER_AGENT in .env for SEC compliance)"


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


# ── fail-open wrappers ──────────────────────────────────────────────────────

def safe_json(url: str, **kw: Any) -> Optional[Any]:
    try:
        return get_json(url, **kw)
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        log.warning("safe_json failed for %s: %s", url, exc)
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


_LEGAL_SUFFIX = re.compile(
    r"[,\.]?\s+(inc|incorporated|corp|corporation|co|ltd|limited|llc|plc|lp|"
    r"holdings|group|sa|s\.a|ag|nv|n\.v|asa|a/s|ab|se|oyj|spa|s\.p\.a)\.?$",
    re.IGNORECASE)


def clean_name(name: str) -> str:
    """Strip trailing legal-entity suffixes for cleaner provider queries.

    'Acrivon Therapeutics, Inc.' -> 'Acrivon Therapeutics'. Applied iteratively (a name can carry
    two, e.g. 'Foo Holdings Ltd'). Keeps descriptive words like 'Therapeutics' that aid matching.
    """
    out = (name or "").strip()
    for _ in range(3):
        new = _LEGAL_SUFFIX.sub("", out).strip().rstrip(",")
        if new == out:
            break
        out = new
    return out
