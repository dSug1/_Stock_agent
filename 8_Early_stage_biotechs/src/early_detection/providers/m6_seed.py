"""Module-6 store reader — the existing-universe priority tier (decision D2, spec §2.4).

Opens ``6_Biotech_platform_discoverer/data/store.db`` **READ-ONLY** (`mode=ro` URI) and emits a
``Listing`` per live company, each tagged ``in_existing_universe=True`` so the reconciled entity lands
in the §2.4 priority tier. Module 8 never writes to M6's store — M6's cardinal-rule guarantee stays
intact.

Fail-soft: a missing/locked M6 store logs a warning and yields [] (Module 8 still builds from EDGAR).

Sector note: M6 has no normalized sector column; it carries ``ta_tags`` (mechanism vocab) + sector/
industry text. We do NOT try to map those to Module-8's normalized taxonomy here — an EDGAR listing
for the same company will supply the SIC-derived sector on merge. M6 seeds identity + the priority
flag; EDGAR supplies sector/CIK. That division keeps each provider authoritative for what it knows.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from ..models import Listing

log = logging.getLogger(__name__)


def _connect_ro(path: Path) -> Optional[sqlite3.Connection]:
    if not path.is_file():
        log.warning("m6_seed: M6 store not found at %s — skipping priority-tier seed", path)
        return None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:  # noqa: BLE001 — fail-open
        log.warning("m6_seed: cannot open M6 store %s read-only: %s", path, exc)
        return None


def load_m6_listings(store_path: Path | str) -> list[Listing]:
    """Read live M6 companies → priority-tier ``Listing`` records. Fail-open (→ [])."""
    path = Path(store_path)
    conn = _connect_ro(path)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT name, primary_ticker, exchange, country, isin, lei, mktcap_usd_fd, "
            "mktcap_unknown FROM companies WHERE is_live=1"
        ).fetchall()
    except sqlite3.Error as exc:  # noqa: BLE001 — schema drift / fail-open
        log.warning("m6_seed: query failed on %s: %s", path, exc)
        conn.close()
        return []
    conn.close()

    listings: list[Listing] = []
    for r in rows:
        cap = r["mktcap_usd_fd"]
        listings.append(Listing(
            name=r["name"],
            ticker=r["primary_ticker"],
            exchange=r["exchange"],
            country=_iso_country(r["country"]),
            isin=r["isin"],
            lei=r["lei"],
            mktcap_usd=cap,
            mktcap_unknown=bool(r["mktcap_unknown"]) or cap is None,
            in_existing_universe=True,
            is_primary=True,
            provenance=["m6"],
        ))
    log.info("m6_seed: %d live companies read from M6 store", len(listings))
    return listings


# M6 stores free-text country labels ("US", "Japan", "United Kingdom", …); normalize the common ones
# to the ISO-ish codes Module 8 uses elsewhere so downstream jurisdiction filters line up. Unknown
# labels pass through unchanged (better a stray label than a wrong-but-tidy one).
_COUNTRY_MAP = {
    "us": "US", "united states": "US", "usa": "US",
    "canada": "CA", "united kingdom": "UK", "uk": "UK",
    "japan": "JP", "south korea": "KR", "korea": "KR",
    "denmark": "DK", "sweden": "SE", "switzerland": "CH", "germany": "DE",
    "france": "FR", "belgium": "BE", "ireland": "IE", "spain": "ES", "netherlands": "NL",
}


def _iso_country(label: Optional[str]) -> Optional[str]:
    if not label:
        return None
    return _COUNTRY_MAP.get(label.strip().lower(), label)
