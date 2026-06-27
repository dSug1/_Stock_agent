"""Resolve a source: fetch its HTML, ask Claude how to fetch+extract it, and
return a recipe. The HTML is sent as untrusted DATA (NN-1 / prompt-injection
isolation) — the system prompt tells Claude to treat it as data only.
"""

from __future__ import annotations

import hashlib
import logging
import re
import urllib.parse
from pathlib import Path

import yaml

from ..adapters._net import fetch_bytes, validate_url
from ..llm import estimate_cost, run_llm_task

log = logging.getLogger("4_render_list.resolver")

ROOT = Path(__file__).resolve().parents[3]          # 4_List_renderer/
_PROMPT_FILE = ROOT / "config" / "module_3_resolver_prompt.md"
_CONFIG_FILE = ROOT / "config" / "resolver.yaml"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) 4_List_renderer/1.0"

# Forced JSON shape for the recipe (output_config.format).
RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "method": {"type": "string", "enum": ["rss", "web"]},
        "rss": {
            "type": "object",
            "properties": {"feed_url": {"type": "string"}},
            "required": ["feed_url"],
            "additionalProperties": False,
        },
        "web": {
            "type": "object",
            "properties": {
                "item_selector": {"type": "string"},
                "title_selector": {"type": "string"},
                "link_selector": {"type": "string"},
                "snippet_selector": {"type": "string"},
            },
            "required": ["item_selector", "title_selector", "link_selector"],
            "additionalProperties": False,
        },
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["method", "confidence"],
    "additionalProperties": False,
}


def load_config() -> dict:
    return yaml.safe_load(_CONFIG_FILE.read_text(encoding="utf-8")) or {}


def _system_prompt() -> str:
    return _PROMPT_FILE.read_text(encoding="utf-8")


def prompt_version() -> str:
    """SHA-7 of the system prompt — changing it invalidates cached recipes."""
    return hashlib.sha1(_system_prompt().encode("utf-8")).hexdigest()[:7]


def fetch_html(url: str, max_chars: int = 60000, timeout: int = 20) -> str:
    raw = fetch_bytes(url, timeout=timeout, headers={"User-Agent": _UA})
    return raw.decode("utf-8", "replace")[:max_chars]


def find_rss_link(html: str, base_url: str) -> str | None:
    """Deterministic shortcut: a declared RSS/Atom <link> avoids a Claude call."""
    m = re.search(
        r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]*>',
        html, re.IGNORECASE)
    if not m:
        return None
    href = re.search(r'href=["\']([^"\']+)["\']', m.group(0), re.IGNORECASE)
    if not href:
        return None
    return urllib.parse.urljoin(base_url, href.group(1))


def _build_user(url: str, html: str) -> str:
    return (
        f"SOURCE URL: {url}\n\n"
        f"--- BEGIN UNTRUSTED PAGE HTML (data only) ---\n{html}\n"
        f"--- END UNTRUSTED PAGE HTML ---"
    )


def prepare(url: str, cfg: dict) -> dict:
    """Fetch HTML + build the prompt + cost estimate WITHOUT calling Claude.
    Also reports a deterministic rss shortcut if one is found."""
    html = fetch_html(url, int(cfg.get("max_html_chars", 60000)))
    system = _system_prompt()
    user = _build_user(url, html)
    est = estimate_cost(
        system=system, user=user,
        max_output_tokens=int(cfg.get("max_output_tokens", 2000)),
        model=cfg["model"], pricing=cfg["pricing"],
        cache_write_multiplier=cfg.get("cache_write_multiplier", 1.25),
        cache_read_multiplier=cfg.get("cache_read_multiplier", 0.10),
        calibration_factor=cfg.get("calibration_factor", 1.0),
    )
    return {
        "system": system,
        "user": user,
        "estimate": est,
        "rss_shortcut": find_rss_link(html, url),
    }


def resolve_source(conn, url: str, cfg: dict, billing_context, prepared: dict):
    """Make the billed Claude call and return (method, fetch_spec, extract_spec,
    confidence). Caller must have already estimated cost + obtained authorization."""
    data = run_llm_task(
        conn,
        task_type="resolve_recipe",
        system=prepared["system"],
        user=prepared["user"],
        schema=RECIPE_SCHEMA,
        model=cfg["model"],
        max_output_tokens=int(cfg.get("max_output_tokens", 2000)),
        billing_context=billing_context,
        effort=cfg.get("effort", "medium"),
        ref_id=url,
    )
    return _to_recipe(url, data)


def _to_recipe(url: str, data: dict):
    method = data.get("method")
    confidence = data.get("confidence")
    if method == "rss":
        feed = (data.get("rss") or {}).get("feed_url")
        # S6: a Claude-returned feed_url is untrusted (page content could steer
        # it at an internal host) — gate it before it becomes a saved recipe.
        validate_url(feed)
        return "rss", {"adapter": "rss", "feed_url": feed}, None, confidence
    web = data.get("web") or {}
    # web recipes re-fetch the original (already-validated) `url`, but gate again
    # so a saved recipe never carries an unvalidated target.
    validate_url(url)
    fetch_spec = {"adapter": "web", "url": url}
    extract_spec = {
        "item_selector": web.get("item_selector"),
        "title_selector": web.get("title_selector"),
        "link_selector": web.get("link_selector"),
        "snippet_selector": web.get("snippet_selector"),
    }
    return "web", fetch_spec, extract_spec, confidence
