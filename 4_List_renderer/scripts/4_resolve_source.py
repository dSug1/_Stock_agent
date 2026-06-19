"""4_resolve_source — resolve how to fetch+extract a source via Claude, and cache
the recipe. THIS CAN SPEND MONEY: it estimates cost and requires a [y/N]
authorization before any billed call (D4). Only --yes bypasses (single-shot).

Free shortcut: if the page declares an RSS/Atom feed, the recipe is built from
that with NO Claude call (unless --force-claude).

Usage (PYTHONPATH=src):
    python scripts/4_resolve_source.py --url https://example.com/news
    python scripts/4_resolve_source.py --url https://site.com --source-id mysite --add-to-board
    python scripts/4_resolve_source.py --url https://site.com --estimate-only
    python scripts/4_resolve_source.py --url https://site.com --yes   # skip the gate
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from list_renderer import db as listdb
from list_renderer.llm import format_cost_panel, self_context
from list_renderer.resolver import (
    load_config, prepare, resolve_source, save_recipe, source_signature,
)
from list_renderer.db import now_iso

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIST_DB = ROOT / "data" / "list_renderer.db"

log = logging.getLogger("4_resolve_source")


def _confirm() -> bool:
    try:
        return input("\nProceed with this billed Claude call? [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _attach_to_source(conn, source_id, method, fetch_spec, recipe_id, on_board):
    """Point an existing/registered source at the resolved recipe."""
    ts = now_iso()
    if method == "rss":
        config = {"feed_url": fetch_spec.get("feed_url")}
        adapter = "rss"
    else:
        config = {"url": fetch_spec.get("url")}
        adapter = "web"
    conn.execute(
        """
        INSERT INTO sources (id, kind, adapter, name, config_json, recipe_id,
                             origin, enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'discovered', 1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            kind=excluded.kind, adapter=excluded.adapter,
            config_json=excluded.config_json, recipe_id=excluded.recipe_id,
            updated_at=excluded.updated_at
        """,
        (source_id, method, adapter, source_id, json.dumps(config), recipe_id, ts, ts),
    )
    if on_board:
        conn.execute(
            """
            INSERT INTO subscriptions (user_id, board_id, source_id, position, enabled, created_at)
            VALUES (?, ?, ?, COALESCE((SELECT MAX(position)+1 FROM subscriptions
                    WHERE user_id=? AND board_id=?),0), 1, ?)
            ON CONFLICT(user_id, board_id, source_id) DO UPDATE SET enabled=1
            """,
            (listdb.LOCAL_USER_ID, listdb.DEFAULT_BOARD_ID, source_id,
             listdb.LOCAL_USER_ID, listdb.DEFAULT_BOARD_ID, ts),
        )
    conn.commit()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Resolve a source recipe via Claude (cost-gated).")
    p.add_argument("--url", required=True)
    p.add_argument("--source-id", help="Register/attach the recipe to this source id.")
    p.add_argument("--add-to-board", action="store_true", help="Put the source on the board.")
    p.add_argument("--model", help="Override resolver model (e.g. claude-sonnet-4-6).")
    p.add_argument("--estimate-only", action="store_true", help="Show cost, never call.")
    p.add_argument("--force-claude", action="store_true", help="Ignore the free RSS shortcut.")
    p.add_argument("--yes", action="store_true", help="Skip the [y/N] gate (single-shot).")
    p.add_argument("--list-db", type=Path, default=DEFAULT_LIST_DB)
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    cfg = load_config()
    if args.model:
        cfg["model"] = args.model

    log.info("Fetching %s and preparing prompt ...", args.url)
    prepared = prepare(args.url, cfg)
    sig = source_signature("source", args.url)

    conn = listdb.connect(args.list_db)
    try:
        # Free path: a declared RSS feed needs no Claude call.
        if prepared["rss_shortcut"] and not args.force_claude:
            feed = prepared["rss_shortcut"]
            log.info("Found a declared RSS feed (no Claude call needed):\n  %s", feed)
            if args.estimate_only:
                return 0
            rid = save_recipe(conn, signature=sig, kind="rss",
                              fetch_spec={"adapter": "rss", "feed_url": feed},
                              extract_spec=None, prompt_version="rss-shortcut",
                              resolved_by="deterministic", confidence=0.99)
            if args.source_id:
                _attach_to_source(conn, args.source_id, "rss",
                                  {"feed_url": feed}, rid, args.add_to_board)
            log.info("Saved recipe %s (rss). Cost: $0.00", rid)
            return 0

        # Billed path: estimate -> authorize -> call.
        print(format_cost_panel(prepared["estimate"]))
        if args.estimate_only:
            log.info("Estimate only; no call made.")
            return 0

        billing = self_context()
        if not billing.has_credentials():
            log.error("No ANTHROPIC_API_KEY in env/.env — cannot make a billed call.")
            return 1
        if not args.yes and not _confirm():
            log.info("Aborted - no Claude call made, nothing billed.")
            return 0

        log.info("Calling Claude (resolve_recipe) ...")
        method, fetch_spec, extract_spec, confidence = resolve_source(
            conn, args.url, cfg, billing, prepared)
        rid = save_recipe(conn, signature=sig, kind=method, fetch_spec=fetch_spec,
                          extract_spec=extract_spec, prompt_version=cfg["model"],
                          resolved_by=cfg["model"], confidence=confidence)
        log.info("Saved recipe %s (method=%s, confidence=%s).", rid, method, confidence)
        if extract_spec:
            log.info("  extract: %s", json.dumps(extract_spec))
        if args.source_id:
            _attach_to_source(conn, args.source_id, method, fetch_spec, rid, args.add_to_board)
            log.info("  attached to source '%s'%s.", args.source_id,
                     " (on board)" if args.add_to_board else "")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
