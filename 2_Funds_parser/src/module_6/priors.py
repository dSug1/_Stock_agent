"""Cross-run cache lookups for prompt injection (D36 / D37).

Two functions, each returning a list of dicts ready to be formatted into the
per-ticker user message:

    query_prior_research(conn, ticker, lookback_days)
        Returns rows from web_search_cache whose last_seen_date is within
        lookback_days. Used to inject a "## Prior research" block.

    query_prior_thesis(conn, ticker, prompt_version, model, max_per_horizon)
        Returns up to N most-recent llm_scores rows per horizon for a ticker.
        Used to inject a "## Prior thesis" block.

Plus formatting helpers that build the actual markdown blocks.

Spec: spec/module_6_spec.md § Cross-run caches.
Decisions: D36 (web_search cache + injection), D37 (prior-thesis injection).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# Prior research (web_search_cache)
# ---------------------------------------------------------------------------


def query_prior_research(
    conn: sqlite3.Connection,
    ticker: str,
    lookback_days: int = 180,
) -> list[dict]:
    """Return cached web_search results for a ticker within lookback_days."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT url, title, content, content_length, domain,
               published_date, first_seen_date, last_seen_date,
               search_query, seen_count
        FROM web_search_cache
        WHERE ticker = ?
          AND (last_seen_date IS NULL
               OR julianday('now') - julianday(last_seen_date) <= ?)
        ORDER BY last_seen_date DESC, published_date DESC
        """,
        (ticker, lookback_days),
    ).fetchall()
    return [dict(r) for r in rows]


def format_prior_research_block(prior_rows: list[dict]) -> str:
    """Render the cached search results as a `## Prior research` markdown block.

    Returns empty string if no rows, so callers can do
    ``user_msg += format_prior_research_block(...)`` safely.
    """
    if not prior_rows:
        return ""
    lines = [
        "",
        "## Prior research",
        "Cached web_search results from previous Module 6 runs on this ticker.",
        "Use as a starting point. Issue new searches only for events AFTER the most",
        "recent prior date below, or for gaps these snippets do not cover.",
        "",
    ]
    for r in prior_rows[:30]:                         # cap injected items
        snippet = (r.get("content") or "")[:500].replace("\n", " ")
        title = (r.get("title") or "").replace("\n", " ")
        date = r.get("published_date") or r.get("first_seen_date") or "n/a"
        domain = r.get("domain") or "n/a"
        lines.append(f"- [{title}] ({domain}, {date}): {snippet}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prior thesis (llm_scores)
# ---------------------------------------------------------------------------


def query_prior_thesis(
    conn: sqlite3.Connection,
    ticker: str,
    prompt_version: str,
    model: str,
    max_per_horizon: int = 2,
) -> dict[str, list[dict]]:
    """Return up to N most-recent llm_scores rows per horizon for a ticker.

    Cross-quarter — a 12mo thesis from last quarter is still relevant context
    for this quarter's scoring, per D37.
    """
    conn.row_factory = sqlite3.Row
    out: dict[str, list[dict]] = {"3mo": [], "12mo": []}
    for horizon in ("3mo", "12mo"):
        rows = conn.execute(
            """
            SELECT scored_at, model, prompt_version, quarter,
                   target_price_usd, time_to_catalyst_weeks, probability,
                   catalyst_type, catalyst_detail, thesis_summary
            FROM llm_scores
            WHERE ticker = ? AND horizon = ?
              AND prompt_version = ? AND model = ?
            ORDER BY scored_at DESC
            LIMIT ?
            """,
            (ticker, horizon, prompt_version, model, max_per_horizon),
        ).fetchall()
        out[horizon] = [dict(r) for r in rows]
    return out


def format_prior_thesis_block(prior_thesis: dict[str, list[dict]]) -> str:
    """Render prior thesis rows as a `## Prior thesis` markdown block."""
    has_3mo = bool(prior_thesis.get("3mo"))
    has_12mo = bool(prior_thesis.get("12mo"))
    if not (has_3mo or has_12mo):
        return ""
    lines = [
        "",
        "## Prior thesis",
        "Your prior estimates on this ticker from earlier runs. Continuity",
        "reference, NOT anchor — justify any continuation with current evidence",
        "and state what changed when you update.",
        "",
    ]
    for horizon_key, label in (("3mo", "near_term_3mo"), ("12mo", "long_term_12mo")):
        for r in prior_thesis.get(horizon_key, []):
            scored_at = r.get("scored_at", "")
            model = r.get("model", "")
            pv = r.get("prompt_version", "")
            tgt = r.get("target_price_usd")
            wks = r.get("time_to_catalyst_weeks")
            prob = r.get("probability")
            cat = r.get("catalyst_type", "")
            detail = r.get("catalyst_detail", "")
            summary = (r.get("thesis_summary") or "").replace("\n", " ")
            tgt_s = f"${tgt:.2f}" if isinstance(tgt, (int, float)) else "n/a"
            prob_s = f"{prob:.2f}" if isinstance(prob, (int, float)) else "n/a"
            lines.append(
                f"- {scored_at} [{model}, {pv}] {label}: target={tgt_s} / "
                f"{wks}w / catalyst={cat} ({detail}) / probability={prob_s}"
            )
            lines.append(f"  Thesis: \"{summary}\"")
    lines.append("")
    return "\n".join(lines)
