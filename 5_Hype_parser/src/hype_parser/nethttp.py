"""Shared HTTP read cap — security hardening (audit S10 / handoff §4(a)).

Bounds memory against a hostile/oversized response body: read one byte past the cap and reject if
exceeded, so no single response can OOM the process. The 5_Hype fetch hosts are all hardcoded public
APIs (no user-supplied URLs ⇒ no SSRF), so this is purely a size guard. The discovery getter
(`discovery/parsers.py`) already enforces the same cap inline (D36); this helper is for the Wave-1..4
ingest clients + the OD-2 archive. Callers stay fail-open (the raised ValueError degrades to empty).
"""

MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def capped_read(resp, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read an HTTP response body, rejecting anything larger than ``max_bytes``."""
    data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"response exceeds {max_bytes}-byte cap")
    return data
