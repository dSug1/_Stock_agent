"""Bespoke `news` adapter — 3 articles each from FierceBiotech / Le Figaro /
CNBC, in that order, with per-site feed fallbacks.

Parsing is shared with the generic `rss` adapter via `_rss_parse`. (This source
could equally be expressed as three `rss` sources in config; it is kept as a
single curated demo source.)
"""

from __future__ import annotations

import logging

from list_renderer.render import build_payload
from . import _rss_parse as rp

log = logging.getLogger("4_render_list.news")

# Order matters: the requested site order is preserved in the output.
SITES = [
    {"brand": "FierceBiotech", "feeds": ["https://www.fiercebiotech.com/rss/xml"]},
    {"brand": "Le Figaro", "feeds": [
        "https://www.lefigaro.fr/rss/figaro_actualites.xml",
        "https://www.lefigaro.fr/rss/figaro_flash-actu.xml",
    ]},
    {"brand": "CNBC", "feeds": ["https://www.cnbc.com/id/100003114/device/rss/rss.html"]},
]


def _fetch_site(site: dict, per_site: int) -> list[dict]:
    for feed in site["feeds"]:
        try:
            results = rp.parse_feed(rp.fetch(feed), site["brand"], per_site)
        except Exception as exc:  # network or parse failure -> try next feed
            log.warning("  %s: %s failed (%s)", site["brand"], feed, exc)
            continue
        if results:
            log.info("  %s: %d article(s) from %s", site["brand"], len(results), feed)
            return results
    log.warning("  %s: no articles fetched", site["brand"])
    return []


def fetch_results(per_site: int = 3) -> list[dict]:
    """Flat list of Highlight dicts across all sites, in order (M8 orchestrator)."""
    results: list[dict] = []
    for site in SITES:
        results.extend(_fetch_site(site, per_site))
    return results


def build_payload_from_news(per_site: int = 3) -> dict:
    """Standalone payload (v0 `--source news` path)."""
    return build_payload(
        query="fiercebiotech, le figaro, cnbc",
        brand="News",
        results=fetch_results(per_site),
    )
