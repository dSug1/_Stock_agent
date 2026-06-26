"""Jury parsers — turn each jury's edition into structured ``jury_signals`` rows.

Build-first = the **machine-readable feeds** (spec §2/§9): clean public APIs/JSON we can pull whole
histories from in one shot. This module ships two of them concretely — **Y Combinator** (yc-oss JSON,
batches back to 2005; a *leading* startup jury that names private firms + explicit Requests-for-
Startups themes) and the **Nobel Prize** API (a *denominator* jury that dates a theme, never finds
it — the Pouzin nuance). Both are parse-split-from-fetch + injectable-HTTP + fail-open, the 5_Hype
house style, so tests feed canned payloads with no network.

The **scrape** juries (MIT-TR10, R&D 100, Fierce 15, SPIE, BNEF…) are already captured by the OD-2
forward archive (``source_snapshots``); ``ingest_snapshot_source`` is the framework that parses the
latest archived edition. A robust per-source HTML extractor is a follow-up (spec §8 "snapshot-parser
robustness"); the generic extractor here is a labelled-crude first cut.
"""

import json
import logging
import re
import urllib.request

log = logging.getLogger(__name__)

USER_AGENT = "HypeParser/0.1 (research; local)"

YC_ALL_URL = "https://yc-oss.github.io/api/companies/all.json"
NOBEL_URL = "https://api.nobelprize.org/2.0/nobelPrizes?limit=1000"


