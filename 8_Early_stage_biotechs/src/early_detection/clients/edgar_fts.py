"""SEC EDGAR full-text search (efts.sec.gov) — the ownership-crossing signal source (spec §3.5).

Unlike the submissions API (which indexes filings under the *filer's* CIK, so a company never sees a
13D/G filed *about* it — the M7 finding), full-text search returns a filing with **all** its associated
CIKs in ``_source.ciks`` + ``display_names``. So we search per specialist fund for SC 13D/G filings and
recover the subject company's CIK directly — the fund-first approach (~20 queries, not one per company).

Pure parser (`parse_hits`) is unit-tested; the fetch is fail-open (→ []) and uses the SEC ``User-Agent``
(loaded from .env by ``_net``). Reads go through ``_net``'s 64 MiB cap. Endpoint confirmed live 2026-07-10.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import quote

from . import _net

log = logging.getLogger(__name__)

EFTS = "https://efts.sec.gov/LATEST/search-index"
PAGE_SIZE = 100   # efts returns up to 100 hits/page; paginate via `from`

# SEC relabeled the beneficial-ownership forms in its 2024-25 EDGAR modernization: the old "SC 13D"/
# "SC 13G" root-form labels only match pre-~2025 filings; recent ones are "SCHEDULE 13D"/"SCHEDULE 13G".
# Query BOTH so the lookback window spans the transition. (Confirmed empirically 2026-07-10.)
OWNERSHIP_FORMS = "SCHEDULE 13D,SCHEDULE 13G,SC 13D,SC 13G"


def _get_json(url: str, *, limiter: Optional[_net.RateLimiter] = None, timeout: float = 30,
              retries: int = 3) -> Optional[dict]:
    """GET efts JSON with retry on transient 429/5xx (efts 500s intermittently under load). Fail-open.

    Distinct from ``_net.safe_json`` so a transient 500 is retried, not silently treated as "no
    results" (which would drop a whole fund's filings — e.g. Baker Bros)."""
    for attempt in range(retries + 1):
        if limiter is not None:
            limiter.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _net.user_agent(),
                                                       "Accept": "application/json",
                                                       "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(_net.capped_read(resp))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After")
                delay = float(ra) if (ra and ra.isdigit()) else 1.5 ** (attempt + 1)
                log.debug("efts HTTP %s; retry in %.1fs", e.code, delay)
                time.sleep(min(delay, 20.0))
                continue
            log.warning("efts HTTP %s for %s", e.code, url)
            return None
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.debug("efts fetch failed for %s: %s", url, exc)
            return None
    return None


def parse_hits(payload: dict) -> list[dict]:
    """efts response → [{form, adsh, file_date, ciks:[...], display_names:[...]}]. Defensive."""
    hits = (((payload or {}).get("hits") or {}).get("hits")) or []
    out: list[dict] = []
    for h in hits:
        src = h.get("_source", {}) or {}
        adsh = (h.get("_id") or "").split(":")[0] or src.get("adsh")
        roots = src.get("root_forms") or []
        out.append({
            "form": src.get("file_type") or (roots[0] if roots else None),
            "adsh": adsh,
            "file_date": src.get("file_date"),
            "ciks": [str(c) for c in (src.get("ciks") or [])],
            "display_names": list(src.get("display_names") or []),
        })
    return out


def total_hits(payload: dict) -> int:
    return int((((payload or {}).get("hits") or {}).get("total") or {}).get("value") or 0)


def search_filings(query: str, *, forms: str = OWNERSHIP_FORMS, startdt: Optional[str] = None,
                   enddt: Optional[str] = None, max_pages: int = 5,
                   limiter: Optional[_net.RateLimiter] = None) -> list[dict]:
    """Full-text search for ``query`` restricted to ``forms`` (+ optional date window). Fail-open (→ []).

    Paginates up to ``max_pages`` × 100 hits. ``query`` is phrase-quoted so a multi-word fund name is
    matched as a phrase, not OR-ed tokens."""
    if not query or not query.strip():
        return []
    base = (f"{EFTS}?q={quote(chr(34) + query.strip() + chr(34))}"
            f"&forms={quote(forms)}")
    if startdt and enddt:
        base += f"&dateRange=custom&startdt={startdt}&enddt={enddt}"
    out: list[dict] = []
    for page in range(max_pages):
        url = base + (f"&from={page * PAGE_SIZE}" if page else "")
        payload = _get_json(url, limiter=limiter)
        if not payload:
            break
        hits = parse_hits(payload)
        if not hits:
            break
        out.extend(hits)
        if len(hits) < PAGE_SIZE:
            break
    return out
