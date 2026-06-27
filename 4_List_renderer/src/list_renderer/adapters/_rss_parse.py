"""Shared RSS/Atom parsing helpers (used by the generic `rss` adapter and the
bespoke `news` adapter). Stdlib only (urllib + xml.etree).

Handles the quirks this project hit: titles wrapped in <a> (FierceBiotech),
HTML entities/CDATA, mixed encodings, and RSS vs Atom element names.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET  # type hints only; parsing uses defusedxml
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from defusedxml.ElementTree import fromstring as _safe_fromstring

from ._net import fetch_bytes

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) 4_List_renderer/1.0"
ATOM = "{http://www.w3.org/2005/Atom}"


def fetch(url: str, timeout: int = 15) -> bytes:
    return fetch_bytes(url, timeout=timeout, headers={"User-Agent": UA})


def text(elem: ET.Element | None) -> str:
    """Full text incl. nested tags, HTML stripped, entities unescaped, collapsed."""
    if elem is None:
        return ""
    raw = "".join(elem.itertext())
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = html.unescape(raw).replace("\xa0", " ")
    return re.sub(r"\s+", " ", raw).strip()


def item_link(item: ET.Element) -> str:
    link = item.find("link")
    if link is not None and (link.text or "").strip():
        return link.text.strip()
    alink = item.find(f"{ATOM}link")  # Atom: <link href="...">
    if alink is not None:
        return (alink.get("href") or "").strip()
    return ""


def _parse_dt(item: ET.Element):
    """Parsed datetime from pubDate/updated/published, or None."""
    raw = (text(item.find("pubDate")) or text(item.find(f"{ATOM}updated"))
           or text(item.find(f"{ATOM}published")))
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        # Atom uses ISO-8601, not RFC-2822 -> try fromisoformat.
        from datetime import datetime
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None


def item_date(item: ET.Element) -> str:
    dt = _parse_dt(item)
    return dt.strftime("%b %d, %Y") if dt else ""


def item_date_iso(item: ET.Element) -> str | None:
    """UTC ISO-8601 publish time (NN-3), or None. The L1 recency attribute (D33)."""
    from datetime import timezone
    dt = _parse_dt(item)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def breadcrumb(link: str) -> str:
    parts = urlsplit(link)
    segs = [s for s in parts.path.split("/") if s][:3]
    crumb = parts.netloc
    if segs:
        crumb += " › " + " › ".join(segs)
    return crumb[:90]


def result_from_item(item: ET.Element, brand: str) -> dict | None:
    """Map one RSS/Atom <item>/<entry> to a Highlight dict for site `brand`."""
    title = text(item.find("title")) or text(item.find(f"{ATOM}title"))
    link = item_link(item)
    if not title or not link:
        return None

    desc = text(item.find("description")) or text(item.find(f"{ATOM}summary"))
    if len(desc) > 240:
        desc = desc[:237].rstrip() + "..."

    date = item_date(item)
    snippet = f"{date} — {desc}".strip(" —") if date else desc
    bold = [date] if date else []

    domain = urlsplit(link).netloc
    return {
        "site_name": brand,
        "url_breadcrumb": breadcrumb(link),
        "favicon": f"https://www.google.com/s2/favicons?sz=64&domain={domain}",
        "title": title,
        "title_url": link,
        "verified": True,
        "snippet": snippet,
        "bold_terms": bold,
        "read_more_url": link,
        "published_at": item_date_iso(item),   # L1 structural attribute (D33)
    }


def parse_feed(raw: bytes, brand: str, max_items: int) -> list[dict]:
    """Parse feed bytes -> up to `max_items` Highlight dicts."""
    root = _safe_fromstring(raw)  # defused: no XXE / entity-expansion DoS
    items = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    out: list[dict] = []
    for item in items:
        result = result_from_item(item, brand)
        if result:
            out.append(result)
        if len(out) >= max_items:
            break
    return out
