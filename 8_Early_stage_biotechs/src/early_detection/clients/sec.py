"""US universe enumeration via SEC EDGAR by SIC code (free, no key).

Adapted from ``platform_discoverer/clients/sec_sic.py`` — the proven approach — but emits Module-8
``Listing`` records with **CIK populated** (the join key M6 dropped) and ``sector_normalized`` mapped
from SIC via the caller's allow-map.

Two public endpoints:
  * ``browse-edgar?action=getcompany&SIC=XXXX&output=atom`` — every filer under a SIC (paginated).
  * ``company_tickers_exchange.json`` — CIK → (ticker, exchange, name) for listed SEC filers.

A CIK with no ticker in that map is not a listed equity → skipped. Parsers are pure + unit-tested on
sample payloads; fetchers are fail-open. SEC needs ``USER_AGENT`` w/ email (``.env``) or it 403s.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, Mapping, Optional

from ..models import Listing
from . import _net

log = logging.getLogger(__name__)

BROWSE_EDGAR = "https://www.sec.gov/cgi-bin/browse-edgar"
TICKERS_EXCHANGE = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

_CIK_RE = re.compile(r"CIK=(\d+)", re.IGNORECASE)


def resolve_via_submissions(cik: int, *, limiter: Optional[_net.RateLimiter] = None) -> Optional[tuple]:
    """(ticker, exchange, name) for a CIK from the submissions feed — the AUTHORITATIVE per-company source.

    Fallback for the ``company_tickers_exchange.json`` gap: that file has only ~9.3k entries and is missing
    real listed filers (e.g. Apellis CIK 1492422), so a SIC-enumerated CIK absent from it would be silently
    dropped. Submissions carries every filer's own ``tickers``/``exchanges`` arrays. Fail-soft (→ None)."""
    payload = _net.safe_json_retry(SUBMISSIONS.format(cik=cik), limiter=limiter)
    if not payload:
        return None
    tickers = payload.get("tickers") or []
    if not tickers:                       # not a listed equity (or FPI without a common ticker)
        return None
    exchanges = payload.get("exchanges") or []
    return tickers[0], (exchanges[0] if exchanges else ""), (payload.get("name") or "")


