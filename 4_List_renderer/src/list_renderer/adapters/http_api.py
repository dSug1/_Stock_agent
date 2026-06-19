"""Generic `http_api` adapter (Phase 2) — fetch a JSON API by config.

Maps an arbitrary JSON response onto Highlights via dotted-path field mappings.
Covers the "social/forums" priority (D10): Reddit JSON, Hacker News, etc.

Config (source.config_json):
    url         : str                      (required)
    items_path  : str  = ""    dotted path to the array of items
                                (e.g. "data.children" for Reddit)
    fields      : dict (required) maps Highlight field -> dotted path INTO each item:
                  { title, url, snippet?, site_name?, published?, breadcrumb? }
    headers     : dict = {}
    brand       : str  = url domain
    max_items   : int  = 15

Example (subreddit):
    url: https://www.reddit.com/r/biotech/hot.json?limit=15
    items_path: data.children
    fields: { title: data.title, url: data.url, snippet: data.selftext }
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlsplit

log = logging.getLogger("4_render_list.http_api")


def _to_iso(value) -> str | None:
    """Normalize an API timestamp to UTC ISO-8601 (NN-3). Accepts epoch
    seconds/ms (int/float/numeric str) or an ISO-8601 string; else None."""
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
            ts = float(value)
            if ts > 1e12:           # milliseconds
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) 4_List_renderer/1.0"


def _dig(obj, dotted: str):
    """Follow a dotted path into nested dicts/lists; return None if missing."""
    if not dotted:
        return obj
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


def fetch_results(
    url: str,
    fields: dict,
    items_path: str = "",
    headers: dict | None = None,
    brand: str | None = None,
    max_items: int = 15,
) -> list[dict]:
    if not url or not fields or not fields.get("title"):
        raise ValueError("http_api adapter requires config.url and fields.title")

    req = urllib.request.Request(url, headers={"User-Agent": _UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))

    raw_items = _dig(data, items_path) or []
    if not isinstance(raw_items, list):
        raise ValueError(f"items_path {items_path!r} did not resolve to a list")

    label = brand or urlsplit(url).netloc or "API"
    results: list[dict] = []
    for it in raw_items[:max_items]:
        title = _dig(it, fields["title"])
        link = _dig(it, fields.get("url", "")) or ""
        if not title:
            continue
        snippet = _dig(it, fields.get("snippet", "")) or ""
        if isinstance(snippet, str) and len(snippet) > 240:
            snippet = snippet[:237].rstrip() + "..."
        site_name = _dig(it, fields.get("site_name", "")) or label
        domain = urlsplit(link).netloc or urlsplit(url).netloc
        published = _to_iso(_dig(it, fields["published"])) if fields.get("published") else None
        results.append({
            "site_name": str(site_name),
            "url_breadcrumb": _dig(it, fields.get("breadcrumb", "")) or domain,
            "favicon": f"https://www.google.com/s2/favicons?sz=64&domain={domain}",
            "title": str(title),
            "title_url": str(link),
            "verified": True,
            "snippet": str(snippet),
            "bold_terms": [],
            "read_more_url": str(link) or None,
            "published_at": published,           # L1 structural attribute (D33)
        })
    log.info("  http_api %s: %d item(s)", url, len(results))
    return results
