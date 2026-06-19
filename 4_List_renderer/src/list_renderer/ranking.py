"""Learning & ranking (M6, Phase 5) — the transparent weighted-feature model (D5).

`score(item) = Σ weight_f · feature_f` over a small, inspectable feature set. Base
weights come from `config/ranking.yaml`; the *learned* per-source / per-topic
affinities are fit offline by `scripts/4_learn_ranking.py` and read from the
`ranking_state` table. Deterministic user rules (D21: mute = hard filter, boost =
weight bump) and the seen-penalty (D22) apply on top.

Design notes:
- **Cold start (no signals).** A `position_prior` feature anchors items to their
  natural source/feed order, so with no interactions/interests/dates the board
  is essentially unchanged — learned signals then reorder it. (Also the recency
  proxy until M4 emits `published_at`.)
- **Fail open.** Any error (missing config, bad row) returns the items in their
  original order — ranking never blanks the board.
- **Trainable through churn.** Affinities are fit from `interactions.context_json`
  (the D19 snapshot), so they survive content/recipe changes.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .db import now_iso

log = logging.getLogger("4_render_list.ranking")

ROOT = Path(__file__).resolve().parents[2]          # 4_List_renderer/
DEFAULT_CONFIG = ROOT / "config" / "ranking.yaml"

# Mirrors config/ranking.yaml so the ranker still runs if the file is absent.
_DEFAULTS: dict[str, Any] = {
    "weights": {
        "position_prior": 1.2, "recency": 1.0, "source_affinity": 1.5,
        "interest_match": 2.0, "topic_affinity": 1.0, "length": 0.15,
        "seen_penalty": -0.8,
    },
    "recency": {"half_life_hours": 48},
    "signals": {
        "like": 3.0, "read_more": 2.0, "open": 1.0, "dwell_30s": 1.0,
        "scroll_past": -0.5, "hide": -3.0, "impression": 0.0,
    },
    "learn": {"min_interactions": 15, "affinity_scale": 6.0},
    "rules": {"mute_keywords": [], "boost_topics": []},
}


def load_config(path: Path | str | None = None) -> dict:
    """Load ranking.yaml, falling back to built-in defaults per key."""
    path = Path(path) if path else DEFAULT_CONFIG
    cfg = dict(_DEFAULTS)
    try:
        loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError:
        return {k: dict(v) if isinstance(v, dict) else v for k, v in _DEFAULTS.items()}
    out: dict[str, Any] = {}
    for key, default in _DEFAULTS.items():
        val = loaded.get(key, default)
        if isinstance(default, dict) and isinstance(val, dict):
            merged = dict(default); merged.update(val); out[key] = merged
        else:
            out[key] = val if val is not None else default
    return out


# --- learned state -----------------------------------------------------------

def load_affinities(conn: sqlite3.Connection, user_id: str) -> dict[str, float]:
    """All learned `ranking_state` rows for the user as {feature: weight}."""
    try:
        rows = conn.execute(
            "SELECT feature, weight FROM ranking_state WHERE user_id=?",
            (user_id,),
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {r["feature"]: r["weight"] for r in rows}


def load_interests(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    """Active declared interests (M5) used by the `interest_match` feature."""
    try:
        rows = conn.execute(
            "SELECT kind, value, weight FROM interests "
            "WHERE user_id=? AND status IN ('active','pending')",
            (user_id,),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


# --- features ----------------------------------------------------------------

def _recency_feature(published_at: str | None, half_life_hours: float, now: datetime) -> float:
    if not published_at:
        return 0.5                       # neutral when undated
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return 0.5
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_h = max(0.0, (now - dt).total_seconds() / 3600.0)
    return math.exp(-math.log(2) * age_h / max(1e-6, half_life_hours))


def _interest_feature(item: dict, interests: list[dict]) -> float:
    if not interests:
        return 0.0
    hay = " ".join(str(item.get(k) or "") for k in
                   ("title", "snippet", "site_name", "url_breadcrumb", "title_url")).lower()
    topics = [str(t).lower() for t in (item.get("topics") or [])]
    sid = str(item.get("source_id") or "").lower()
    total = 0.0
    for it in interests:
        val = str(it.get("value") or "").strip().lower()
        if not val:
            continue
        w = float(it.get("weight") or 1.0)
        kind = it.get("kind")
        if kind == "site":
            if val in hay or val in sid:
                total += w
        else:  # topic | query | ticker
            if val in hay or val in topics:
                total += w
    return min(2.0, total)               # cap so one interest can't dominate


def _topic_affinity_feature(item: dict, affinities: dict[str, float]) -> float:
    topics = item.get("topics") or []
    if not topics:
        return 0.0
    vals = [affinities.get(f"topic_affinity:{t}") for t in topics]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def score_item(
    item: dict,
    *,
    index: int,
    n_items: int,
    weights: dict,
    recency_cfg: dict,
    affinities: dict[str, float],
    interests: list[dict],
    seen: set[str],
    now: datetime,
    breakdown: bool = False,
) -> tuple[float, dict | None]:
    """Return (score, optional breakdown) for one item."""
    pos_prior = (n_items - index) / n_items if n_items else 0.0
    recency = _recency_feature(item.get("published_at"), recency_cfg.get("half_life_hours", 48), now)
    src_aff = affinities.get(f"source_affinity:{item.get('source_id')}", 0.0)
    interest = _interest_feature(item, interests)
    topic_aff = _topic_affinity_feature(item, affinities)
    snippet = item.get("snippet") or ""
    length = min(1.0, len(snippet) / 300.0)
    seen_f = 1.0 if item.get("id") in seen else 0.0

    feats = {
        "position_prior": pos_prior, "recency": recency,
        "source_affinity": src_aff, "interest_match": interest,
        "topic_affinity": topic_aff, "length": length, "seen_penalty": seen_f,
    }
    contribs = {f: weights.get(f, 0.0) * v for f, v in feats.items()}
    score = sum(contribs.values())
    if breakdown:
        return score, {"features": feats, "contributions": contribs, "total": score}
    return score, None


# --- rules (D21) -------------------------------------------------------------

def _apply_mute(items: list[dict], mute_keywords: list[str]) -> list[dict]:
    kws = [str(k).strip().lower() for k in (mute_keywords or []) if str(k).strip()]
    if not kws:
        return items
    kept = []
    for it in items:
        hay = (str(it.get("title") or "") + " " + str(it.get("snippet") or "")).lower()
        if any(k in hay for k in kws):
            continue
        kept.append(it)
    return kept


def _boost_for(item: dict, boost_topics: list) -> float:
    if not boost_topics:
        return 0.0
    hay = (str(item.get("title") or "") + " " + str(item.get("snippet") or "")).lower()
    topics = [str(t).lower() for t in (item.get("topics") or [])]
    total = 0.0
    for rule in boost_topics:
        if not isinstance(rule, dict):
            continue
        topic = str(rule.get("topic") or "").strip().lower()
        if topic and (topic in hay or topic in topics):
            total += float(rule.get("weight") or 0.0)
    return total


# --- top-level ---------------------------------------------------------------

def rank_results(
    conn: sqlite3.Connection,
    user_id: str,
    results: list[dict],
    *,
    seen: set[str] | None = None,
    config_path: Path | str | None = None,
    include_breakdown: bool = False,
) -> list[dict]:
    """Order `results` by predicted interest (descending). Mutates each item to
    add `score` (and `score_breakdown` if requested); fails open to the input
    order on any error."""
    if not results:
        return results
    try:
        cfg = load_config(config_path)
        weights = cfg["weights"]
        affinities = load_affinities(conn, user_id)
        interests = load_interests(conn, user_id)
        if seen is None:
            from . import interactions as _ix
            seen = _ix.seen_item_ids(conn, user_id=user_id)
        now = datetime.now(timezone.utc)
        rules = cfg.get("rules") or {}

        items = _apply_mute(list(results), rules.get("mute_keywords"))
        n = len(items)
        boost_topics = rules.get("boost_topics") or []
        for i, it in enumerate(items):
            score, bd = score_item(
                it, index=i, n_items=n, weights=weights,
                recency_cfg=cfg["recency"], affinities=affinities,
                interests=interests, seen=seen, now=now,
                breakdown=include_breakdown,
            )
            score += _boost_for(it, boost_topics)
            it["score"] = round(score, 6)
            if bd is not None:
                bd["boost"] = round(_boost_for(it, boost_topics), 6)
                it["score_breakdown"] = bd
        # Stable sort: equal scores keep their original (feed) order.
        items.sort(key=lambda r: r.get("score", 0.0), reverse=True)
        return items
    except Exception:                    # never blank the board on a ranking bug
        log.exception("ranking failed; serving unranked order")
        return results


# --- offline learning (4_learn_ranking.py) -----------------------------------

def _signal_value(action: str, dwell_ms: int | None, signals: dict) -> float:
    if action == "dwell":
        return float(signals.get("dwell_30s", 0.0)) if (dwell_ms or 0) >= 30000 else 0.0
    return float(signals.get(action, 0.0))


def fit_affinities(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    config_path: Path | str | None = None,
) -> dict[str, Any]:
    """Re-fit per-source and per-topic affinities from the interaction log and
    persist them to `ranking_state` (replacing the prior fit). Returns a report.

    Affinity = tanh(net_signal / scale), bounded to [-1, 1], where net_signal is
    the Σ of action signal weights (spec §8) attributed via each interaction's
    `context_json` snapshot (D19 — why we stored source_id/topics at event time).
    Below `learn.min_interactions` the fit is skipped (cold start stands, D5).
    """
    cfg = load_config(config_path)
    signals = cfg["signals"]
    min_n = int(cfg["learn"].get("min_interactions", 15))
    scale = float(cfg["learn"].get("affinity_scale", 6.0)) or 1.0

    rows = conn.execute(
        "SELECT item_id, action, dwell_ms, context_json FROM interactions "
        "WHERE user_id=?",
        (user_id,),
    ).fetchall()
    n_total = len(rows)
    if n_total < min_n:
        return {"fitted": False, "reason": "below min_interactions",
                "n_interactions": n_total, "min": min_n,
                "n_sources": 0, "n_topics": 0}

    src_net: dict[str, float] = {}
    topic_net: dict[str, float] = {}
    for r in rows:
        val = _signal_value(r["action"], r["dwell_ms"], signals)
        if val == 0.0:
            continue
        ctx = {}
        if r["context_json"]:
            try:
                ctx = json.loads(r["context_json"]) or {}
            except (ValueError, TypeError):
                ctx = {}
        sid = ctx.get("source_id")
        if sid:
            src_net[sid] = src_net.get(sid, 0.0) + val
        for t in (ctx.get("topics") or []):
            topic_net[t] = topic_net.get(t, 0.0) + val

    ts = now_iso()
    # Replace the prior learned rows for this user (full re-fit).
    conn.execute(
        "DELETE FROM ranking_state WHERE user_id=? AND "
        "(feature LIKE 'source_affinity:%' OR feature LIKE 'topic_affinity:%')",
        (user_id,),
    )
    rows_out = []
    for sid, net in src_net.items():
        rows_out.append((user_id, f"source_affinity:{sid}", math.tanh(net / scale), ts))
    for t, net in topic_net.items():
        rows_out.append((user_id, f"topic_affinity:{t}", math.tanh(net / scale), ts))
    conn.executemany(
        "INSERT INTO ranking_state (user_id, feature, weight, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, feature) DO UPDATE SET "
        "  weight=excluded.weight, updated_at=excluded.updated_at",
        rows_out,
    )
    conn.commit()
    return {
        "fitted": True, "n_interactions": n_total,
        "n_sources": len(src_net), "n_topics": len(topic_net),
        "source_affinity": {k: round(math.tanh(v / scale), 4) for k, v in src_net.items()},
        "topic_affinity": {k: round(math.tanh(v / scale), 4) for k, v in topic_net.items()},
    }
