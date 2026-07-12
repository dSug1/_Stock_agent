"""13F-holdings seed provider (spec §2.4-adjacent) — mirrors the M6 seed (decision D2 pattern).

Opens ``2_Funds_parser/2_fundparser.db`` **READ-ONLY** (`mode=ro`) and emits a ``Listing`` per name the
tracked specialist biotech funds hold — closing a real coverage gap: the EDGAR SIC enumeration is
page-capped, so some small/mid US therapeutics that top specialists (Baker Bros / OrbiMed / RA Capital /
Perceptive …) own weren't in the universe (Apellis, Celcuity, Terns, Day One, Arcellx, …). A name a
specialist fund holds is thesis-relevant by construction, so it belongs in the universe. Module 8 never
writes to 2_Funds_parser's store.

Two disciplines make this clean:
  * **Exclude broad/mis-scoped filers** — a filer holding more than ``max_holdings_per_filer`` distinct
    names in the latest quarter is a diversified manager, not a biotech specialist (e.g. the mis-mapped
    "Janus Henderson Group PLC" whole-company 13F with ~2,300 mostly-non-biotech positions). Its
    holdings would pollute the universe with utilities/insurers, so it's dropped by this generic rule.
  * **Authoritative biotech filter by SIC** — each held ticker is resolved to a CIK (SEC cik↔ticker map)
    and admitted ONLY if its SEC SIC is in the module's biotech allow-set. This catches biotech names a
    keyword filter misses (Celcuity, Arcellx) and cleanly excludes the funds' non-biotech positions.

Fail-soft throughout (missing DB / unresolved ticker / SIC fetch failure → skip, never abort).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable, Mapping, Optional

from ..clients import _net, sec
from ..models import Listing

log = logging.getLogger(__name__)

_LIMITER = _net.RateLimiter(per_sec=8.0)   # SEC fair-access
SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


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


def _sic_of_cik(cik: int, *, limiter: Optional[_net.RateLimiter] = None) -> Optional[str]:
    """The SEC SIC code for a CIK (from the submissions feed), or None. Fail-soft."""
    payload = _net.safe_json_retry(SUBMISSIONS.format(cik=cik), limiter=limiter)
    if not payload:
        return None
    sic = payload.get("sic")
    return str(sic).strip() if sic else None


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


def load_fund13f_listings(fund_db_path: Path | str, sic_allow: Mapping[str, str], *,
                          cik_map: Optional[dict] = None,
                          sic_lookup: Optional[Callable[[int], Optional[str]]] = None,
                          limiter: Optional[_net.RateLimiter] = None,
                          max_holdings_per_filer: int = 500) -> list[Listing]:
    """Specialist-fund-held US biotechs → ``Listing`` records (SIC-filtered). ``cik_map``/``sic_lookup``
    injectable for tests. Fail-open (→ [])."""
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
    if cik_map is None:
        payload = _net.safe_json(sec.TICKERS_EXCHANGE, limiter=lim)
        cik_map = sec.parse_cik_exchange(payload) if payload else {}
    # invert {cik: (ticker, exchange, name)} → {UPPER(ticker): (cik, exchange, name)}
    tk2cik = {info[0].upper(): (cik, info[1], info[2]) for cik, info in cik_map.items() if info and info[0]}
    sic_lookup = sic_lookup or (lambda cik: _sic_of_cik(cik, limiter=lim))

    listings: list[Listing] = []
    resolved = admitted = 0
    for tk, name in held.items():
        info = tk2cik.get(tk)
        if not info:                    # not a listed common equity in the SEC map (warrants/units, etc.)
            continue
        cik, exchange, exname = info
        resolved += 1
        sic = sic_lookup(cik)
        sector = sic_allow.get(sic) if sic else None
        if sector is None:              # not a biotech-SIC filer → not thesis-relevant
            continue
        admitted += 1
        listings.append(Listing(
            name=exname or name,
            ticker=tk,
            exchange=exchange or None,
            country="US",
            cik=f"{cik:010d}",
            sic=sic,
            sector_normalized=sector,
            filer_type="domestic",
            provenance=["fund13f"],
        ))
    log.info("fund13f: %d held tickers → %d resolved → %d biotech-SIC admitted", len(held), resolved, admitted)
    return listings
