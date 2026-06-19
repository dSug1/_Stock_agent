"""Adapter: news RSS feeds -> list_renderer payload.

Fetches N articles from each configured news site (FierceBiotech, Le Figaro,
CNBC) and maps them onto the generic result schema rendered by the stable
Outputs/list_results.html template. Same template, different source.

Stdlib only (urllib + xml.etree) so no new dependency is pulled in.
"""

from __future__ import annotations

import html
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from list_renderer.render import build_payload

log = logging.getLogger("4_render_list.news")

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) 4_List_renderer/1.0"
_ATOM = "{http://www.w3.org/2005/Atom}"

# Order matters: the requested site order is preserved in the output.
SITES = [
    {
        "key": "fiercebiotech",
        "brand": "FierceBiotech",
        "feeds": ["https://www.fiercebiotech.com/rss/xml"],
    },
    {
        "key": "lefigaro",
        "brand": "Le Figaro",
        "feeds": [
            "https://www.lefigaro.fr/rss/figaro_actualites.xml",
            "https://www.lefigaro.fr/rss/figaro_flash-actu.xml",
        ],
    },
    {
        "key": "cnbc",
        "brand": "CNBC",
        "feeds": ["https://www.cnbc.com/id/100003114/device/rss/rss.html"],
    },
]


def _fetch(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _text(elem: ET.Element | None) -> str:
    """Full text of an element incl. nested tags (FierceBiotech wraps the
    title in an <a>), with HTML tags/entities stripped and space collapsed."""
    if elem is None:
        return ""
    raw = "".join(elem.itertext())
    raw = re.sub(r"<[^>]+>", "", raw)          # strip any inline markup
    raw = html.unescape(raw).replace("\xa0", " ")
    return re.sub(r"\s+", " ", raw).strip()


def _item_link(item: ET.Element) -> str:
    link = item.find("link")
    if link is not None and (link.text or "").strip():
        return link.text.strip()
    # Atom: <link href="...">
    alink = item.find(f"{_ATOM}link")
    if alink is not None:
        return (alink.get("href") or "").strip()
    return ""


def _item_date(item: ET.Element) -> str:
    raw = _text(item.find("pubDate")) or _text(item.find(f"{_ATOM}updated"))
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).strftime("%b %d, %Y")
    except (TypeError, ValueError):
        return raw[:24]


def _breadcrumb(link: str) -> str:
    parts = urlsplit(link)
    segs = [s for s in parts.path.split("/") if s][:3]
    crumb = parts.netloc
    if segs:
        crumb += " › " + " › ".join(segs)
    return crumb[:90]


def _result_from_item(item: ET.Element, site: dict) -> dict | None:
    title = _text(item.find("title")) or _text(item.find(f"{_ATOM}title"))
    link = _item_link(item)
    if not title or not link:
        return None

    desc = _text(item.find("description")) or _text(item.find(f"{_ATOM}summary"))
    if len(desc) > 240:
        desc = desc[:237].rstrip() + "..."

    date = _item_date(item)
    # Lead the snippet with the publish date and embolden it (Google-style).
    snippet = f"{date} — {desc}".strip(" —") if date else desc
    bold = [date] if date else []

    domain = urlsplit(link).netloc
    return {
        "site_name": site["brand"],
        "url_breadcrumb": _breadcrumb(link),
        "favicon": f"https://www.google.com/s2/favicons?sz=64&domain={domain}",
        "title": title,
        "title_url": link,
        "verified": True,
        "snippet": snippet,
        "bold_terms": bold,
        "read_more_url": link,
    }


def _fetch_site(site: dict, per_site: int) -> list[dict]:
    for feed in site["feeds"]:
        try:
            root = ET.fromstring(_fetch(feed))
        except Exception as exc:  # network or parse failure -> try next feed
            log.warning("  %s: %s failed (%s)", site["brand"], feed, exc)
            continue
        items = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
        out: list[dict] = []
        for item in items:
            result = _result_from_item(item, site)
            if result:
                out.append(result)
            if len(out) >= per_site:
                break
        if out:
            log.info("  %s: %d article(s) from %s", site["brand"], len(out), feed)
            return out
    log.warning("  %s: no articles fetched", site["brand"])
    return []


def fetch_results(per_site: int = 3) -> list[dict]:
    """Fetch `per_site` articles from each configured site, in order, as a flat
    list of Highlight dicts. Used by the registry orchestrator (M8)."""
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
