"""ClinicalTrials.gov v2 client (free, no key) — the clinical-stage signal source (spec §3.2).

CT.gov's v2 REST API returns structured study records (NCT id, title, phase, status, lead sponsor +
collaborators, start date) with NO API key. We query by sponsor name to find a company's trials; the
signal layer then keeps only studies whose LEAD sponsor (or a collaborator) actually matches the
company — a bare ``query.spons`` hit can be an investigator-sponsored trial merely *using* the
company's drug (e.g. a cancer-center Phase 2 of an Acrivon compound, sponsored by the center, not
Acrivon). Clinical stage is an INDEPENDENT convergence dimension: unlike the literature signal it does
not depend on resolving a founder to an OpenAlex author, so it reaches names the citation trail misses.

Pure parser (``parse_studies``) is unit-tested; the fetch is fail-open (→ []), paginated + capped, and
goes through ``_net``'s retry (429/5xx honoring Retry-After) + a rate limiter. The endpoint is
hard-coded (no user-URL / SSRF surface); reads use ``_net``'s 64 MiB cap. Verified live 2026-07-11.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import quote

from . import _net

log = logging.getLogger(__name__)

BASE = "https://clinicaltrials.gov/api/v2/studies"
# Legacy-style field names the v2 API accepts; each maps to a protocolSection module we parse below.
_FIELDS = "NCTId,BriefTitle,Phase,OverallStatus,LeadSponsorName,CollaboratorName,StartDate"

# Ordinal so "highest phase reached" is comparable. Higher = later-stage = more de-risked.
PHASE_RANK = {
    "EARLY_PHASE1": 1, "PHASE1": 2, "PHASE1/PHASE2": 3, "PHASE2": 4,
    "PHASE2/PHASE3": 5, "PHASE3": 6, "PHASE4": 7,
}
# Trial STATUS buckets — a raw count conflates a live program with a dead one. Distinguish:
#  - ACTIVE: ongoing (recruiting / enrolling / active). A live program.
#  - COMPLETED: finished. Still positive evidence (the program advanced).
#  - STALLED: stopped early — TERMINATED / WITHDRAWN / SUSPENDED. NOT positive; often a NEGATIVE signal.
#  - anything else (notably UNKNOWN = the sponsor stopped updating past the expected end) = stale/unclear.
ACTIVE_STATUSES = {"RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING",
                   "AVAILABLE", "NOT_YET_RECRUITING"}
COMPLETED_STATUSES = {"COMPLETED"}
STALLED_STATUSES = {"TERMINATED", "WITHDRAWN", "SUSPENDED"}
# "Meaningful" = counts as positive convergence (a live or finished program). Stalled/unknown do NOT —
# a withdrawn Phase 2 must not inflate "highest phase" or admit a name through the pre-filter.
MEANINGFUL_STATUSES = ACTIVE_STATUSES | COMPLETED_STATUSES


def trial_health(status: str | None) -> str:
    """Bucket a raw CT.gov status → 'active' | 'completed' | 'stalled' | 'unknown'."""
    s = (status or "").upper()
    if s in ACTIVE_STATUSES:
        return "active"
    if s in COMPLETED_STATUSES:
        return "completed"
    if s in STALLED_STATUSES:
        return "stalled"
    return "unknown"


def phase_rank(phases: list[str] | None) -> int:
    """Rank of the furthest phase in a study's phase list (0 if unknown/NA)."""
    return max((PHASE_RANK.get((p or "").upper(), 0) for p in (phases or [])), default=0)


def parse_studies(payload: dict) -> list[dict]:
    """v2 ``/studies`` response → flat dicts. The API nests everything under ``protocolSection`` modules;
    this pulls the fields the signal needs and drops the rest."""
    out: list[dict] = []
    for s in (payload or {}).get("studies", []) or []:
        ps = s.get("protocolSection") or {}
        idm = ps.get("identificationModule") or {}
        stm = ps.get("statusModule") or {}
        spm = ps.get("sponsorCollaboratorsModule") or {}
        dm = ps.get("designModule") or {}
        phases = dm.get("phases") or []
        collaborators = [c.get("name") for c in (spm.get("collaborators") or []) if c.get("name")]
        out.append({
            "nct_id": idm.get("nctId"),
            "title": idm.get("briefTitle"),
            "phases": phases,
            "phase_rank": phase_rank(phases),
            "status": stm.get("overallStatus"),
            "lead_sponsor": (spm.get("leadSponsor") or {}).get("name"),
            "collaborators": collaborators,
            "start_date": (stm.get("startDateStruct") or {}).get("date"),
        })
    return out


def search_studies(sponsor: str, *, max_studies: int = 100, page_size: int = 100,
                   limiter: Optional[_net.RateLimiter] = None) -> Optional[list[dict]]:
    """Studies matching ``sponsor`` (query.spons — sponsor OR collaborator), paginated to ``max_studies``.

    Returns ``None`` when the FETCH FAILED (so the caller can retry rather than treat it as "no trials");
    ``[]`` for a genuine no-result. Mirrors the openalex fail-signal contract."""
    if not sponsor or not sponsor.strip():
        return []
    studies: list[dict] = []
    page_token: Optional[str] = None
    while len(studies) < max_studies:
        # Security: host+path are the hard-coded constant BASE (no user-controlled host → no SSRF); the
        # ONLY caller-influenced inputs are query-param VALUES (sponsor, pageToken), fully percent-encoded
        # with safe="" so they can't break out of the param or inject a new one. pageSize is int-coerced.
        # Reads are 64 MiB-capped + retry-with-capped-backoff inside _net. `fields` is a fixed constant.
        url = (f"{BASE}?query.spons={quote(sponsor.strip(), safe='')}&fields={_FIELDS}"
               f"&pageSize={int(min(page_size, max_studies))}&countTotal=false")
        if page_token:
            url += f"&pageToken={quote(page_token, safe='')}"
        payload = _net.safe_json_retry(url, limiter=limiter, accept="application/json")
        if payload is None:                 # fetch failed after retries — signal retry, don't say "none"
            return None if not studies else studies
        studies.extend(parse_studies(payload))
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    return studies[:max_studies]
