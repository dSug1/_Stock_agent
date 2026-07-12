"""13F-holdings seed provider (spec §2.4-adjacent) — mirrors the M6 seed (decision D2 pattern).

Opens ``2_Funds_parser/2_fundparser.db`` **READ-ONLY** (`mode=ro`) and emits a ``Listing`` per name the
tracked specialist biotech funds hold — closing a real coverage gap: the EDGAR SIC enumeration is
page-capped AND SEC's ``company_tickers.json`` is incomplete (missing e.g. Apellis/Terns/Day One), so some
small/mid US therapeutics that top specialists (Baker Bros / OrbiMed / RA Capital / Perceptive …) own
weren't in the universe. A name a specialist fund holds is thesis-relevant by construction.

Resolution: each held ticker is looked up via EDGAR ``browse-edgar?action=getcompany&ticker=…`` (one call,
returns the authoritative **CIK + SIC + name** — and it covers ALL listed filers, unlike the incomplete
company_tickers files). Admit only if the SIC is in the biotech-adjacent allow-set (therapeutics /
diagnostics / tools / devices) — SIC (not a name keyword) catches keyword-less biotechs (Celcuity=8071,
Arcellx) and cleanly rejects the funds' utility/insurer/ADR positions. CIK-keyed → union-find merges a
held name with its EDGAR/M6 twin; a genuine gap becomes a NEW entity (mktcap_unknown until enriched).

Disciplines: exclude broad/mis-scoped filers (> ``max_holdings_per_filer`` distinct names = a diversified
manager, e.g. the mis-mapped whole-company "Janus Henderson Group PLC" 13F). Fail-soft throughout.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from ..clients import _net
from ..models import Listing

log = logging.getLogger(__name__)

_LIMITER = _net.RateLimiter(per_sec=8.0)   # SEC fair-access
GETCOMPANY = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&ticker={t}"
              "&type=&dateb=&owner=include&count=1&output=atom")
_RE_CIK = re.compile(r"<cik>(\d+)</cik>", re.I)
_RE_SIC = re.compile(r"<assigned-sic>(\d+)</assigned-sic>", re.I)
_RE_NAME = re.compile(r"<conformed-name>([^<]+)</conformed-name>", re.I)

# Biotech-adjacent SIC → normalized sector. Broader than the EDGAR-enumeration set on purpose: the 13F
# holdings are already pre-filtered to specialist-fund conviction, so we admit the full drug/dx/tools/device
# span (and SEC classifies some real biotechs oddly — Celcuity is 8071 "medical labs", not a 283x).
_SIC_SECTOR = {
    "2833": "therapeutics", "2834": "therapeutics", "2836": "therapeutics",
    "2835": "diagnostics", "8071": "diagnostics",
    "8731": "tools_platform", "3826": "tools_platform", "3827": "tools_platform",
    "3841": "devices", "3845": "devices",
}


def _connect_ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.is_file():
        log.warning("fund13f: 2_Funds store not found at %s — skipping 13F seed", path)
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:  # noqa: BLE001 — fail-open
        log.warning("fund13f: cannot open 2_Funds store %s read-only: %s", path, exc)
        return None


def _lookup_ticker(ticker: str, *, limiter: Optional[_net.RateLimiter] = None) -> Optional[tuple]:
    """EDGAR getcompany by ticker → (cik:int, sic:str, name:str), or None. Fail-soft. Works for ALL
    listed filers (SEC's company_tickers.json is incomplete for some biotechs)."""
    if limiter is not None:
        limiter.wait()
    try:
        body = _net.get_bytes(GETCOMPANY.format(t=quote(ticker)), accept="application/atom+xml").decode(
            "utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — fail-soft per ticker (unknown ticker → 404, etc.)
        log.debug("fund13f: getcompany failed for %s: %s", ticker, exc)
        return None
    m_cik, m_sic = _RE_CIK.search(body), _RE_SIC.search(body)
    if not m_cik:
        return None
    m_name = _RE_NAME.search(body)
    return int(m_cik.group(1)), (m_sic.group(1) if m_sic else None), (m_name.group(1) if m_name else "")


def _held_tickers(conn: sqlite3.Connection, max_holdings_per_filer: int) -> dict[str, str]:
    """{UPPER(ticker): name_of_issuer} for the latest-quarter EQUITY holdings, excluding broad filers."""
    latest = conn.execute("SELECT MAX(period_of_report) FROM holdings").fetchone()[0]
    if not latest:
        return {}
    broad = [r[0] for r in conn.execute(
        "SELECT fund_id FROM holdings WHERE period_of_report=? AND ticker!='' "
        "GROUP BY fund_id HAVING COUNT(DISTINCT ticker) > ?", (latest, max_holdings_per_filer))]
    excl = (" AND fund_id NOT IN (%s)" % ",".join("?" * len(broad))) if broad else ""
    rows = conn.execute(
        f"SELECT UPPER(ticker), MAX(name_of_issuer) FROM holdings WHERE period_of_report=? "
        f"AND ticker IS NOT NULL AND ticker!='' AND (put_call IS NULL OR put_call=''){excl} "
        f"GROUP BY UPPER(ticker)", (latest, *broad)).fetchall()
    if broad:
        log.info("fund13f: excluded %d broad filer(s) (> %d holdings)", len(broad), max_holdings_per_filer)
    return {r[0]: (r[1] or "") for r in rows}


def load_fund13f_listings(fund_db_path: Path | str, *, lookup: Optional[Callable[[str], Optional[tuple]]] = None,
                          limiter: Optional[_net.RateLimiter] = None,
                          max_holdings_per_filer: int = 500) -> list[Listing]:
    """Specialist-fund-held US biotechs → ``Listing`` records (EDGAR-resolved, SIC-filtered). ``lookup``
    (ticker → (cik, sic, name)) injectable for tests. Fail-open (→ [])."""
    conn = _connect_ro(Path(fund_db_path))
    if conn is None:
        return []
    try:
        held = _held_tickers(conn, max_holdings_per_filer)
    except sqlite3.Error as exc:  # noqa: BLE001 — schema drift / fail-open
        log.warning("fund13f: holdings query failed: %s", exc)
        conn.close()
        return []
    conn.close()
    if not held:
        return []

    lim = limiter or _LIMITER
    lookup = lookup or (lambda tk: _lookup_ticker(tk, limiter=lim))

    listings: list[Listing] = []
    resolved = 0
    for tk, name in held.items():
        info = lookup(tk)
        if not info or not info[0]:
            continue                        # unresolved (warrant/unit/delisted) → skip
        resolved += 1
        cik, sic, exname = info
        sector = _SIC_SECTOR.get(sic)
        if sector is None:                  # not a biotech-adjacent SIC → not thesis-relevant
            continue
        listings.append(Listing(
            name=exname or name,
            ticker=tk,
            country="US",
            cik=f"{cik:010d}",
            sic=sic,
            sector_normalized=sector,
            filer_type="domestic",
            provenance=["fund13f"],
        ))
    log.info("fund13f: %d held tickers → %d resolved → %d biotech-SIC admitted",
             len(held), resolved, len(listings))
    return listings
