"""Constituent resolution — classify each jury-surfaced company **listed vs private** (spec §4a).

Track A (listed) = a confident SEC ticker match ⇒ investable now, feeds the diffusion/panel pipeline.
Track B (private) = no match ⇒ watchlist + a ``listing_watch`` that monitors EDGAR for an S-1/F-1/
424B/S-4 naming the firm; on first filing it flips private→listed (the IPO is often the re-rating
catalyst — the silicon_photonics case). Resolution reuses the SEC ``company_tickers.json`` already
loaded by ``fundamentals.py``; matching is conservative (a wrong ticker is worse than a missed one).
"""

import logging
import re

from ..db import now_iso
from ..fundamentals import TICKERS_URL, _default_http_get

log = logging.getLogger(__name__)

# Corporate suffixes / filler stripped before matching, so "Acme Robotics, Inc." == "Acme Robotics".
_SUFFIX = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|llc|lp|plc|holdings?|"
    r"group|technologies|technology|tech|labs?|systems|solutions|sa|ag|nv|the)\b", re.IGNORECASE)
_NONWORD = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def _normalize(name: str) -> str:
    s = (name or "").lower()
    s = _NONWORD.sub(" ", s)
    s = _SUFFIX.sub(" ", s)
    return _WS.sub(" ", s).strip()


def load_name_index(http_get=None) -> dict:
    """{normalized_company_name: {ticker, cik}} from SEC company_tickers.json. {} on failure.
    On a normalized-name collision the first (lowest CIK row) wins — deterministic, rare."""
    import json
    http_get = http_get or _default_http_get
    try:
        raw = json.loads(http_get(TICKERS_URL))
    except Exception as exc:
        log.warning("ticker name index fetch failed: %s", exc)
        return {}
    index = {}
    for entry in (raw.values() if isinstance(raw, dict) else raw):
        title, tk, cik = entry.get("title"), entry.get("ticker"), entry.get("cik_str")
        if not (title and tk):
            continue
        norm = _normalize(title)
        if norm and norm not in index:
            index[norm] = {"ticker": str(tk).upper(), "cik": str(cik).zfill(10) if cik is not None else None}
    return index


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    return len(sa & sb) / len(sa | sb) if (sa or sb) else 0.0


def match_org(org_name: str, index: dict, *, min_conf: float = 0.90) -> dict:
    """Resolve one org name against the SEC name index.

    Exact normalized match ⇒ listed, confidence 1.0. Otherwise the best token-Jaccard candidate: if it
    clears ``min_conf`` ⇒ listed, else ⇒ private (no ticker). Empty/degenerate name ⇒ unknown."""
    norm = _normalize(org_name)
    if not norm:
        return {"listing_status": "unknown", "ticker": None, "cik": None, "confidence": 0.0}
    hit = index.get(norm)
    if hit:
        return {"listing_status": "listed", "ticker": hit["ticker"], "cik": hit["cik"], "confidence": 1.0}
    best, best_sim = None, 0.0
    for cand_norm, info in index.items():
        sim = _jaccard(norm, cand_norm)
        if sim > best_sim:
            best, best_sim = info, sim
    if best and best_sim >= min_conf:
        return {"listing_status": "listed", "ticker": best["ticker"], "cik": best["cik"],
                "confidence": round(best_sim, 3)}
    return {"listing_status": "private", "ticker": None, "cik": None, "confidence": round(best_sim, 3)}


def _theme_org_entities(conn, theme_id: str) -> list[dict]:
    """Distinct *company* entities backing a discovered theme. Only entity_type='company' is rostered:
    persons/technologies are excluded, and so is 'unknown' (the crude snapshot extractor can't tell a
    company name from a breakthrough headline — rostering those would mislabel a headline as a Track-B
    listing-watch firm). A per-source snapshot parser that tags 'company' lets those juries contribute
    orgs later (spec §8 robustness follow-up); the clean API feeds (YC) already tag 'company'."""
    rows = conn.execute(
        "SELECT DISTINCT s.entity AS entity, s.source_id AS source_id, s.entity_type AS et "
        "FROM theme_convergence tc JOIN jury_signals s ON s.signal_id=tc.signal_id "
        "WHERE tc.theme_id=? AND s.entity IS NOT NULL AND s.entity_type='company'",
        (theme_id,)).fetchall()
    seen, out = set(), []
    for r in rows:
        key = (r["entity"] or "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(dict(r))
    return out


def resolve_theme_orgs(conn, theme_id: str, index: dict, *, min_conf: float = 0.90) -> dict:
    """Classify + upsert ``theme_orgs`` for one theme. Private rows get listing_watch=1 (Track B).
    Returns {listed, private, unknown} counts."""
    now = now_iso()
    counts = {"listed": 0, "private": 0, "unknown": 0}
    for ent in _theme_org_entities(conn, theme_id):
        m = match_org(ent["entity"], index, min_conf=min_conf)
        status = m["listing_status"]
        counts[status] += 1
        conn.execute(
            """
            INSERT INTO theme_orgs
                (theme_id, org_name, source_id, listing_status, ticker, cik,
                 resolution_confidence, listing_watch, first_seen, last_seen)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(theme_id, org_name) DO UPDATE SET
                listing_status=excluded.listing_status, ticker=excluded.ticker, cik=excluded.cik,
                resolution_confidence=excluded.resolution_confidence,
                listing_watch=excluded.listing_watch, last_seen=excluded.last_seen
            """,
            (theme_id, ent["entity"], ent["source_id"], status, m["ticker"], m["cik"],
             m["confidence"], int(status == "private"), now, now),
        )
    conn.commit()
    return counts


def listing_watch_orgs(conn) -> list[dict]:
    """Private orgs under EDGAR listing-watch (Track B) — the S-1 monitor's worklist."""
    return [dict(r) for r in conn.execute(
        "SELECT org_id, theme_id, org_name, source_id, first_seen FROM theme_orgs "
        "WHERE listing_status='private' AND listing_watch=1 AND became_listed_at IS NULL "
        "ORDER BY theme_id, org_name")]


def mark_listed(conn, org_id: int, ticker: str, cik: str | None = None) -> None:
    """Flip a Track-B org private→listed on its first registration filing (the listing event)."""
    conn.execute(
        "UPDATE theme_orgs SET listing_status='listed', ticker=?, cik=?, listing_watch=0, "
        "became_listed_at=? WHERE org_id=?",
        (str(ticker).upper(), cik, now_iso(), org_id))
    conn.commit()


def track_a_tickers(conn, theme_id: str | None = None) -> list[str]:
    """Listed (Track-A) tickers — overall or for one theme — the investable constituent set."""
    sql = "SELECT DISTINCT ticker FROM theme_orgs WHERE listing_status='listed' AND ticker IS NOT NULL"
    params = ()
    if theme_id:
        sql += " AND theme_id=?"
        params = (theme_id,)
    return [r["ticker"] for r in conn.execute(sql + " ORDER BY ticker", params)]
