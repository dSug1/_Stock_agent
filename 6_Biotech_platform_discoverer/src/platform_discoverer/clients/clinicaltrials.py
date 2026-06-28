"""ClinicalTrials.gov v2 evidence — the translational signal (spec §6, free, no key).

Summarizes a company's trials by lead sponsor: count, phases, top conditions, and whether the records
carry **biomarker / companion-diagnostic** language (the Acrivon `E_translation` tell). Global and
English, so it works even where foreign filings are opaque.

Pure parser (`parse_studies`) is unit-tested; the fetch is fail-open.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Optional

from . import _net

log = logging.getLogger(__name__)

ENDPOINT = "https://clinicaltrials.gov/api/v2/studies"
_FIELDS = ",".join([
    "protocolSection.identificationModule",
    "protocolSection.sponsorCollaboratorsModule",
    "protocolSection.statusModule",
    "protocolSection.designModule",
    "protocolSection.conditionsModule",
    "protocolSection.descriptionModule",
    "protocolSection.eligibilityModule",
])


# FDA / regulatory designation phrases — high-signal regulatory recognitions (spec §0, §9 E-axis).
_FDA_DESIGNATIONS = {
    "breakthrough therapy": "Breakthrough Therapy",
    "breakthrough device": "Breakthrough Device",
    "fast track": "Fast Track",
    "orphan drug": "Orphan Drug",
    "regenerative medicine advanced therapy": "RMAT",
    "rmat designation": "RMAT",
    "prime designation": "PRIME (EMA)",
    "priority review": "Priority Review",
    "accelerated approval": "Accelerated Approval",
    "rare pediatric disease": "Rare Pediatric Disease",
}


def scan_designations(text: Optional[str]) -> list[str]:
    """Extract FDA/regulatory designation mentions from free text (descriptions, protocol blobs)."""
    if not text:
        return []
    low = text.lower()
    return sorted({label for phrase, label in _FDA_DESIGNATIONS.items() if phrase in low})


def parse_studies(payload: dict) -> dict:
    studies = payload.get("studies") or []
    phases: set[str] = set()
    conditions: set[str] = set()
    designations: set[str] = set()
    latest: Optional[str] = None
    biomarker = False
    for s in studies:
        ps = s.get("protocolSection") or {}
        for ph in ((ps.get("designModule") or {}).get("phases") or []):
            phases.add(ph)
        for cond in ((ps.get("conditionsModule") or {}).get("conditions") or [])[:50]:
            conditions.add(cond)
        d = ((ps.get("statusModule") or {}).get("lastUpdatePostDateStruct") or {}).get("date")
        if d and (latest is None or d > latest):
            latest = d
        blob = json.dumps(ps).lower()
        if "companion diagnostic" in blob or "biomarker" in blob:
            biomarker = True
        designations.update(scan_designations(blob))
    return {
        "trial_count": payload.get("totalCount", len(studies)),
        "phases": sorted(phases),
        "top_conditions": sorted(conditions)[:12],
        "biomarker_or_cdx_language": biomarker,
        "fda_designations": sorted(designations),
        "latest_update": latest,
    }


def fetch(company_name: str, *,
          limiter: Optional[_net.RateLimiter] = None) -> Optional[tuple[dict, str]]:
    """Return (summary, cursor) or None. cursor = latest lastUpdatePostDate across the sponsor's trials."""
    params = {"query.spons": _net.clean_name(company_name), "pageSize": "100",
              "countTotal": "true", "fields": _FIELDS}
    payload = _net.safe_json(f"{ENDPOINT}?{urllib.parse.urlencode(params)}", limiter=limiter)
    if not payload:
        return None
    summary = parse_studies(payload)
    if not summary.get("trial_count"):
        return None
    cursor = summary.get("latest_update") or str(summary["trial_count"])
    return summary, cursor
