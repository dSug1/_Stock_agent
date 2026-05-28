"""Module 7 — catalyst-identity cache (D17).

Decides whether a candidate catalyst was already deep-dived under
**unchanged identity** and therefore can skip the Anthropic call.

Rule (user-locked 2026-05-28):

    SKIP the API call when the catalyst's
        (drug, stage, next_catalyst_type, catalyst_date)
    is unchanged versus the most recent successful deep_dive for the
    same (ticker, drug, nct_number, next_catalyst_type).

    DISPATCH the API call when any of those four fields has changed —
    OR when there's no prior successful row — OR when the user passes
    `--force-refresh`.

No TTL. Identity is the only criterion (per the user's spec). drug,
nct_number, ticker, next_catalyst_type are already in the PK, so a
change to any of them produces a new PK → no prior row → automatic
cache miss. The signature column therefore only needs to capture
stage + catalyst_date (the two non-PK fields), but we include all four
for self-contained readability + future-proofing.

`prompt_version` match is also enforced — a YAML edit (modifier tuning,
prompt rewrite) bumps the SHA-7 and invalidates the cache wholesale.
This is on top of identity matching, not in place of it.

Spec: spec/module_7_spec.md §5.12.
Decisions: spec/decisions.md § D17.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


# Field separator chosen to be ASCII-safe and *unlikely* to appear inside
# any of the four fields. Drug/catalyst-type names contain `:` and `,`
# but not vertical bars in practice.
_SIG_DELIM = "|"


def compute_catalyst_signature(
    *,
    drug: str,
    stage: Optional[str],
    next_catalyst_type: str,
    catalyst_date_iso: Optional[str],
) -> str:
    """Build the catalyst-identity signature used for cache lookup.

    All four fields are normalised (str(), lower-cased, stripped) so
    cosmetic differences (trailing spaces, case) don't invalidate the
    cache. None values become the literal string `""`.
    """
    def _norm(v) -> str:
        if v is None:
            return ""
        return str(v).strip().lower()

    return _SIG_DELIM.join((
        _norm(drug),
        _norm(stage),
        _norm(next_catalyst_type),
        _norm(catalyst_date_iso),
    ))


@dataclass(frozen=True)
class CacheLookup:
    """Result of a cache-hit check for one candidate."""
    is_hit: bool
    prior_run_id: Optional[int]
    prior_snapshot_date: Optional[str]
    reason: str          # 'identity_match' | 'no_prior_row' | 'identity_changed' | 'prompt_version_changed' | 'prior_row_failed'


def lookup_cache(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    drug: str,
    nct_number: str,
    next_catalyst_type: str,
    current_signature: str,
    current_prompt_version: str,
) -> CacheLookup:
    """Return the cache decision for one candidate catalyst.

    Looks for the MOST RECENT (highest run_id) successful prior deep_dives
    row matching the four PK identity columns (ticker, drug, nct_number,
    next_catalyst_type). "Successful" means `p_clinical IS NOT NULL`
    (parse-error rows shouldn't grant cache hits — they need a retry).

    Returns ``CacheLookup(is_hit=True, …)`` only when:
      • A prior successful row exists, AND
      • Its ``catalyst_signature`` matches ``current_signature``, AND
      • Its ``prompt_version`` matches ``current_prompt_version``.

    Otherwise ``is_hit=False`` with the disqualifying reason carried in
    the ``.reason`` attribute so the caller can show it in the dispatch
    summary.
    """
    row = conn.execute(
        """
        SELECT run_id, snapshot_date, catalyst_signature, prompt_version,
               p_clinical
        FROM deep_dives
        WHERE ticker = ?
          AND drug = ?
          AND nct_number = ?
          AND next_catalyst_type = ?
        ORDER BY run_id DESC
        LIMIT 1
        """,
        (ticker, drug, nct_number, next_catalyst_type),
    ).fetchone()

    if row is None:
        return CacheLookup(False, None, None, "no_prior_row")
    # sqlite3.Row supports __getitem__; index by name for clarity.
    prior_p = row["p_clinical"] if hasattr(row, "keys") else row[4]
    if prior_p is None:
        return CacheLookup(
            False, row["run_id"], row["snapshot_date"], "prior_row_failed",
        )
    prior_sig = row["catalyst_signature"] if hasattr(row, "keys") else row[2]
    if prior_sig != current_signature:
        return CacheLookup(
            False, row["run_id"], row["snapshot_date"], "identity_changed",
        )
    prior_pv = row["prompt_version"] if hasattr(row, "keys") else row[3]
    if prior_pv != current_prompt_version:
        return CacheLookup(
            False, row["run_id"], row["snapshot_date"], "prompt_version_changed",
        )
    return CacheLookup(
        True, row["run_id"], row["snapshot_date"], "identity_match",
    )


def partition_feed_by_cache(
    conn: sqlite3.Connection,
    candidates: list[dict],
    *,
    current_prompt_version: str,
    force_refresh: bool = False,
) -> tuple[list[dict], list[dict]]:
    """Split a candidate feed into (to_dispatch, skipped_cache_hits).

    Each ``candidates[i]`` must carry these keys:
        ticker, drug, nct_number, next_catalyst_type,
        stage, catalyst_date_iso       (for signature computation)

    Adds two keys to every candidate before returning:
        catalyst_signature       — the computed signature for this run
        cache_lookup             — CacheLookup result for audit

    When ``force_refresh=True``, every candidate lands in ``to_dispatch``
    with `cache_lookup.is_hit = False` and `cache_lookup.reason =
    'force_refresh'` so the dispatch summary still shows what *would*
    have hit cache.
    """
    to_dispatch: list[dict] = []
    skipped: list[dict] = []
    for cand in candidates:
        sig = compute_catalyst_signature(
            drug=cand["drug"],
            stage=cand.get("stage"),
            next_catalyst_type=cand["next_catalyst_type"],
            catalyst_date_iso=cand.get("catalyst_date_iso"),
        )
        cand["catalyst_signature"] = sig
        if force_refresh:
            cand["cache_lookup"] = CacheLookup(
                False, None, None, "force_refresh",
            )
            to_dispatch.append(cand)
            continue
        lookup = lookup_cache(
            conn,
            ticker=cand["ticker"], drug=cand["drug"],
            nct_number=cand["nct_number"],
            next_catalyst_type=cand["next_catalyst_type"],
            current_signature=sig,
            current_prompt_version=current_prompt_version,
        )
        cand["cache_lookup"] = lookup
        (skipped if lookup.is_hit else to_dispatch).append(cand)
    return to_dispatch, skipped
