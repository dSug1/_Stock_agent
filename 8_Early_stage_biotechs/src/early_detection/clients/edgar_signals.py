"""SEC EDGAR recent-filings reader for the capital-markets signal (spec §3.5).

One call per entity to the submissions API (`data.sec.gov/submissions/CIK##########.json`) returns the
filer's recent filings as parallel arrays. We surface material forms — 5%+ ownership crossings
(SC 13D/G), insider Form-4, material 8-Ks, and registration/shelf/ATM raises — as `signal` rows.

Pure parser (`parse_recent_filings`) is unit-tested; the fetch is fail-open (→ []) and needs the SEC
`User-Agent` (loaded from .env by `_net`). US filers only — a non-US ticker has no CIK. All reads go
through `_net`'s 64 MiB cap.

Note: the submissions feed gives form + date + accession, not the *filer* of a 13D/G (which fund).
Resolving the specific specialist fund from the filing index is a deferred refinement — the presence
of a fresh 13D/G on a micro-cap is already the signal.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from . import _net

log = logging.getLogger(__name__)

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


def parse_recent_filings(payload: dict) -> list[dict]:
    """Submissions payload → [{form, date, accession, primary_doc}] from ``filings.recent``.

    The recent block holds parallel arrays; we zip them defensively (a short array just truncates)."""
    recent = (((payload or {}).get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accns = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    out: list[dict] = []
    for i, form in enumerate(forms):
        d = dates[i] if i < len(dates) else None
        out.append({
            "form": form,
            "date": d,
            "accession": accns[i] if i < len(accns) else None,
            "primary_doc": docs[i] if i < len(docs) else None,
        })
    return out


def _cik_int(cik: Optional[str]) -> Optional[int]:
    if not cik:
        return None
    digits = "".join(ch for ch in str(cik) if ch.isdigit())
    return int(digits) if digits else None


def recent_material_filings(cik: str, *, material_forms: set[str], lookback_days: int,
                            today: Optional[str] = None,
                            limiter: Optional[_net.RateLimiter] = None) -> list[dict]:
    """Recent filings for ``cik`` filtered to ``material_forms`` within ``lookback_days``. Fail-open."""
    ci = _cik_int(cik)
    if ci is None:
        return []
    # safe_json_retry: SEC 429s under sustained load; retry with Retry-After rather than drop the filer.
    payload = _net.safe_json_retry(SUBMISSIONS.format(cik=ci), limiter=limiter)
    if not payload:
        return []
    cutoff = _cutoff(lookback_days, today)
    out = []
    for f in parse_recent_filings(payload):
        if f["form"] in material_forms and _within(f["date"], cutoff):
            out.append(f)
    return out


def _cutoff(lookback_days: int, today: Optional[str]) -> str:
    """ISO cutoff date = today - lookback_days. ``today`` injectable for deterministic tests."""
    base = date.fromisoformat(today) if today else date.today()
    return (base - timedelta(days=lookback_days)).isoformat()


def _within(filing_date: Optional[str], cutoff: str) -> bool:
    return bool(filing_date) and len(filing_date) == 10 and filing_date >= cutoff
