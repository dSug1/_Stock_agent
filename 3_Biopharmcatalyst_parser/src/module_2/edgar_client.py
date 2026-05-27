"""EDGAR HTTP client — rate-limited GET with User-Agent compliance.

Per spec §1.7 architectural decision (D1/§4.3.4), this is a *copy-paste
adaptation* of `2_Funds_parser/src/{layer_1/edgar_13f.py,
module_4c/edgar_client.py}` — same rate limiter shape, same Form 4
filename-discovery dance, same fail-open semantics. Promotion to a
shared `layer_1/edgar_form4.py` is deferred (touching 2_Funds_parser
while it's in production is risky).

Rate limit is 9.5 req/sec — matches 2_Funds_parser's setting and stays
safely under SEC's 10 req/sec fair-use cap. Spec §4.3.2 originally said
5/sec; bumped to 9.5 for consistency across projects (see D5 update).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Iterable, Optional

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)


# .env at the repo root carries USER_AGENT for SEC compliance.
# Load lazily so import-time has no side effects beyond reading the file.
load_dotenv()
EDGAR_USER_AGENT = os.getenv("USER_AGENT", "").strip()
if not EDGAR_USER_AGENT:
    # Fallback only — SEC requires a real contact; raise loudly when we try
    # to actually make a request, not at import.
    EDGAR_USER_AGENT = ""

EDGAR_RATE_LIMIT_PER_SEC = float(os.getenv("EDGAR_RATE_LIMIT_PER_SEC", "9.5"))

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


# --- rate limiter ---------------------------------------------------------

class _RateLimiter:
    """Process-global token bucket gating every thread through one lock so
    aggregate request rate never exceeds ``rate_per_sec``. Default 9.5/sec
    matches 2_Funds_parser; SEC's published fair-use cap is 10/sec."""

    def __init__(self, rate_per_sec: float) -> None:
        self._interval = 1.0 / rate_per_sec
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait <= 0:
                self._next = now + self._interval
                wait = 0.0
            else:
                self._next += self._interval
        if wait > 0:
            time.sleep(wait)


_EDGAR_LIMITER = _RateLimiter(EDGAR_RATE_LIMIT_PER_SEC)


# --- shared session -------------------------------------------------------

_SESSION = requests.Session()
_ADAPTER = HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=0)
_SESSION.mount("https://", _ADAPTER)
_SESSION.mount("http://",  _ADAPTER)


def _ua() -> str:
    if not EDGAR_USER_AGENT:
        raise RuntimeError(
            "USER_AGENT is not set in .env — SEC requires a contact User-Agent "
            "(e.g., 'Name email@example.com'). Edit the .env at the repo root."
        )
    return EDGAR_USER_AGENT


# --- HTTP error type ------------------------------------------------------

class HttpError(Exception):
    """Raised for non-2xx responses. Carries HTTP code for caller forks."""
    def __init__(self, code: int, reason: str, url: str) -> None:
        super().__init__(f"HTTP {code} {reason} — {url}")
        self.code = code
        self.reason = reason
        self.url = url


# --- low-level GET with retry --------------------------------------------

def http_get(
    url: str,
    *,
    accept: str = "application/json",
    max_retries: int = 3,
) -> bytes:
    """Rate-limited GET. Retries 429 + 5xx with exponential backoff.
    Hard-fails on 403 (almost always means missing User-Agent)."""
    _EDGAR_LIMITER.acquire()
    headers = {"User-Agent": _ua(), "Accept": accept}
    attempt = 0
    last_err: Optional[Exception] = None
    while attempt <= max_retries:
        try:
            resp = _SESSION.get(url, headers=headers, timeout=30)
        except (requests.ConnectionError, requests.Timeout) as e:
            last_err = e
            wait = 2 ** attempt
            log.warning("network error on %s (attempt %d/%d): %s — backing off %ds",
                        url, attempt + 1, max_retries + 1, e, wait)
            time.sleep(wait)
            attempt += 1
            continue
        if resp.status_code == 403:
            raise HttpError(403, "Forbidden (User-Agent likely missing/invalid)", url)
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            if attempt >= max_retries:
                raise HttpError(resp.status_code, resp.reason or "", url)
            wait = 2 ** attempt
            log.warning("HTTP %d on %s (attempt %d/%d) — backing off %ds",
                        resp.status_code, url, attempt + 1, max_retries + 1, wait)
            time.sleep(wait)
            attempt += 1
            continue
        if not (200 <= resp.status_code < 300):
            raise HttpError(resp.status_code, resp.reason or "", url)
        return resp.content
    raise HttpError(0, f"network failure: {last_err}", url)


