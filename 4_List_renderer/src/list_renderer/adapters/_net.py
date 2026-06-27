"""Shared network-fetch hardening (SSRF gate + capped, redirect-validating GET).

Every adapter that fetches a config/DB/Claude-supplied URL routes through here so
one place enforces the policy:

  * scheme must be http/https (no file://, ftp://, gopher://, data:, ...);
  * the host must not resolve to a loopback / private / link-local / reserved /
    multicast address (blocks cloud metadata 169.254.169.254, other localhost
    services, and internal hosts) — checked across ALL resolved addresses;
  * redirects are re-validated against the same gate (a public URL cannot 302 to
    an internal target);
  * the response body is capped (memory-exhaustion DoS guard).

Stdlib only (urllib + socket + ipaddress). Raises `BlockedURLError` (a ValueError
subclass) so existing adapter `except Exception` fail-open paths catch it
unchanged.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.request
from urllib.parse import urlsplit

# 64 MiB: generous for any feed/page/JSON we fetch, but bounds a hostile body.
MAX_FETCH_BYTES = 64 * 1024 * 1024

_ALLOWED_SCHEMES = ("http", "https")


class BlockedURLError(ValueError):
    """Raised when a URL fails the SSRF policy (scheme or host/IP)."""


def _ip_is_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable -> refuse
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def validate_url(url: str) -> str:
    """Return `url` unchanged if it passes the SSRF policy, else raise
    `BlockedURLError`. Resolves the host and rejects if ANY resolved address is
    private/loopback/link-local/reserved/multicast (incl. 169.254.169.254)."""
    if not url:
        raise BlockedURLError("empty url")
    parts = urlsplit(url)
    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        raise BlockedURLError(f"scheme not allowed: {parts.scheme!r}")
    host = parts.hostname
    if not host:
        raise BlockedURLError("url has no host")
    if host == "169.254.169.254":
        raise BlockedURLError("blocked metadata host")
    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedURLError(f"could not resolve host {host!r}: {exc}") from exc
    for info in infos:
        ip = info[4][0]
        if _ip_is_blocked(ip):
            raise BlockedURLError(f"host {host!r} resolves to blocked address {ip}")
    return url


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-run the SSRF gate on every redirect target (closes the public->internal
    302 bypass / TOCTOU)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_ValidatingRedirectHandler)


def fetch_bytes(url: str, *, timeout: int, headers: dict | None = None,
                max_bytes: int = MAX_FETCH_BYTES) -> bytes:
    """Validated, redirect-checked, size-capped GET. Returns the raw body bytes.
    Raises `BlockedURLError` for a blocked URL/redirect, `ValueError` if the body
    exceeds `max_bytes`."""
    validate_url(url)
    req = urllib.request.Request(url, headers=headers or {})
    with _OPENER.open(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"response exceeded {max_bytes} byte cap")
    return data
