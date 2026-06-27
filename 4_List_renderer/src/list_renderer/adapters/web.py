"""Generic `web` adapter (Phase 3) — scrape a page using a recipe's CSS selectors.

The selectors come from a Claude-resolved recipe (extract_spec); rendering just
replays them (no Claude call). Used for sources with no usable RSS feed.

extract_spec:
    item_selector    : CSS for each repeating item (required)
    title_selector   : CSS for the headline within an item (required)
    link_selector    : CSS for the anchor within an item (defaults to title)
    snippet_selector  : CSS for a teaser within an item (optional)
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlsplit

from ._net import fetch_bytes

log = logging.getLogger("4_render_list.web")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) 4_List_renderer/1.0"


def _fetch(url: str, timeout: int = 20) -> str:
    raw = fetch_bytes(url, timeout=timeout, headers={"User-Agent": _UA})
    return raw.decode("utf-8", "replace")


def fetch_results(url: str, extract_spec: dict, max_items: int = 20) -> list[dict]:
    from bs4 import BeautifulSoup  # lazy import; only web sources need it

    item_sel = extract_spec.get("item_selector")
    title_sel = extract_spec.get("title_selector")
    if not item_sel or not title_sel:
        raise ValueError("web recipe requires item_selector and title_selector")
    link_sel = extract_spec.get("link_selector") or title_sel
    snip_sel = extract_spec.get("snippet_selector")

    soup = BeautifulSoup(_fetch(url), "html.parser")
    domain = urlsplit(url).netloc
    results: list[dict] = []
    for item in soup.select(item_sel)[:max_items]:
        title_el = item.select_one(title_sel)
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        if not title:
            continue
        link_el = item.select_one(link_sel)
        href = link_el.get("href") if link_el else None
        link = urljoin(url, href) if href else url
        snippet = ""
        if snip_sel:
            sn = item.select_one(snip_sel)
            if sn:
                snippet = sn.get_text(strip=True)[:240]
        results.append({
            "site_name": domain,
            "url_breadcrumb": domain,
            "favicon": f"https://www.google.com/s2/favicons?sz=64&domain={domain}",
            "title": title,
            "title_url": link,
            "verified": True,
            "snippet": snippet,
            "bold_terms": [],
            "read_more_url": link,
        })
    log.info("  web %s: %d item(s)", url, len(results))
    return results
