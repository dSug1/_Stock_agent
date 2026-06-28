"""ListingDirectoryProvider — the automated full-universe net (spec §5.1, the M2b build).

Composes free sources into one stream of ``ListingRecord``s, so Stage 0a's `sector`/`name_keyword`
nets fire on the *whole* listed biotech universe instead of a hand-seeded CSV:
  * `sec_us`      — US listed companies enumerated by SIC (SEC EDGAR).
  * `wikidata_eu` — Europe + Nordic (incl. SE/DK) listed biotech/pharma (Wikidata SPARQL).

**Enumeration only — NO market-data enrichment here.** Enrichment (yfinance, one slow call per ticker)
is Stage 0b's job, so the full universe (~900+ companies) is admitted and *persisted* at Stage 0a in
seconds, independent of the slow cap-fetching. (Earlier the provider enriched inline, which blocked
the whole universe behind ~900 yfinance calls — if that stalled, the store stayed empty.) The records
carry Yahoo-resolvable tickers (suffix attached for non-US) so Stage 0b can enrich them directly.

The seed CSV stays a SEPARATE net (`SeedCSVProvider`) — the directory is additive, never a
replacement. All network is fail-open; a dead source yields nothing rather than crashing the run.
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from .clients import _net, sec_sic, wikidata
from .models import ListingRecord

log = logging.getLogger(__name__)

# Yahoo Finance suffix by exchange-label keyword (checked first) then country-label.
_EXCHANGE_SUFFIX = {
    "stockholm": ".ST", "copenhagen": ".CO", "helsinki": ".HE", "oslo": ".OL",
    "paris": ".PA", "euronext paris": ".PA", "amsterdam": ".AS", "brussels": ".BR",
    "lisbon": ".LS", "frankfurt": ".F", "xetra": ".DE", "deutsche": ".DE",
    "six": ".SW", "swiss": ".SW", "zurich": ".SW", "london": ".L",
    "milan": ".MI", "madrid": ".MC", "vienna": ".VI", "dublin": ".IR",
}
_COUNTRY_SUFFIX = {
    "sweden": ".ST", "denmark": ".CO", "finland": ".HE", "norway": ".OL",
    "france": ".PA", "netherlands": ".AS", "belgium": ".BR", "portugal": ".LS",
    "germany": ".DE", "switzerland": ".SW", "united kingdom": ".L", "italy": ".MI",
    "spain": ".MC", "austria": ".VI", "ireland": ".IR",
}


def yahoo_symbol(rec: ListingRecord) -> Optional[str]:
    """Best-effort Yahoo Finance symbol for yfinance enrichment.

    US tickers are used as-is. Non-US tickers get a market suffix from the exchange label (else the
    country label); the raw ticker is normalized (spaces/dots → '-', e.g. 'BIOA B' → 'BIOA-B.ST').
    Returns None if no ticker.
    """
    if not rec.ticker:
        return None
    if (rec.country or "").upper() in ("US", "UNITED STATES", "USA"):
        return rec.ticker
    suffix = ""
    ex = (rec.exchange or "").lower()
    for key, suf in _EXCHANGE_SUFFIX.items():
        if key in ex:
            suffix = suf
            break
    if not suffix:
        suffix = _COUNTRY_SUFFIX.get((rec.country or "").lower(), "")
    if not suffix:                       # unknown market → bare ticker (enrichment may miss it)
        return rec.ticker
    base = rec.ticker.strip().replace(" ", "-").replace(".", "-")
    return f"{base}{suffix}"


class ListingDirectoryProvider:
    """Enumerate the full listed universe (no enrichment). Mirrors ``SeedCSVProvider.fetch``."""

    def __init__(self, config: dict):
        self.config = config
        nets = config.get("stage0a_nets", {}) or {}
        self.dir_cfg = nets.get("directory", {}) or {}
        self.sics = (nets.get("sector_codes", {}) or {}).get("sic", []) or []
        self.sources = self.dir_cfg.get("sources", ["sec_us", "wikidata_eu"])
        self.max_pages = int(self.dir_cfg.get("max_pages_per_sic", 40))
        self._sec_limiter = _net.RateLimiter(float(self.dir_cfg.get("sec_rate_per_sec", 8)))
        self._wd_limiter = _net.RateLimiter(float(self.dir_cfg.get("wikidata_rate_per_sec", 2)))

    def fetch(self, regions: Optional[Iterable[str]] = None) -> list[ListingRecord]:
        regions = list(regions or self.config.get("run", {}).get("regions", []))
        records: list[ListingRecord] = []

        if "sec_us" in self.sources and "US" in regions:
            records += sec_sic.build_us_records(self.sics, limiter=self._sec_limiter,
                                                max_pages=self.max_pages)
        if "wikidata_eu" in self.sources and any(r != "US" for r in regions):
            records += wikidata.build_eu_records(regions, limiter=self._wd_limiter)

        # Attach Yahoo-resolvable symbols (no network) so Stage 0b's enricher can fetch caps.
        for rec in records:
            sym = yahoo_symbol(rec)
            if sym:
                rec.ticker = sym
        log.info("directory: %d records enumerated from %s (enrichment deferred to Stage 0b)",
                 len(records), self.sources)
        return records
