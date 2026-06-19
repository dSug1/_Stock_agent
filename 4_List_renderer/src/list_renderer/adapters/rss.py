"""Generic `rss` adapter (Phase 2) — fetch ANY RSS/Atom feed by config.

Config (source.config_json):
    feed_url   : str                 (required)
    brand      : str   = feed domain  (site_name label)
    max_items  : int   = 10

Covers most news + a lot of "social/forums" (Reddit `/.rss`, Hacker News via
hnrss.org, many subreddits/tags). Shares parsing with the bespoke `news` adapter.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from . import _rss_parse as rp

log = logging.getLogger("4_render_list.rss")


def fetch_results(
    feed_url: str,
    brand: str | None = None,
    max_items: int = 10,
) -> list[dict]:
    if not feed_url:
        raise ValueError("rss adapter requires config.feed_url")
    label = brand or urlsplit(feed_url).netloc or "Feed"
    raw = rp.fetch(feed_url)
    results = rp.parse_feed(raw, label, max_items)
    log.info("  rss %s: %d item(s)", feed_url, len(results))
    return results
