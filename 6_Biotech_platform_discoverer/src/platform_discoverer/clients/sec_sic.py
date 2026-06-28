"""US universe enumeration via SEC EDGAR by SIC code (free, no key).

Two endpoints, both public:
  * `browse-edgar?action=getcompany&SIC=XXXX&output=atom` — lists every filer under a SIC code
    (paginated by `start`). We parse company name + CIK from each entry.
  * `company_tickers_exchange.json` — maps CIK → (ticker, exchange) for SEC filers that are listed.

A CIK with no ticker in that map is not a listed equity → skipped (this is a *listed*-biotech screen).
The SIC list is the broad sector set from config (biotech 2836, pharma 2834, research 8731,
instruments 3826/3829), so a company mis-coded as Tools/Instruments is still caught.

The parsers (`parse_browse_atom`, `parse_cik_exchange`) are pure and unit-tested with sample payloads;
the fetchers are fail-open. Live schema may vary slightly — validate on the first real run.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

from ..models import ListingRecord
from . import _net

log = logging.getLogger(__name__)

BROWSE_EDGAR = "https://www.sec.gov/cgi-bin/browse-edgar"
TICKERS_EXCHANGE = "https://www.sec.gov/files/company_tickers_exchange.json"

_CIK_RE = re.compile(r"CIK=(\d+)", re.IGNORECASE)


def _prefer_common(new: str, cur: str) -> bool:
    """True if ``new`` is a better primary ticker than ``cur`` for the same CIK.

    A company can list several securities (common + warrants/units/preferred) under one CIK; the
    exchange file gives each its own row, so naive last-wins lets a warrant clobber the common (e.g.
    Ginkgo's ``DNABW`` overwriting ``DNA``). The common is reliably the SHORTEST ticker (warrants add
    ``W``/``WS``, units ``U``, rights ``R``, preferred a suffix); ties break lexicographically for
    determinism.
    """
    if len(new) != len(cur):
        return len(new) < len(cur)
    return new < cur


def parse_cik_exchange(payload: dict) -> dict[int, tuple[str, str, str]]:
    """company_tickers_exchange.json → {cik: (ticker, exchange, name)}, common ticker preferred."""
    fields = payload.get("fields", [])
    idx = {f: i for i, f in enumerate(fields)}
    ci, ti = idx.get("cik"), idx.get("ticker")
    ei, ni = idx.get("exchange"), idx.get("name")
    out: dict[int, tuple[str, str, str]] = {}
    for row in payload.get("data", []):
        try:
            cik = int(row[ci])
        except (TypeError, ValueError, IndexError):
            continue
        ticker = row[ti] if ti is not None and ti < len(row) else None
        if not ticker:
            continue
        exchange = row[ei] if ei is not None and ei < len(row) else None
        name = row[ni] if ni is not None and ni < len(row) else None
        existing = out.get(cik)
        if existing is None or _prefer_common(ticker, existing[0]):
            out[cik] = (ticker, exchange or "", name or "")
    return out


def parse_browse_atom(root) -> list[tuple[int, str]]:
    """browse-edgar atom feed → [(cik, name)]. Namespace-agnostic and defensive."""
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for el in root.iter():
        if _net.localname(el.tag) != "entry":
            continue
        name = None
        cik: Optional[int] = None
        for child in el.iter():
            ln = _net.localname(child.tag)
            if ln == "title" and child.text and name is None:
                name = child.text.strip()
            # CIK can appear in a link href, an <id>, or a <cik> element
            for val in list(child.attrib.values()) + ([child.text] if child.text else []):
                m = _CIK_RE.search(val or "")
                if m:
                    cik = int(m.group(1))
                    break
            if cik is None and ln == "cik" and child.text and child.text.strip().isdigit():
                cik = int(child.text.strip())
        if cik is not None and cik not in seen:
            seen.add(cik)
            out.append((cik, name or ""))
    return out


def enumerate_sic(sic: str, *, limiter: Optional[_net.RateLimiter] = None,
                  max_pages: int = 30, count: int = 100) -> list[tuple[int, str]]:
    """All (cik, name) filers under a SIC, following pagination. Fail-open (→ [])."""
    out: list[tuple[int, str]] = []
    start = 0
    for _ in range(max_pages):
        url = (f"{BROWSE_EDGAR}?action=getcompany&SIC={sic}&type=&dateb=&owner=include"
               f"&count={count}&start={start}&output=atom")
        root = _net.safe_xml(url, limiter=limiter)
        if root is None:
            break
        page = parse_browse_atom(root)
        if not page:
            break
        out.extend(page)
        if len(page) < count:
            break
        start += count
    return out


def build_us_records(sics: Iterable[str], *, limiter: Optional[_net.RateLimiter] = None,
                     max_pages: int = 30) -> list[ListingRecord]:
    """Enumerate the US listed universe across the SIC set → ListingRecords (sector net)."""
    sics = [str(s) for s in sics]
    payload = _net.safe_json(TICKERS_EXCHANGE, limiter=limiter)
    cik_map = parse_cik_exchange(payload) if payload else {}
    if not cik_map:
        log.warning("SEC cik↔ticker map unavailable; US enumeration will be empty this run")

    records: list[ListingRecord] = []
    seen: set[int] = set()
    for sic in sics:
        for cik, name in enumerate_sic(sic, limiter=limiter, max_pages=max_pages):
            if cik in seen:
                continue
            info = cik_map.get(cik)
            if not info:                      # not a listed equity → skip
                continue
            seen.add(cik)
            ticker, exchange, exname = info
            # The multi-result browse-edgar atom serializes names as the SEC "ARRAY(0x..)" bug, so
            # take the clean name from the cik↔ticker map; fall back to the atom name only if absent.
            clean = exname or name
            records.append(ListingRecord(
                name=clean, ticker=ticker, exchange=exchange or None, country="US",
                sic=str(sic), provenance=["sector"]))
    log.info("SEC: %d US listed records across %d SIC codes", len(records), len(sics))
    return records