def _default_http_get(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ─── Y Combinator (leading; private-firm + RFS theme jury) ───────────────────

_BATCH_YEAR = re.compile(r"(20\d{2})|[WSF](\d{2})|IK(\d{2})")


def _batch_year(batch: str | None):
    """YC batch label -> edition year. Handles 'Winter 2021', 'W21', 'S20', 'IK12'. None if absent."""
    if not batch:
        return None
    m = _BATCH_YEAR.search(str(batch))
    if not m:
        return None
    if m.group(1):
        return int(m.group(1))
    yy = m.group(2) or m.group(3)
    return 2000 + int(yy) if yy else None


def parse_yc(payload, *, source_id: str = "yc_batch_rfs", since_year: int | None = None) -> list[dict]:
    """yc-oss companies JSON (list, or {'companies': [...]}) -> jury signals (one per company).

    ``item_text`` = name + one-liner + industry tags (what convergence embeds). ``since_year`` bounds
    the history (the full corpus is ~5k firms; the proving slice wants recent editions)."""
    companies = payload.get("companies", []) if isinstance(payload, dict) else (payload or [])
    out = []
    for c in companies:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        year = _batch_year(c.get("batch"))
        if since_year and (year is None or year < since_year):
            continue
        blurb = (c.get("one_liner") or c.get("oneLiner") or c.get("long_description") or "").strip()
        tags = c.get("tags") or c.get("industries") or []
        tag_text = " ".join(t for t in tags if isinstance(t, str))
        item_text = " — ".join(p for p in (name, blurb, tag_text) if p)
        out.append({
            "source_id": source_id,
            "diffusion_position": "leading",
            "jury_credibility": "medium",
            "year": year,
            "item_text": item_text,
            "entity": name,
            "entity_type": "company",
            "url": c.get("url") or c.get("website"),
        })
    return out


def fetch_yc(http_get=None, *, since_year: int | None = None) -> list[dict]:
    http_get = http_get or _default_http_get
    try:
        payload = json.loads(http_get(YC_ALL_URL))
    except Exception as exc:  # fail-open
        log.warning("YC fetch failed: %s", exc)
        return []
    return parse_yc(payload, since_year=since_year)


# ─── Nobel Prize (denominator; dates a theme, does NOT find it) ───────────────

def parse_nobel(payload, *, source_id: str = "nobel_prize", since_year: int | None = None) -> list[dict]:
    """Nobel API v2.0 nobelPrizes JSON -> one denominator signal per (prize, laureate). The text is
    the category + motivation (what was recognized), so convergence can date a theme it confirms."""
    prizes = payload.get("nobelPrizes", []) if isinstance(payload, dict) else (payload or [])
    out = []
    for p in prizes:
        year = p.get("awardYear")
        year = int(year) if year and str(year).isdigit() else None
        if since_year and (year is None or year < since_year):
            continue
        category = ((p.get("category") or {}).get("en")
                    if isinstance(p.get("category"), dict) else p.get("category")) or ""
        for lau in p.get("laureates", []) or []:
            name = (lau.get("fullName") or lau.get("knownName") or {})
            name = name.get("en") if isinstance(name, dict) else name
            motivation = (lau.get("motivation") or {})
            motivation = motivation.get("en") if isinstance(motivation, dict) else (motivation or "")
            item_text = " — ".join(x for x in (category, motivation) if x).strip(" —")
            if not item_text:
                continue
            out.append({
                "source_id": source_id,
                "diffusion_position": "denominator",
                "jury_credibility": "high",
                "year": year,
                "item_text": item_text,
                "entity": name,
                "entity_type": "person",
                "url": "https://www.nobelprize.org/prizes/",
            })
    return out


def fetch_nobel(http_get=None, *, since_year: int | None = None) -> list[dict]:
    http_get = http_get or _default_http_get
    try:
        payload = json.loads(http_get(NOBEL_URL))
    except Exception as exc:  # fail-open
        log.warning("Nobel fetch failed: %s", exc)
        return []
    return parse_nobel(payload, since_year=since_year)


# ─── snapshot juries (scrape sources captured by the OD-2 forward archive) ────

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def extract_named_entities_from_html(html: str, *, max_items: int = 200) -> list[str]:
    """CRUDE generic extractor (labelled): list-item / heading text from an archived award page.

    A first cut so the framework runs end-to-end on any snapshot; per-source extractors (knowing each
    page's real winner markup) are the robustness follow-up (spec §8). Pulls <li>/<h2..h4> inner text,
    trims, and keeps short title-like lines (a winner name/blurb), dropping nav/boilerplate."""
    if not html:
        return []
    chunks = re.findall(r"<(?:li|h[2-4])\b[^>]*>(.*?)</(?:li|h[2-4])>", html,
                        flags=re.IGNORECASE | re.DOTALL)
    items, seen = [], set()
    for raw in chunks:
        text = _WS.sub(" ", _TAG.sub(" ", raw)).strip()
        if not (3 <= len(text) <= 140):           # too short = noise; too long = paragraph, not a name
            continue
        low = text.lower()
        if low in seen or any(b in low for b in ("cookie", "subscribe", "sign in", "newsletter",
                                                 "privacy", "all rights")):
            continue
        seen.add(low)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


def _latest_changed_snapshot(conn, source_id: str):
    return conn.execute(
        "SELECT content, fetched_at FROM source_snapshots "
        "WHERE source_id = ? AND content IS NOT NULL "
        "ORDER BY snapshot_id DESC LIMIT 1",
        (source_id,),
    ).fetchone()


def parse_snapshot_source(conn, source_id: str, *, extractor=None, year: int | None = None) -> list[dict]:
    """Parse the latest archived edition of a scrape jury into signals (crude extractor by default).
    Returns [] if nothing is archived yet — discovery never re-fetches; the OD-2 archive owns fetching."""
    row = _latest_changed_snapshot(conn, source_id)
    if not row:
        return []
    src = conn.execute("SELECT diffusion_position, jury_credibility FROM sources WHERE source_id=?",
                       (source_id,)).fetchone()
    extractor = extractor or extract_named_entities_from_html
    out = []
    for text in extractor(row["content"]):
        out.append({
            "source_id": source_id,
            "diffusion_position": src["diffusion_position"] if src else None,
            "jury_credibility": src["jury_credibility"] if src else None,
            "year": year,
            "item_text": text,
            "entity": text,
            "entity_type": "unknown",
            "url": None,
        })
    return out


# API parsers keyed by source_id (the build-first feeds). Snapshot sources go through
# parse_snapshot_source instead. Extend as more clean feeds are wired (CNCF, ARPA-E, Product Hunt…).
API_FETCHERS = {
    "yc_batch_rfs": fetch_yc,
    "nobel_prize": fetch_nobel,
}
