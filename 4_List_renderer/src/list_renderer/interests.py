"""Interest input & source discovery (M5, Phase 6).

The user declares an *area of interest* — a site URL, a topic, a query, or a
ticker — and M5 turns it into concrete, registered sources (D7):

  1. **Classify** the input kind (site | ticker | topic).
  2. **Resolve → register** a source (`origin='discovered'`, on the board):
     - **site**: discover a declared RSS feed for free (the M3 resolver's
       deterministic `<link rel=alternate type=application/rss+xml>` shortcut —
       no Claude call). If the page declares no feed, the interest is stored
       `pending`; a billed Claude resolution is deferred to the gated
       `4_resolve_source.py` CLI (D4 keeps billed calls behind the [y/N] gate;
       the server has no terminal, so it never calls Claude itself).
     - **topic / query / ticker**: register a free **Google News RSS search**
       source for the term (the generic `rss` adapter fetches it) — so the board
       actually surfaces items for that interest with no Claude and no new code.
  3. **Store** the interest in `interests`, which also feeds the M6
     `interest_match` ranking feature immediately.

Resolution is automatic + audited (an `interests` row + a `discovered` source),
never an interactive per-item prompt (`feedback_avoid_multiplying_user_requests`).
Fails open: a network/discovery failure stores the interest `pending` and reports
why, rather than raising.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import urllib.parse

from .db import DEFAULT_BOARD_ID, LOCAL_USER_ID, now_iso
from .sources import add_source

log = logging.getLogger("4_render_list.interests")

# Google News RSS search — a free, generic "query/topic/ticker -> feed".
_GNEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"

_URL_RE = re.compile(r"^(https?://|www\.)", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.[a-z]{2,}(/.*)?$", re.IGNORECASE)
# Explicit $TICKER, or 3-5 uppercase letters (avoids tagging "AI"/"ML" as tickers).
_TICKER_RE = re.compile(r"^\$[A-Za-z]{1,5}$|^[A-Z]{3,5}$")


def classify(value: str) -> str:
    """site | ticker | topic. (query is folded into topic for v1 ranking.)"""
    v = value.strip()
    if _URL_RE.match(v) or _DOMAIN_RE.match(v):
        return "site"
    if _TICKER_RE.match(v):
        return "ticker"
    return "topic"


def _slug(value: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return s[:maxlen] or "item"


def google_news_feed(query: str) -> str:
    return _GNEWS.format(q=urllib.parse.quote(query))


def _normalize_site_url(value: str) -> str:
    v = value.strip()
    if not v.lower().startswith(("http://", "https://")):
        v = "https://" + v.lstrip("/")
    return v


def discover_site_feed(url: str) -> str | None:
    """Free RSS discovery: the URL itself if it looks like a feed, else a declared
    <link> on the page (reuses the M3 resolver shortcut). No Claude call."""
    # Lazy import: resolver pulls in the llm package; keep interest input cheap.
    from .resolver.resolve import fetch_html, find_rss_link

    if re.search(r"(\.xml|/rss/?|/feed/?|atom)(\?|$)", url, re.IGNORECASE):
        return url
    html = fetch_html(url)
    return find_rss_link(html, url)


def _interest_exists(conn, user_id, kind, value) -> bool:
    row = conn.execute(
        "SELECT 1 FROM interests WHERE user_id=? AND kind=? AND lower(value)=lower(?) "
        "AND status IN ('active','pending') LIMIT 1",
        (user_id, kind, value),
    ).fetchone()
    return row is not None


def _store_interest(conn, *, user_id, kind, value, status):
    if not _interest_exists(conn, user_id, kind, value):
        conn.execute(
            "INSERT INTO interests (user_id, kind, value, weight, status, created_at) "
            "VALUES (?, ?, ?, 1.0, ?, ?)",
            (user_id, kind, value, status, now_iso()),
        )
        conn.commit()


def add_interest(
    conn: sqlite3.Connection,
    value: str,
    *,
    kind: str | None = None,
    user_id: str = LOCAL_USER_ID,
    board_id: str = DEFAULT_BOARD_ID,
) -> dict:
    """Classify + resolve + register a declared interest. Returns a report dict:
    {kind, status, source_id?, feed_url?, note}. Never raises (fails open)."""
    value = (value or "").strip()
    if not value:
        return {"status": "error", "note": "empty interest"}
    kind = kind or classify(value)

    # --- topic / query / ticker -> free Google News search source ----------
    if kind in ("topic", "query", "ticker"):
        query = f"{value} stock" if kind == "ticker" else value
        feed = google_news_feed(query)
        sid = f"disc_{kind}_{_slug(value)}"
        label = (value if kind != "ticker" else value.upper())
        try:
            add_source(
                conn, source_id=sid, kind="rss", adapter="rss",
                name=f"Interest: {label}", label=label,
                config={"feed_url": feed, "brand": label, "max_items": 10},
                origin="discovered", on_board=True,
                user_id=user_id, board_id=board_id,
            )
            _store_interest(conn, user_id=user_id, kind=kind, value=value, status="active")
            return {"kind": kind, "status": "active", "source_id": sid,
                    "feed_url": feed, "note": f"registered a news-search source for {label!r}"}
        except Exception as exc:  # fail open
            log.warning("interest %r: source registration failed: %s", value, exc)
            _store_interest(conn, user_id=user_id, kind=kind, value=value, status="pending")
            return {"kind": kind, "status": "pending", "note": f"stored; registration failed: {exc}"}

    # --- site -> free RSS discovery, else pending (billed resolve via CLI) ---
    url = _normalize_site_url(value)
    try:
        feed = discover_site_feed(url)
    except Exception as exc:  # network/parse failure -> pending, fail open
        log.warning("interest %r: feed discovery failed: %s", value, exc)
        _store_interest(conn, user_id=user_id, kind="site", value=value, status="pending")
        return {"kind": "site", "status": "pending",
                "note": f"could not reach {url}: {exc}; run scripts/4_resolve_source.py to resolve"}

    if feed:
        host = urllib.parse.urlsplit(url).netloc
        sid = f"disc_site_{_slug(host)}"
        add_source(
            conn, source_id=sid, kind="rss", adapter="rss",
            name=f"Interest: {host}", label=host,
            config={"feed_url": feed, "brand": host, "max_items": 10},
            origin="discovered", on_board=True,
            user_id=user_id, board_id=board_id,
        )
        _store_interest(conn, user_id=user_id, kind="site", value=value, status="active")
        return {"kind": "site", "status": "active", "source_id": sid,
                "feed_url": feed, "note": f"discovered an RSS feed for {host} (free, no Claude)"}

    # No declared feed: defer the billed Claude resolution to the gated CLI (D4).
    _store_interest(conn, user_id=user_id, kind="site", value=value, status="pending")
    return {"kind": "site", "status": "pending",
            "note": f"no RSS feed declared by {url}; resolve with "
                    f"scripts/4_resolve_source.py --url {url} --add-to-board"}


def list_interests(
    conn: sqlite3.Connection, *, user_id: str = LOCAL_USER_ID
) -> list[dict]:
    rows = conn.execute(
        "SELECT id, kind, value, weight, status, created_at FROM interests "
        "WHERE user_id=? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]
