"""Registry-driven render orchestrator (M8) with the Phase-2 SWR cache.

Loads the board's enabled sources, dispatches each to its adapter (bespoke
file/biopharm/news or generic rss/http_api/sqlite), applies the SWR content
cache for network sources, stamps source identity, merges, and builds the
sidecar payload. Ranking (M6) and dedup (M4) are later phases; Phase 2 merges in
source/position order. Each source fails open (serve stale / skip) so one broken
source never blanks the board.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from . import cache
from . import interactions
from . import normalize as normalize_mod
from . import ranking
from .render import build_payload
from .resolver.recipes import get_recipe_by_id
from .sources import list_enabled_sources
from .adapters import biopharm as biopharm_adapter
from .adapters import file as file_adapter
from .adapters import http_api as http_api_adapter
from .adapters import news as news_adapter
from .adapters import rss as rss_adapter
from .adapters import sqlite_source as sqlite_adapter
from .adapters import web as web_adapter

log = logging.getLogger("4_render_list.pipeline")

ROOT = Path(__file__).resolve().parents[2]          # 4_List_renderer/
REPO_ROOT = ROOT.parent
DEFAULT_BIOTECH_DB = (
    REPO_ROOT / "3_Biopharmcatalyst_parser" / "data" / "biotech.db"
)

# Network adapters whose results are worth caching (SWR). Local adapters
# (file/biopharm/sqlite) are cheap and read fresh each time.
CACHEABLE = {"rss", "http_api", "news", "web"}


def _resolve(path_like, default: Path) -> Path:
    p = Path(path_like) if path_like else default
    return p if p.is_absolute() else (ROOT / p)


def _fetch_source(conn: sqlite3.Connection, src: dict) -> list[dict]:
    """Dispatch one registry source to its adapter -> Highlight list."""
    adapter = src["adapter"]
    cfg = src.get("config", {})
    if adapter == "rss":
        return rss_adapter.fetch_results(
            cfg.get("feed_url"), cfg.get("brand"), int(cfg.get("max_items", 10)))
    if adapter == "web":
        recipe = get_recipe_by_id(conn, src.get("recipe_id")) if src.get("recipe_id") else None
        extract_spec = (recipe or {}).get("extract_spec") or cfg.get("extract_spec")
        if not extract_spec:
            raise ValueError(f"web source {src['id']} has no recipe/extract_spec")
        return web_adapter.fetch_results(
            cfg.get("url"), extract_spec, int(cfg.get("max_items", 20)))
    if adapter == "http_api":
        return http_api_adapter.fetch_results(
            cfg.get("url"), cfg.get("fields", {}), cfg.get("items_path", ""),
            cfg.get("headers"), cfg.get("brand"), int(cfg.get("max_items", 15)))
    if adapter == "sqlite":
        return sqlite_adapter.fetch_results(
            _resolve(cfg.get("db"), ROOT), cfg.get("query"), cfg.get("columns", {}),
            cfg.get("brand", "SQLite"), int(cfg.get("max_items", 50)))
    if adapter == "news":
        return news_adapter.fetch_results(per_site=int(cfg.get("per_site", 3)))
    if adapter == "biopharm":
        db = _resolve(cfg.get("db"), DEFAULT_BIOTECH_DB)
        return biopharm_adapter.fetch_results(db, top_n=int(cfg.get("top_n", 10)))
    if adapter == "file":
        path = _resolve(cfg.get("input"), ROOT / "data" / "sample_input.json")
        return file_adapter.fetch_results(path)
    raise ValueError(f"unknown adapter: {adapter!r}")


def _items_for_source(
    conn: sqlite3.Connection, src: dict, *, refresh: bool, no_fetch: bool
) -> list[dict]:
    """Resolve one source's Highlights honoring the SWR cache."""
    adapter = src["adapter"]
    sid = src["id"]
    cacheable = adapter in CACHEABLE
    ttl = int(src.get("config", {}).get("cache_ttl", cache.DEFAULT_TTL_SECONDS))

    if cacheable and no_fetch:
        return cache.get_cached_items(conn, sid)
    if cacheable and not refresh and cache.is_fresh(conn, sid, ttl):
        log.info("  %s: cache hit", sid)
        return cache.get_cached_items(conn, sid)

    try:
        items = _fetch_source(conn, src)
    except Exception as exc:  # fail open: serve stale (cacheable) or skip
        log.warning("  source %s (%s) failed: %s", sid, adapter, exc)
        if cacheable:
            cache.mark_fetched(conn, sid, "error", 0)
            return cache.get_cached_items(conn, sid)
        return []

    if cacheable:
        cache.store_items(conn, sid, items)
        cache.mark_fetched(conn, sid, "ok", len(items))
    return items


