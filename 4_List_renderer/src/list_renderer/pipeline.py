"""Registry-driven render orchestrator (M8 — Phase 1).

Loads the enabled sources for a board, dispatches each to its adapter, stamps
source identity onto every Highlight, merges the lists, and builds the sidecar
payload. Ranking (M6) and dedup (M4) are later phases; Phase 1 merges in
source/position order. Each source fails open (logged, skipped) so one broken
source never blanks the board.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .render import build_payload
from .sources import list_enabled_sources
from .adapters import biopharm as biopharm_adapter
from .adapters import file as file_adapter
from .adapters import news as news_adapter

log = logging.getLogger("4_render_list.pipeline")

ROOT = Path(__file__).resolve().parents[2]          # 4_List_renderer/
REPO_ROOT = ROOT.parent
DEFAULT_BIOTECH_DB = (
    REPO_ROOT / "3_Biopharmcatalyst_parser" / "data" / "biotech.db"
)


def _resolve(path_like, default: Path) -> Path:
    p = Path(path_like) if path_like else default
    return p if p.is_absolute() else (ROOT / p)


def _fetch_source(src: dict) -> list[dict]:
    """Dispatch one registry source to its adapter -> Highlight list."""
    adapter = src["adapter"]
    cfg = src.get("config", {})
    if adapter == "news":
        return news_adapter.fetch_results(per_site=int(cfg.get("per_site", 3)))
    if adapter == "biopharm":
        db = _resolve(cfg.get("db"), DEFAULT_BIOTECH_DB)
        return biopharm_adapter.fetch_results(db, top_n=int(cfg.get("top_n", 10)))
    if adapter == "file":
        path = _resolve(cfg.get("input"), ROOT / "data" / "sample_input.json")
        return file_adapter.fetch_results(path)
    raise ValueError(f"unknown adapter: {adapter!r}")


def build_payload_from_registry(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    board_id: str,
    board_name: str = "My board",
) -> dict:
    """Render the enabled source set for (user_id, board_id) into a payload."""
    sources = list_enabled_sources(conn, user_id=user_id, board_id=board_id)
    results: list[dict] = []
    labels: list[str] = []
    for src in sources:
        try:
            items = _fetch_source(src)
        except Exception as exc:  # fail open — one bad source never blanks the board
            log.warning("  source %s (%s) failed: %s", src["id"], src["adapter"], exc)
            items = []
        for it in items:
            it.setdefault("source_id", src["id"])
        results.extend(items)
        labels.append(src.get("label") or src["name"])
        log.info("  %s: %d item(s)", src["id"], len(items))
    return build_payload(
        query=", ".join(labels) if labels else "no sources enabled",
        brand=board_name,
        results=results,
    )
