"""Normalization & dedup (M4) — the layer between fetch (M2) and rank (M6).

Takes the merged Highlight list from all enabled sources and:
  1. **Enriches** each item with the cheap L1/L2 attributes ranking learns over
     (D33): a `domain`, and `topics` from the controlled-vocabulary keyword tagger
     (`config/topics.yaml`). `published_at` is already emitted by the adapters
     (the other L1 attribute) — recency uses it directly.
  2. **Dedups** across sources so the same story arriving from two feeds collapses
     to one row (it currently double-renders): exact canonical-URL match, exact
     normalized-title match, then a high-threshold title-token Jaccard for near
     duplicates. The first occurrence wins (preserves source/feed order for the
     ranker); merged sources are recorded on `dup_sources`.

Deterministic, local, no network, no LLM (D33 L1/L2). Embeddings (L3) are the
later taxonomy-free similarity/dedup engine. Fails open: any error returns the
input list unchanged so normalization never blanks the board.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

log = logging.getLogger("4_render_list.normalize")

ROOT = Path(__file__).resolve().parents[2]          # 4_List_renderer/
DEFAULT_TOPICS = ROOT / "config" / "topics.yaml"

# Query params that are tracking noise, stripped when canonicalizing URLs.
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref|ref_src|igshid|spm)", re.I)
_TITLE_JACCARD_THRESHOLD = 0.85
_MIN_TITLE_TOKENS = 4        # below this, only exact-match dedup (avoid false merges)

_vocab_cache: dict | None = None


# --- vocabulary --------------------------------------------------------------

def load_vocab(path: Path | str | None = None) -> dict[str, list[str]]:
    """Load the controlled topic vocabulary {topic: [keywords]} (lowercased)."""
    global _vocab_cache
    if path is None and _vocab_cache is not None:
        return _vocab_cache
    try:
        data = yaml.safe_load(Path(path or DEFAULT_TOPICS).read_text(encoding="utf-8")) or {}
        topics = data.get("topics", {}) or {}
        vocab = {t: [str(k).lower() for k in kws] for t, kws in topics.items()}
    except OSError:
        vocab = {}
    if path is None:
        _vocab_cache = vocab
    return vocab


# --- L2 keyword topic tagging ------------------------------------------------

def tag_topics(text: str, vocab: dict[str, list[str]]) -> list[str]:
    """Topics whose keywords appear in `text` (case-insensitive, word-boundary)."""
    if not text or not vocab:
        return []
    hay = " " + text.lower() + " "
    out = []
    for topic, keywords in vocab.items():
        for kw in keywords:
            # word-boundary match; keywords with leading/trailing spaces (e.g. " ai ")
            # are matched literally to avoid substring noise.
            if kw.startswith(" ") or kw.endswith(" "):
                if kw in hay:
                    out.append(topic); break
            elif re.search(r"\b" + re.escape(kw) + r"\b", hay):
                out.append(topic); break
    return out


def enrich(item: dict, vocab: dict[str, list[str]]) -> dict:
    """Add `domain` + `topics` (L1/L2). published_at comes from the adapter."""
    url = item.get("title_url") or item.get("read_more_url") or ""
    if url and not item.get("domain"):
        item["domain"] = urlsplit(url).netloc.lower()
    if not item.get("topics"):
        text = f"{item.get('title') or ''} {item.get('snippet') or ''}"
        topics = tag_topics(text, vocab)
        if topics:
            item["topics"] = topics
    return item


# --- dedup -------------------------------------------------------------------

def canonical_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    kept = [kv for kv in parts.query.split("&")
            if kv and not _TRACKING.match(kv.split("=", 1)[0])]
    q = ("?" + "&".join(sorted(kept))) if kept else ""
    return f"{host}{path}{q}".lower()


def _title_tokens(title: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def dedup(items: list[dict]) -> tuple[list[dict], int]:
    """Collapse cross-source duplicates; first occurrence wins. Returns
    (deduped_items, n_dropped)."""
    seen_urls: dict[str, dict] = {}
    seen_titles: dict[str, dict] = {}
    kept: list[dict] = []
    kept_token_sets: list[tuple[set[str], dict]] = []
    dropped = 0

    for it in items:
        cu = canonical_url(it.get("title_url") or it.get("read_more_url") or "")
        nt = re.sub(r"\s+", " ", (it.get("title") or "").lower()).strip()
        tokens = _title_tokens(it.get("title") or "")

        match = None
        if cu and cu in seen_urls:
            match = seen_urls[cu]
        elif nt and nt in seen_titles:
            match = seen_titles[nt]
        elif len(tokens) >= _MIN_TITLE_TOKENS:
            for toks, owner in kept_token_sets:
                if _jaccard(tokens, toks) >= _TITLE_JACCARD_THRESHOLD:
                    match = owner
                    break

        if match is not None:
            dropped += 1
            # Record the merged source so the UI/telemetry can show "also from X".
            srcs = match.setdefault("dup_sources", [match.get("source_id")])
            if it.get("source_id") and it["source_id"] not in srcs:
                srcs.append(it["source_id"])
            continue

        kept.append(it)
        if cu:
            seen_urls[cu] = it
        if nt:
            seen_titles[nt] = it
        if len(tokens) >= _MIN_TITLE_TOKENS:
            kept_token_sets.append((tokens, it))

    return kept, dropped


# --- top-level ---------------------------------------------------------------

def normalize(
    items: list[dict],
    *,
    vocab: dict[str, list[str]] | None = None,
    do_dedup: bool = True,
) -> tuple[list[dict], int]:
    """Enrich + dedup the merged Highlight list. Fails open to the input list.
    Returns (items, n_deduped)."""
    if not items:
        return items, 0
    try:
        v = vocab if vocab is not None else load_vocab()
        for it in items:
            enrich(it, v)
        if do_dedup:
            return dedup(items)
        return items, 0
    except Exception:
        log.exception("normalize failed; serving un-normalized items")
        return items, 0