def build_payload_from_registry(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    board_id: str,
    board_name: str = "My board",
    refresh: bool = False,
    no_fetch: bool = False,
    rank: bool = True,
    include_breakdown: bool = False,
) -> dict:
    """Render the enabled source set for (user_id, board_id) into a payload.

    Results are ordered by the M6 interest model (descending `score`) unless
    `rank=False`; ranking fails open to source/position order. (M4 cross-source
    dedup is still pending — the same story from two feeds can still appear twice.)
    """
    sources = list_enabled_sources(conn, user_id=user_id, board_id=board_id)
    results: list[dict] = []
    labels: list[str] = []
    for src in sources:
        items = _items_for_source(conn, src, refresh=refresh, no_fetch=no_fetch)
        for it in items:
            it.setdefault("source_id", src["id"])
            # Every rendered item needs a stable id so M7 interactions can key
            # off it (cached network items already have one; local sources —
            # file/biopharm/sqlite — are stamped here).
            it.setdefault("id", cache.item_id(src["id"], it))
        results.extend(items)
        labels.append(src.get("label") or src["name"])
        log.info("  %s: %d item(s)", src["id"], len(items))
    # M4: enrich (domain + L2 keyword topics) then cross-source dedup, before ranking.
    results, n_deduped = normalize_mod.normalize(results)
    if n_deduped:
        log.info("  dedup: collapsed %d cross-source duplicate(s)", n_deduped)
    if rank:
        results = ranking.rank_results(
            conn, user_id, results, include_breakdown=include_breakdown
        )
    return build_payload(
        query=", ".join(labels) if labels else "no sources enabled",
        brand=board_name,
        results=results,
    )


def build_board(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    board_id: str,
    board_name: str = "My board",
    refresh: bool = False,
    no_fetch: bool = False,
    apply_user_state: bool = True,
) -> dict:
    """Board payload for the M7 server delivery (§13.7 / D15).

    Same `LIST_DATA` shape as the file sidecar, plus the per-user state the
    served UI needs: hidden items are filtered out (deterministic override), and
    a `seen`/`liked` id list is attached under `meta` so the template can dim
    seen rows (D22) and reflect like state — without changing the result schema
    (the template ignores unknown `meta`, so this stays file-sidecar compatible).
    """
    payload = build_payload_from_registry(
        conn,
        user_id=user_id,
        board_id=board_id,
        board_name=board_name,
        refresh=refresh,
        no_fetch=no_fetch,
    )
    if apply_user_state:
        hidden = interactions.hidden_item_ids(conn, user_id=user_id, board_id=board_id)
        if hidden:
            payload["results"] = [
                r for r in payload["results"] if r.get("id") not in hidden
            ]
        payload["meta"] = {
            "mode": "no_fetch" if no_fetch else ("fresh" if refresh else "swr"),
            "seen": sorted(
                interactions.seen_item_ids(conn, user_id=user_id, board_id=board_id)
            ),
            "liked": sorted(
                interactions.liked_item_ids(conn, user_id=user_id, board_id=board_id)
            ),
            "n_hidden": len(hidden),
        }
    return payload
