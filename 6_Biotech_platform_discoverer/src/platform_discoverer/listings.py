"""Listing providers + identity helpers (spec §6 listings.py, §5.1 Stage 0a inputs).

A ``ListingProvider`` yields ``ListingRecord``s for the in-scope regions. v1 prototype ships:
  * ``SeedCSVProvider`` — offline, deterministic; reads the analyst's tracked-universe CSV(s). This
    is the recall-safe seed net and the backbone of unit tests.
  * ``yfinance_enricher`` — OPTIONAL per-company market-cap/liveness enrichment (network). yfinance
    is fine for a LOCAL prototype but is ToS-limited for public deploy (repo-wide caveat — swap for
    a licensed provider, e.g. FMP/EODHD, before hosting). Fail-open: any error → None → KEEP+flag.

Identity: ``company_id`` is a stable hash(normalized-name | primary-listing) so re-runs are
idempotent and ADR/dual listings collapse to one id (see ``dedup.collapse``).
"""

from __future__ import annotations

import csv
import hashlib
import logging
import re
from pathlib import Path
from typing import Callable, Iterable, Optional

from .models import Company, ListingRecord

log = logging.getLogger(__name__)


# ── identity ────────────────────────────────────────────────────────────────

_NAME_NOISE = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Lowercase, collapse non-alphanumerics — for dedup grouping + stable ids."""
    return _NAME_NOISE.sub(" ", (name or "").lower()).strip()


def primary_listing(rec: ListingRecord) -> str:
    return f"{(rec.exchange or '?')}:{(rec.ticker or '?')}"


def company_id_for(name: str, listing: str) -> str:
    raw = f"{normalize_name(name)}|{listing}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:16]


def record_to_company(rec: ListingRecord, source_nets: list[str]) -> Company:
    """Build a ``Company`` from a canonical record + the Stage-0a nets that matched it."""
    cid = company_id_for(rec.name, primary_listing(rec))
    cap = rec.mktcap_usd_fd
    return Company(
        company_id=cid, name=rec.name, primary_ticker=rec.ticker, exchange=rec.exchange,
        country=rec.country, isin=rec.isin, lei=rec.lei, mktcap_usd_fd=cap,
        mktcap_unknown=(cap is None), source_nets=source_nets, is_live=rec.is_live,
    )


# ── providers ───────────────────────────────────────────────────────────────

class SeedCSVProvider:
    """Reads tracked-universe CSV(s). Every row is tagged provenance ``seed_list``.

    Recognized columns (all optional except name|ticker): name, ticker, exchange, country, sic,
    gics_industry, icb_equiv, indices (``;``-separated), isin, lei, mktcap_usd, currency,
    mktcap_native, shares_fd, is_primary, is_live. Unknown columns are ignored.
    """

    def __init__(self, paths: Iterable[str | Path]):
        self.paths = [Path(p) for p in paths]

    def fetch(self, regions: Optional[Iterable[str]] = None) -> list[ListingRecord]:
        out: list[ListingRecord] = []
        for path in self.paths:
            if not path.exists():
                log.warning("seed list not found, skipping: %s", path)
                continue
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    rec = _row_to_record(row)
                    if rec is not None:
                        out.append(rec)
        return out


def _f(row: dict, key: str) -> Optional[float]:
    v = (row.get(key) or "").strip()
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _b(row: dict, key: str, default: bool) -> bool:
    v = (row.get(key) or "").strip().lower()
    if v in ("1", "true", "yes", "y"):
        return True
    if v in ("0", "false", "no", "n"):
        return False
    return default


def _row_to_record(row: dict) -> Optional[ListingRecord]:
    name = (row.get("name") or "").strip()
    ticker = (row.get("ticker") or "").strip() or None
    if not name and not ticker:
        return None
    indices = [s.strip() for s in (row.get("indices") or "").split(";") if s.strip()]
    return ListingRecord(
        name=name or ticker, ticker=ticker,
        exchange=(row.get("exchange") or "").strip() or None,
        country=(row.get("country") or "").strip() or None,
        sic=(row.get("sic") or "").strip() or None,
        gics_industry=(row.get("gics_industry") or "").strip() or None,
        icb_equiv=(row.get("icb_equiv") or "").strip() or None,
        indices=indices,
        isin=(row.get("isin") or "").strip() or None,
        lei=(row.get("lei") or "").strip() or None,
        mktcap_native=_f(row, "mktcap_native"), currency=(row.get("currency") or "").strip() or None,
        shares_fd=_f(row, "shares_fd"), mktcap_usd_fd=_f(row, "mktcap_usd"),
        is_primary=_b(row, "is_primary", True), is_live=_b(row, "is_live", True),
        provenance=["seed_list"],
    )


# ── optional network enrichment (LOCAL prototype only) ──────────────────────

def yfinance_enricher(config: Optional[dict] = None) -> Callable[[Company], Optional[dict]]:
    """Return an enricher(company) -> {mktcap_usd_fd, is_live} | None using yfinance.

    LOCAL-ONLY (yfinance ToS). Fail-open: import/fetch error → None, so Stage 0b keeps the company
    and flags ``mktcap_unknown`` rather than dropping it. Cap is BASIC (no pre-funded warrants — M2b
    deferral); the native cap is FX-converted to USD. Shares ``clients.market`` with the directory.
    """
    from .clients import market
    from .fx import FXConverter

    fx = FXConverter.from_config(config)

    def enrich(company: Company) -> Optional[dict]:
        if not company.primary_ticker:
            return None
        info = market.fetch_ticker_info(company.primary_ticker)
        if not info:
            return None
        cap = fx.to_usd(info.get("mktcap_native"), info.get("currency"))
        result: dict = {"is_live": info.get("is_live", True)}
        if cap is not None:
            result["mktcap_usd_fd"] = cap
        for key in ("business_description", "sector", "industry"):
            if info.get(key):
                result[key] = info[key]
        return result

    return enrich
