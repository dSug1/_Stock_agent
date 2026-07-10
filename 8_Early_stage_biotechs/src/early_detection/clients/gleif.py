"""GLEIF LEI lookup (free, no key) — https://api.gleif.org/api/v1/lei-records.

Backfills the Legal Entity Identifier, the cleanest cross-market join key (spec §2.3 LEI-first). LEI
is also Module-8's **strongest** identity key, so a wrong LEI is dangerous — it would cause a false
merge on the next universe build. Therefore matching is deliberately **high-precision, low-recall**:
we accept an LEI only when exactly one candidate's normalized legal name equals the entity's
(``pick_lei``); zero or ambiguous → no LEI. Endpoint is a hard-coded public API (no SSRF surface);
fetch is capped + UA'd + fail-open via ``_net``.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import quote

from ..identity import normalize_name
from . import _net

log = logging.getLogger(__name__)

GLEIF_API = "https://api.gleif.org/api/v1/lei-records"

# GLEIF tolerates high-concurrency bursts (~24/s) but throttles sustained load past a burst bucket
# (~130 requests) with HTTP 429. So callers run bounded-concurrency and rely on this 429-aware retry
# (honors Retry-After) as the safety net rather than a fixed low rate. Measured 2026-07-10.
_MAX_RETRIES = 5
_BACKOFF_BASE = 1.5


def _get(url: str, *, timeout: float = 30, retries: int = _MAX_RETRIES) -> Optional[dict]:
    """GET GLEIF JSON with 429-aware retry (Retry-After honored). Fail-open (→ None).

    Separated from ``_net.safe_json`` because that swallows the HTTPError and can't distinguish a 429
    (retryable) from a real failure. Kept as a module hook so tests can monkeypatch it."""
    headers = {"User-Agent": _net.user_agent(), "Accept": "application/vnd.api+json",
               "Accept-Encoding": "identity"}
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(_net.capped_read(resp))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                ra = e.headers.get("Retry-After")
                delay = float(ra) if (ra and ra.isdigit()) else _BACKOFF_BASE ** (attempt + 1)
                log.debug("GLEIF 429; backing off %.1fs (attempt %d)", delay, attempt + 1)
                time.sleep(min(delay, 30.0))
                continue
            log.debug("GLEIF HTTP %s for %s", e.code, url)
            return None
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.debug("GLEIF fetch failed for %s: %s", url, exc)
            return None
    return None

# Internal jurisdiction code → ISO 3166-1 alpha-2 for GLEIF's country filter. Most codes already are
# alpha-2; only the ones we store differently need mapping (we use "UK", GLEIF uses "GB").
_ISO2 = {"UK": "GB"}


def _iso2(country: Optional[str]) -> Optional[str]:
    if not country:
        return None
    c = country.strip().upper()
    return _ISO2.get(c, c if len(c) == 2 else None)


def search_lei(name: str, *, country: Optional[str] = None, page_size: int = 10) -> list[dict]:
    """Fulltext GLEIF search → [{lei, legal_name, country, status}]. 429-safe, fail-open (→ [])."""
    if not name or not name.strip():
        return []
    params = f"filter[fulltext]={quote(name.strip())}&page[size]={int(page_size)}"
    iso = _iso2(country)
    if iso:
        params += f"&filter[entity.legalAddress.country]={iso}"
    data = _get(f"{GLEIF_API}?{params}")
    if not data or not isinstance(data, dict):
        return []
    out: list[dict] = []
    for rec in data.get("data", []) or []:
        attrs = rec.get("attributes", {}) or {}
        ent = attrs.get("entity", {}) or {}
        legal_name = (ent.get("legalName") or {}).get("name")
        ctry = (ent.get("legalAddress") or {}).get("country")
        status = (attrs.get("registration") or {}).get("status")
        lei = rec.get("id") or attrs.get("lei")
        if lei and legal_name:
            out.append({"lei": lei, "legal_name": legal_name, "country": ctry, "status": status})
    return out


def pick_lei(entity_name: str, candidates: list[dict]) -> Optional[str]:
    """Accept an LEI only if EXACTLY ONE distinct LEI has a normalized legal name equal to the
    entity's. Zero or ambiguous → None. High precision: a false LEI causes a false merge next build.

    Prefers ISSUED registrations when both an ISSUED and a lapsed record match the same name but carry
    different LEIs (a lapsed successor); still requires the survivor to be unique."""
    target = normalize_name(entity_name)
    if not target:
        return None
    exact = [c for c in candidates if normalize_name(c["legal_name"]) == target]
    leis = {c["lei"] for c in exact}
    if len(leis) == 1:
        return next(iter(leis))
    if len(leis) > 1:
        issued = {c["lei"] for c in exact if (c.get("status") or "").upper() == "ISSUED"}
        if len(issued) == 1:
            return next(iter(issued))
    return None