def _prefer_common(new: str, cur: str) -> bool:
    """True if ``new`` is a better primary ticker than ``cur`` for one CIK.

    A CIK can list common + warrants/units/preferred; the common is reliably the SHORTEST ticker
    (warrants add W/WS, units U, rights R). Ties break lexicographically for determinism.
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
        name: Optional[str] = None
        cik: Optional[int] = None
        for child in el.iter():
            ln = _net.localname(child.tag)
            if ln == "title" and child.text and name is None:
                name = child.text.strip()
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


# EDGAR location codes for Canadian provinces/territories (browse-edgar `State` param). Used by the
# Canada provider to enumerate SEC-filing (FPI) Canadian issuers without SEDAR+ scraping (spec §2.5).
CA_STATE_CODES = ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "B0")


def enumerate_sic(sic: str, *, limiter: Optional[_net.RateLimiter] = None,
                  max_pages: int = 30, count: int = 100, state: Optional[str] = None) -> list[tuple[int, str]]:
    """All (cik, name) filers under a SIC, following pagination. Fail-open (→ []).

    ``state`` (optional) filters by EDGAR location code (a US state or Canadian province) — used to
    isolate Canadian-domiciled filers.
    """
    out: list[tuple[int, str]] = []
    start = 0
    state_q = f"&State={state}" if state else ""
    for _ in range(max_pages):
        url = (f"{BROWSE_EDGAR}?action=getcompany&SIC={sic}{state_q}&type=&dateb=&owner=include"
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


def build_us_listings(sic_map: Mapping[str, str], *, limiter: Optional[_net.RateLimiter] = None,
                      max_pages: int = 30, cik_map: Optional[dict[int, tuple[str, str, str]]] = None,
                      resolve_missing: bool = True,
                      submissions_lookup: Optional[callable] = None) -> list[Listing]:
    """Enumerate the US listed universe across ``sic_map`` (SIC → normalized sector) → ``Listing``s.

    ``cik_map`` may be injected (tests); otherwise fetched from ``company_tickers_exchange.json``. That SEC
    file is INCOMPLETE (~9.3k entries, missing real filers like Apellis) — so when a SIC-enumerated CIK is
    absent from it, ``resolve_missing`` falls back to the authoritative submissions feed
    (``resolve_via_submissions``) to recover the ticker. Without the fallback those companies are silently
    dropped (the latent bug the 13F-holdings audit surfaced). ``submissions_lookup`` injectable for tests.
    """
    if cik_map is None:
        payload = _net.safe_json(TICKERS_EXCHANGE, limiter=limiter)
        cik_map = parse_cik_exchange(payload) if payload else {}
    if not cik_map:
        log.warning("SEC cik↔ticker map unavailable; US enumeration relies on the submissions fallback")
    sub_lookup = submissions_lookup or (lambda cik: resolve_via_submissions(cik, limiter=limiter))

    listings: list[Listing] = []
    seen: set[int] = set()
    recovered = 0
    for sic, sector in sic_map.items():
        for cik, name in enumerate_sic(str(sic), limiter=limiter, max_pages=max_pages):
            if cik in seen:
                continue
            info = cik_map.get(cik)
            if not info and resolve_missing:   # not in the (incomplete) ticker file → submissions fallback
                info = sub_lookup(cik)
                if info:
                    recovered += 1
            if not info:                       # genuinely not a listed common equity → skip
                continue
            seen.add(cik)
            ticker, exchange, exname = info
            listings.append(Listing(
                name=exname or name,
                ticker=ticker or None,
                exchange=exchange or None,
                country="US",
                cik=f"{cik:010d}",
                sic=str(sic),
                sector_normalized=sector,
                filer_type="domestic",
                provenance=["edgar_us"],
            ))
    log.info("SEC: %d US listed listings across %d SIC codes (%d recovered via submissions fallback)",
             len(listings), len(sic_map), recovered)
    return listings


def build_ca_listings(sic_map: Mapping[str, str], *, limiter: Optional[_net.RateLimiter] = None,
                      max_pages: int = 30,
                      cik_map: Optional[dict[int, tuple[str, str, str]]] = None) -> list[Listing]:
    """Canadian FPI issuers (spec §2.2 supplementary) — same SIC set, filtered to Canadian provinces.

    Catches TSX/TSXV names that also file with the SEC (40-F/20-F/6-K FPIs) automatically, WITHOUT
    SEDAR+ scraping (the messy path is deferred, spec §2.5). Marked ``country=CA``, ``filer_type=FPI``.
    Exchange from the cik↔ticker map is typically the US listing venue; the CA listing is added
    separately by the orchestrator if known. Fail-open across province enumerations.
    """
    if cik_map is None:
        payload = _net.safe_json(TICKERS_EXCHANGE, limiter=limiter)
        cik_map = parse_cik_exchange(payload) if payload else {}

    listings: list[Listing] = []
    seen: set[int] = set()
    for sic, sector in sic_map.items():
        for state in CA_STATE_CODES:
            for cik, name in enumerate_sic(str(sic), limiter=limiter, max_pages=max_pages, state=state):
                if cik in seen:
                    continue
                seen.add(cik)
                info = cik_map.get(cik)
                ticker, exchange, exname = info if info else (None, None, None)
                listings.append(Listing(
                    name=exname or name,
                    ticker=ticker or None,
                    exchange=exchange or None,
                    country="CA",
                    cik=f"{cik:010d}",
                    sic=str(sic),
                    sector_normalized=sector,
                    filer_type="FPI",
                    # An FPI that isn't in the listed-ticker map is still a real filer — keep it, but
                    # cap is unknown (no US ticker to price). Missing data → KEEP + flag (repo posture).
                    mktcap_unknown=info is None,
                    provenance=["edgar_canada"],
                ))
    log.info("SEC: %d Canadian FPI listings across %d SIC codes", len(listings), len(sic_map))
    return listings