# --- submissions index ----------------------------------------------------

@dataclass
class FilingsResult:
    status: str                    # 'ok' | 'failed'
    error: Optional[str]
    filings: list[dict]            # accession_number, form, filing_date, primary_doc


def fetch_submissions(cik: str) -> dict:
    """Return the parsed submissions.json payload for a CIK."""
    url = SUBMISSIONS_URL.format(cik=str(cik).zfill(10))
    body = http_get(url, accept="application/json")
    return json.loads(body)


def list_form_filings(
    cik: str, *, forms: Iterable[str], since_date_iso: str,
) -> FilingsResult:
    """Walk submissions.filings.recent and return filings whose `form` is in
    `forms` and whose `filingDate >= since_date_iso`. Spec §4.3.3 notes
    that paginated older filings are out of scope for v1."""
    try:
        payload = fetch_submissions(cik)
    except HttpError as e:
        return FilingsResult(status="failed", error=str(e), filings=[])
    except Exception as e:  # noqa: BLE001
        return FilingsResult(status="failed", error=f"{type(e).__name__}: {e}", filings=[])

    recent = payload.get("filings", {}).get("recent", {})
    n = len(recent.get("accessionNumber", []) or [])
    forms_set = {f.upper() for f in forms}
    out: list[dict] = []
    for i in range(n):
        form = (recent.get("form") or [None] * n)[i]
        date = (recent.get("filingDate") or [None] * n)[i]
        if not form or not date:
            continue
        if form.upper() not in forms_set:
            continue
        if date < since_date_iso:
            continue
        out.append({
            "accession_number": (recent.get("accessionNumber") or [None] * n)[i],
            "form": form,
            "filing_date": date,
            "primary_doc": (recent.get("primaryDocument") or [None] * n)[i],
        })
    return FilingsResult(status="ok", error=None, filings=out)


# --- Form 4 XML retrieval -------------------------------------------------

def _list_accession_files(cik: str, accn_raw: str) -> list[str]:
    """Fetch the EDGAR `index.json` directory listing for an accession.
    One cheap (~5 KB) call. Returns [] on any error (caller falls back)."""
    url = f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}/index.json"
    try:
        body = http_get(url, accept="application/json")
    except Exception:  # noqa: BLE001
        return []
    try:
        obj = json.loads(body)
    except json.JSONDecodeError:
        return []
    return [
        (it.get("name") or "").strip()
        for it in obj.get("directory", {}).get("item", [])
        if it.get("name")
    ]


def _pick_form4_xml_name(files: list[str]) -> Optional[str]:
    """Choose the Form 4 ownership XML from a directory listing."""
    xmls = [
        f for f in files
        if f.lower().endswith(".xml") and "index" not in f.lower()
    ]
    if not xmls:
        return None
    for preferred in ("primary_doc.xml", "ownership.xml"):
        if preferred in xmls:
            return preferred
    for f in xmls:
        if f.lower().startswith("wf-form4_"):
            return f
    return xmls[0]


def fetch_form4_xml(
    cik: str, accession_number: str, primary_doc: Optional[str],
) -> bytes:
    """Fetch one Form 4 filing's primary XML, with the basename-first
    fallback to the index.json directory listing. Raises HttpError if
    every candidate URL 404s."""
    accn_raw = accession_number.replace("-", "")
    archive_root = f"{ARCHIVES_BASE}/{int(cik)}/{accn_raw}"
    tried: list[str] = []

    # Step 1: optimistic basename try (modern filings put the XSL at
    # xslF345X05/<name> and the raw XML at <name>).
    if primary_doc and primary_doc.lower().endswith(".xml"):
        basename = primary_doc.rsplit("/", 1)[-1]
        url = f"{archive_root}/{basename}"
        tried.append(url)
        try:
            return http_get(url, accept="application/xml,text/xml,*/*")
        except HttpError as e:
            if e.code != 404:
                raise

    # Step 2: directory discovery.
    files = _list_accession_files(cik, accn_raw)
    name = _pick_form4_xml_name(files)
    if name:
        url = f"{archive_root}/{name}"
        tried.append(url)
        return http_get(url, accept="application/xml,text/xml,*/*")

    raise HttpError(404, f"no Form 4 XML found (tried: {tried})", archive_root)
