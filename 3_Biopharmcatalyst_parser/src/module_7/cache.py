"""Module 7 — catalyst- and drug-identity cache (D17 + D23).

Decides whether a candidate catalyst was already deep-dived under
**unchanged identity** and therefore can skip the Anthropic call.

Two granularities:

  • **catalyst_signature** (D17) — per-row identity for the original
    one-row-per-catalyst world: hashes (drug, stage, next_catalyst_type,
    catalyst_date).

  • **drug_signature** (D23) — per-DRUG identity used by the new
    dedup-by-drug dispatcher: hashes (drug, stage, [sorted list of
    (catalyst_type, catalyst_date) for every catalyst of this drug in the
    current snapshot]). A change in ANY catalyst's date/type, or the
    addition/removal of any catalyst from the drug's set, changes the
    drug_signature — so the D17 invariant ("re-run if the catalyst
    changed") is preserved at drug granularity.

Rule:

    SKIP the API call when there's a successful prior deep_dive row for
    THIS (ticker, drug) with matching drug_signature AND prompt_version.

    DISPATCH when no prior row, signature differs, prompt_version
    differs, prior row failed (p_clinical is NULL), or --force-refresh.

No TTL. Identity is the only criterion.

`prompt_version` match is also enforced — a YAML edit (modifier tuning,
prompt rewrite) bumps the SHA-7 and invalidates the cache wholesale.

Spec: spec/module_7_spec.md §5.12.
Decisions: spec/decisions.md § D17 + § D23.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


# Field separator chosen to be ASCII-safe and *unlikely* to appear inside
# any of the four fields. Drug/catalyst-type names contain `:` and `,`
# but not vertical bars in practice.
_SIG_DELIM = "|"


def _norm(v) -> str:
    if v is None:
        return ""
    return str(v).strip().lower()


def compute_catalyst_signature(
    *,
    drug: str,
    stage: Optional[str],
    next_catalyst_type: str,
    catalyst_date_iso: Optional[str],
) -> str:
    """Build the per-catalyst identity signature (D17).

    All four fields are normalised (str(), lower-cased, stripped) so
    cosmetic differences (trailing spaces, case) don't invalidate the
    cache. None values become the literal string `""`.
    """
    return _SIG_DELIM.join((
        _norm(drug),
        _norm(stage),
        _norm(next_catalyst_type),
        _norm(catalyst_date_iso),
    ))


def compute_drug_signature(
    *,
    drug: str,
    stage: Optional[str],
    catalysts: list[tuple[Optional[str], Optional[str]]],
) -> str:
    """Build the per-drug identity signature (D23).

    `catalysts` is a list of `(next_catalyst_type, catalyst_date_iso)`
    tuples — one per (ticker, drug, nct_number, next_catalyst_type) row
    in the same snapshot. Order-insensitive.

    The signature changes when:
      • drug name changes
      • stage changes
      • any catalyst is added or removed from the drug's set
      • any catalyst's date or type changes

    This preserves the D17 invariant ("re-run if the catalyst changed")
    at drug granularity, then lets the dispatcher dedupe one API call
    across all catalysts of the same drug.
    """
    cat_tuples = sorted(
        (_norm(ct), _norm(cd)) for (ct, cd) in catalysts
    )
    cat_blob = ",".join(f"{ct}~{cd}" for ct, cd in cat_tuples)
    return _SIG_DELIM.join((_norm(drug), _norm(stage), cat_blob))


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


def lookup_drug_cache(
    conn: sqlite3.Connection,
    *,
    ticker: str,
    drug: str,
    current_drug_signature: str,
    current_prompt_version: str,
) -> CacheLookup:
    """D23 drug-level cache check.

    Looks for ANY successful prior deep_dives row for (ticker, drug) with
    matching drug_signature and prompt_version. If found, the whole drug
    group is a cache hit and the dispatcher skips it.
    """
    row = conn.execute(
        """
        SELECT run_id, snapshot_date, drug_signature, prompt_version,
               p_clinical
        FROM deep_dives
        WHERE ticker = ?
          AND drug = ?
          AND p_clinical IS NOT NULL
          AND drug_signature IS NOT NULL
        ORDER BY run_id DESC
        LIMIT 1
        """,
        (ticker, drug),
    ).fetchone()

    if row is None:
        return CacheLookup(False, None, None, "no_prior_row")
    prior_sig = row["drug_signature"] if hasattr(row, "keys") else row[2]
    if prior_sig != current_drug_signature:
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


def group_candidates_by_drug(candidates: list[dict]) -> list[dict]:
    """D23 — collapse one-row-per-catalyst into one entry per (ticker, drug).

    Each output dict carries:
      ticker, drug, stage          (taken from the first member; stages
                                    within a drug rarely differ)
      members        — list of original candidate dicts
      catalysts      — list of (next_catalyst_type, catalyst_date_iso)
      drug_signature — D23 hash
      anchor         — the member chosen to source the Anthropic pack
                       (deterministic: earliest catalyst_date, then
                        lexicographic by (nct_number, catalyst_type))

    Members are returned in the original input order (stable group).
    """
    groups: dict[tuple[str, str], dict] = {}
    for c in candidates:
        key = (c["ticker"], c["drug"])
        g = groups.setdefault(key, {
            "ticker": c["ticker"],
            "drug":   c["drug"],
            "stage":  c.get("stage"),
            "members": [],
        })
        g["members"].append(c)

    out: list[dict] = []
    for g in groups.values():
        cats = [(m.get("next_catalyst_type"), m.get("catalyst_date_iso"))
                for m in g["members"]]
        g["catalysts"] = cats
        g["drug_signature"] = compute_drug_signature(
            drug=g["drug"], stage=g["stage"], catalysts=cats,
        )
        # Anchor: earliest defined catalyst_date, then lex (nct_num, type)
        def _anchor_key(m: dict) -> tuple:
            d = m.get("catalyst_date_iso") or "9999-12-31"
            return (d, m.get("nct_number") or "", m.get("next_catalyst_type") or "")
        g["anchor"] = min(g["members"], key=_anchor_key)
        out.append(g)
    return out


def partition_drug_groups_by_cache(
    conn: sqlite3.Connection,
    groups: list[dict],
    *,
    current_prompt_version: str,
    force_refresh: bool = False,
) -> tuple[list[dict], list[dict]]:
    """D23 split a list of drug groups into (to_dispatch, skipped).

    Each group dict must carry: ticker, drug, drug_signature.
    Adds `cache_lookup` to every group.
    """
    to_dispatch: list[dict] = []
    skipped: list[dict] = []
    for g in groups:
        if force_refresh:
            g["cache_lookup"] = CacheLookup(False, None, None, "force_refresh")
            to_dispatch.append(g)
            continue
        lookup = lookup_drug_cache(
            conn,
            ticker=g["ticker"], drug=g["drug"],
            current_drug_signature=g["drug_signature"],
            current_prompt_version=current_prompt_version,
        )
        g["cache_lookup"] = lookup
        (skipped if lookup.is_hit else to_dispatch).append(g)
    return to_dispatch, skipped


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
