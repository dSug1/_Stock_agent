"""Module 8 — rescue classification (pure-compute).

Three rescue classes, per the user's M8 spec (D35):

  A — H1 fail (market-cap gate), but mcap ∈ [0, 2B]. Effectively widens
      H1's lower bound from $30M down to $0. The other gates (H2/H4/H6)
      must still pass; H3/H5 may or may not fail, but A doesn't care.

  B — H3 fail (`date_min ≥ snapshot + 14d` PASS, so H3 FAILS when the
      catalyst is too imminent or undated), AND H1 passes. Other gates
      (H2/H4/H6) must still pass.

  C — H5 fail (stage ∈ {phase1,2,3} AND type ∈ {Interim, Initial,
      Topline, Full Results, Conference}), AND H1 passes, AND
      next_catalyst_type IS NULL OR == 'Regulatory Decision' (per user's
      M8 scope decision — drops Submissions, EOP Meetings, phase0
      Conference Presentations as low-signal).

H4 (catalyst window already past) is INTENTIONALLY allowed — the user
chose to "trust Claude to handle" via HARD RULE #8 in the rescue prompt.
H2/H6 are NOT bypassed — H2 (no precision_tier) and H6 (delisted) stay
hard-excluded.

A catalyst may match multiple classes simultaneously (e.g. mcap < $30M
AND H3 fail). The resulting `rescue_class` string concatenates the class
letters in sorted order: 'A', 'B', 'C', 'AB', 'AC', 'BC', 'ABC'.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

# Per user's M8 design (Class C scope answer): only these next_catalyst_type
# values, OR NULL, qualify for C rescue. The high-noise types (Submission,
# End of Phase Meeting, phase0 Conference Presentation) are explicitly
# excluded — they're rarely binary near-term catalysts and Claude has no
# good scoring framework for them.
C_ELIGIBLE_TYPES = frozenset({
    "Regulatory Decision",   # PDUFA / approval — binary, material
    # NULL is also eligible — handled separately in classify_catalyst.
})

# H1 upper bound (matches module_6/filters.py::MARKET_CAP_MAX_USD). H1
# fails when mcap >= 2B OR mcap < 30M. Rescue A captures the LOWER tail
# only: any mcap that's known AND < 2B.
RESCUE_A_MCAP_MAX_USD = 2_000_000_000.0

RescueClass = Literal["A", "B", "C"]


@dataclass(frozen=True)
class RescueDecision:
    """Result of classifying one catalyst against the three rescue rules."""
    classes: tuple[RescueClass, ...]    # subset of ('A', 'B', 'C'), sorted
    rescued: bool                       # True iff classes is non-empty
    reason_notes: tuple[str, ...]       # short human-readable trace per class

    @property
    def rescue_class_str(self) -> str | None:
        """'A' / 'B' / 'C' / 'AB' / 'AC' / 'BC' / 'ABC', or None."""
        return "".join(self.classes) if self.classes else None


def _parse_fail_reasons(fail_reasons: Optional[str]) -> frozenset[str]:
    """Comma-separated 'H1,H3' → {'H1', 'H3'}. Tolerates whitespace."""
    if not fail_reasons:
        return frozenset()
    return frozenset(x.strip() for x in fail_reasons.split(",") if x.strip())


def classify_catalyst(
    *,
    fail_reasons: Optional[str],
    market_cap_usd: Optional[float],
    next_catalyst_type: Optional[str],
) -> RescueDecision:
    """Pure-compute classifier for one catalyst row.

    Inputs come from catalyst_scores (fail_reasons) + the best-available
    mcap (fundamentals.db.financials.market_cap_fdsc_usd, falling back to
    catalyst_snapshots.market_cap_usd) + catalyst_snapshots.next_catalyst_type.

    Returns the RescueDecision. Caller is responsible for ensuring this
    is called only on `hard_pass = 0` rows — passing hard-pass rows is
    valid (decision.rescued=False) but pointless.
    """
    fails = _parse_fail_reasons(fail_reasons)

    # Hard exclusions even from rescue: H2 (no timing precision at all)
    # and H6 (delisted ticker). H4 is intentionally allowed per user's
    # design — let Claude's HARD RULE #8 reject past catalysts.
    if "H2" in fails:
        return RescueDecision(classes=(), rescued=False,
                              reason_notes=("blocked by H2 (timing precision unknown)",))
    if "H6" in fails:
        return RescueDecision(classes=(), rescued=False,
                              reason_notes=("blocked by H6 (delisted ticker)",))

    classes: list[RescueClass] = []
    notes: list[str] = []

    # ── Class A: H1 fail, mcap in [0, 2B] ────────────────────────────
    h1_fails = "H1" in fails
    if h1_fails:
        if market_cap_usd is None:
            # User chose to include NULL mcap (rare — 1 row in current data).
            classes.append("A")
            notes.append("A: H1 fail, mcap=NULL (treated as small-cap rescue)")
        elif 0 <= market_cap_usd <= RESCUE_A_MCAP_MAX_USD:
            classes.append("A")
            notes.append(f"A: H1 fail, mcap=${market_cap_usd:,.0f} ≤ $2B")
        # else: mcap > $2B → stays excluded (out of A's scope).

    # ── Class B: H3 fail, H1 pass ────────────────────────────────────
    # H3 PASS means date_min >= snapshot + 14d. H3 FAIL means either the
    # catalyst is too imminent (< 14d out) or there's no parseable date
    # at all. Either case is rescue-worthy: Claude re-resolves the date.
    if "H3" in fails and not h1_fails:
        classes.append("B")
        notes.append("B: H3 fail (imminent/undated), H1 pass — Claude resolves date")

    # ── Class C: H5 fail, H1 pass, type ∈ allowlist or NULL ──────────
    if "H5" in fails and not h1_fails:
        t = next_catalyst_type
        if t is None or t in C_ELIGIBLE_TYPES:
            classes.append("C")
            notes.append(f"C: H5 fail (non-standard stage/type='{t or 'NULL'}')")
        # else: dropped per M8 scope decision (Submission/EOP/phase0/etc).

    classes_sorted = tuple(sorted(classes))
    return RescueDecision(
        classes=classes_sorted,
        rescued=bool(classes_sorted),
        reason_notes=tuple(notes),
    )


def classify_rows(
    rows: list[dict],
    *,
    mcap_lookup: dict[str, Optional[float]],
) -> list[tuple[dict, RescueDecision]]:
    """Bulk classifier.

    `rows` should be dicts with at minimum: ticker, fail_reasons,
    next_catalyst_type. `mcap_lookup` maps ticker → best-available mcap.
    Returns one (row, decision) pair per input row (in input order).
    """
    out: list[tuple[dict, RescueDecision]] = []
    for r in rows:
        decision = classify_catalyst(
            fail_reasons=r.get("fail_reasons"),
            market_cap_usd=mcap_lookup.get(r["ticker"]),
            next_catalyst_type=r.get("next_catalyst_type"),
        )
        out.append((r, decision))
    return out
