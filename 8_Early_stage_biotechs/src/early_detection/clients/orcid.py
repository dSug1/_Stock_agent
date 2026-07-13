"""ORCID Public API client (FREE; needs free OAuth *public-API* credentials, NOT paid membership) — an
OPTIONAL precision booster for the author-resolution fallback (spec §3.1 support; decision D25).

ORCID gives a persistent author identity (the ORCID iD) + affiliations — a stronger disambiguator than a
name string. It is strictly OPT-IN: active only when ``ORCID_CLIENT_ID`` **and** ``ORCID_CLIENT_SECRET``
are set in the environment; without them the fallback runs Crossref-only (fully functional). Flow:
name → iD (expanded-search) → the founder's DOIs (works), used to confirm which Crossref works are really
the founder's before mapping the foundational paper to OpenAlex.

Security (repo SECURITY_AUDIT.md):
  - **Secrets from ENV only** (never yaml, never a CLI arg). The client id/secret and the OAuth token are
    NEVER logged (error logs carry only the exception *type*), never persisted, held in memory only.
  - **No SSRF surface** — hard-coded HTTPS hosts (token + API), only encoded query/path values vary. The
    ORCID iD is regex-validated before it goes into a path.
  - **JSON only** (``Accept: application/json``) → no XML entity-expansion surface.
  - 64 MiB capped reads + retry via the shared ``_net``; fail-open (creds/token/fetch failure → the
    fallback silently proceeds Crossref-only).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional
from urllib.parse import quote, urlencode

from . import _net

log = logging.getLogger(__name__)

TOKEN_URL = "https://orcid.org/oauth/token"
API_BASE = "https://pub.orcid.org/v3.0"

# ORCID public API allows ~24/s; stay well under with a shared limiter.
_LIMITER = _net.RateLimiter(per_sec=8.0)
_ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")


def credentials() -> Optional[tuple[str, str]]:
    """(client_id, client_secret) from the environment, or None if either is absent. This is the sole
    activation switch for the ORCID path."""
    cid = os.getenv("ORCID_CLIENT_ID", "").strip()
    sec = os.getenv("ORCID_CLIENT_SECRET", "").strip()
    return (cid, sec) if cid and sec else None


def get_token() -> Optional[str]:
    """Two-legged ``client_credentials`` token for the public API (``scope=/read-public``). Returns None if
    creds are absent or the request fails — the caller then runs Crossref-only. NEVER logs the secret/body
    or the returned token."""
    creds = credentials()
    if not creds:
        return None
    cid, sec = creds
    body = urlencode({"client_id": cid, "client_secret": sec,
                      "grant_type": "client_credentials", "scope": "/read-public"}).encode()
    try:
        payload = _net.get_json_retry(
            TOKEN_URL, accept="application/json", data=body, limiter=_LIMITER,
            extra_headers={"Content-Type": "application/x-www-form-urlencoded"})
    except Exception as exc:  # noqa: BLE001 — fail-open; log ONLY the type, never the body/creds
        log.warning("orcid: token request failed (%s); proceeding Crossref-only", type(exc).__name__)
        return None
    return (payload or {}).get("access_token")


def _auth_get(url: str, token: str) -> Optional[dict]:
    return _net.safe_json_retry(url, limiter=_LIMITER, accept="application/json",
                                extra_headers={"Authorization": f"Bearer {token}"})


def parse_expanded_search(payload: dict) -> list[dict]:
    """expanded-search response → ``[{orcid, given, family, institutions:[str]}]``."""
    out = []
    for r in (payload or {}).get("expanded-result", []) or []:
        insts = r.get("institution-name") or []
        out.append({
            "orcid": r.get("orcid-id"),
            "given": r.get("given-names") or "",
            "family": r.get("family-names") or "",
            "institutions": list(insts) if isinstance(insts, list) else [insts],
        })
    return out


def search(name: str, token: str, *, rows: int = 10) -> Optional[list[dict]]:
    """Candidate ORCID records for ``name``. None on fetch failure vs [] on no-match/empty token."""
    if not name or not name.strip() or not token:
        return []
    url = f"{API_BASE}/expanded-search/?q={quote(name.strip(), safe='')}&rows={int(rows)}"
    payload = _auth_get(url, token)
    if payload is None:
        return None
    return parse_expanded_search(payload)


def parse_work_dois(payload: dict, *, max_dois: int = 100) -> list[str]:
    dois = []
    for g in (payload or {}).get("group", []) or []:
        for s in g.get("work-summary", []) or []:
            for e in ((s.get("external-ids") or {}).get("external-id") or []):
                if (e.get("external-id-type") or "").lower() == "doi" and e.get("external-id-value"):
                    dois.append(e["external-id-value"])
    return dois[:max_dois]


def work_dois(orcid: str, token: str, *, max_dois: int = 100) -> Optional[list[str]]:
    """DOIs of the works on an ORCID record. None on an invalid iD or fetch failure."""
    if not token or not _ORCID_RE.match(orcid or ""):
        return None
    url = f"{API_BASE}/{quote(orcid, safe='-')}/works"
    payload = _auth_get(url, token)
    if payload is None:
        return None
    return parse_work_dois(payload, max_dois=max_dois)
