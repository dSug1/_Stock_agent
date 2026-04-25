"""Three-tier routing classification per ticker (D39).

Each ticker in the Module 6 feed is routed into one of three tiers BEFORE
dispatch:

    Tier A — exact cache hit ($0, no API call)
    Tier B — light refresh (~15-20% of full cost)
    Tier C — full scoring (full cost)

The classifier looks at the most recent llm_scores row per horizon (across
all quarters), compares against the current pack hash + age, and returns the
ticker's effective tier — the WORST of its two horizons (because a single
API call covers both, so if either horizon needs Tier C the whole ticker
needs Tier C).

Spec: spec/module_6_spec.md § Three-tier routing.
Decision: D39.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .scores_db import query_priors_for_ticker


class Tier(str, Enum):
    A = "A"
    B = "B"
    C = "C"


@dataclass(frozen=True)
class TierClassification:
    """Per-ticker tier assignment plus per-horizon details for the report."""

    ticker: str
    overall_tier: Tier
    tier_3mo: Tier
    tier_12mo: Tier
    days_since_3mo: Optional[int]
    days_since_12mo: Optional[int]
    pack_hash_match_3mo: bool
    pack_hash_match_12mo: bool


def _days_since(scored_at_iso: Optional[str], now_utc: datetime) -> Optional[int]:
    if not scored_at_iso:
        return None
    try:
        scored = datetime.fromisoformat(scored_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if scored.tzinfo is None:
        scored = scored.replace(tzinfo=timezone.utc)
    return max(0, (now_utc - scored).days)


def _classify_one_horizon(
    prior: Optional[sqlite3.Row],
    current_quarter: str,
    current_pack_hash: str,
    refresh_threshold_days: int,
    now_utc: datetime,
) -> tuple[Tier, Optional[int], bool]:
    """Classify one horizon in isolation. Returns (tier, days_since_prior, pack_hash_match)."""
    if prior is None:
        return Tier.C, None, False

    days = _days_since(prior["scored_at"], now_utc)
    pack_hash_match = (
        prior["pack_source_rank_hash"] == current_pack_hash
        and prior["quarter"] == current_quarter
    )

    if pack_hash_match:
        return Tier.A, days, True

    # Quarter changed OR pack hash changed → check refresh window
    if days is not None and days <= refresh_threshold_days:
        return Tier.B, days, False

    return Tier.C, days, False


def classify_ticker_tier(
    conn: sqlite3.Connection,
    ticker: str,
    current_quarter: str,
    current_pack_hash: str,
    prompt_version: str,
    model: str,
    refresh_threshold_days_3mo: int,
    refresh_threshold_days_12mo: int,
    now_utc: Optional[datetime] = None,
) -> TierClassification:
    """Classify one ticker's effective tier per D39.

    The ticker's overall_tier is max(tier_3mo, tier_12mo) under the order
    A < B < C — because a single LLM call covers both horizons, the call's
    cost is determined by the more expensive horizon's tier.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    priors = query_priors_for_ticker(conn, ticker, prompt_version, model)

    tier_3mo, days_3mo, hash_match_3mo = _classify_one_horizon(
        priors["3mo"], current_quarter, current_pack_hash,
        refresh_threshold_days_3mo, now_utc,
    )
    tier_12mo, days_12mo, hash_match_12mo = _classify_one_horizon(
        priors["12mo"], current_quarter, current_pack_hash,
        refresh_threshold_days_12mo, now_utc,
    )

    # Overall tier = worst of the two (C > B > A).
    order = {Tier.A: 0, Tier.B: 1, Tier.C: 2}
    overall = tier_3mo if order[tier_3mo] >= order[tier_12mo] else tier_12mo

    return TierClassification(
        ticker=ticker,
        overall_tier=overall,
        tier_3mo=tier_3mo,
        tier_12mo=tier_12mo,
        days_since_3mo=days_3mo,
        days_since_12mo=days_12mo,
        pack_hash_match_3mo=hash_match_3mo,
        pack_hash_match_12mo=hash_match_12mo,
    )
