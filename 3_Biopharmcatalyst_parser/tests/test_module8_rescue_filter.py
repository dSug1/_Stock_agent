"""Module 8 (D35) — rescue classification unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_8.rescue_filter import (  # noqa: E402
    RESCUE_A_MCAP_MAX_USD,
    classify_catalyst,
    classify_rows,
)


# ───────────────────── Class A: H1 small-cap rescue ─────────────────────


def test_class_a_h1_fail_sub_2b_is_rescued():
    d = classify_catalyst(
        fail_reasons="H1",
        market_cap_usd=25_000_000,        # sub-$30M, was H1-failed for being too small
        next_catalyst_type="Initial Data",
    )
    assert d.rescued
    assert d.classes == ("A",)
    assert d.rescue_class_str == "A"


def test_class_a_h1_fail_over_2b_stays_excluded():
    d = classify_catalyst(
        fail_reasons="H1",
        market_cap_usd=3_500_000_000,     # > $2B (big pharma) — NOT rescued
        next_catalyst_type="Initial Data",
    )
    assert not d.rescued
    assert d.classes == ()


def test_class_a_h1_fail_null_mcap_is_rescued():
    # User answer: include NULL mcap rows in A (rare, but useful).
    d = classify_catalyst(
        fail_reasons="H1",
        market_cap_usd=None,
        next_catalyst_type="Initial Data",
    )
    assert d.rescued
    assert d.classes == ("A",)


def test_class_a_boundary_at_2b_inclusive():
    # Spec: $0 ≤ mcap ≤ $2B is the rescue band.
    d = classify_catalyst(
        fail_reasons="H1",
        market_cap_usd=RESCUE_A_MCAP_MAX_USD,
        next_catalyst_type="Initial Data",
    )
    assert d.rescued


# ───────────────────── Class B: H3 rescue ────────────────────────────────


def test_class_b_h3_fail_h1_pass_is_rescued():
    d = classify_catalyst(
        fail_reasons="H3",                # imminent or undated catalyst
        market_cap_usd=500_000_000,       # H1 PASSES
        next_catalyst_type="Interim Data",
    )
    assert d.rescued
    assert d.classes == ("B",)


def test_class_b_blocked_when_h1_also_fails():
    # When H1 fails, only A applies (or nothing) — B is suppressed.
    d = classify_catalyst(
        fail_reasons="H1,H3",
        market_cap_usd=10_000_000_000,    # H1 fails BIG (over $2B), so not in A either
        next_catalyst_type="Interim Data",
    )
    assert not d.rescued


def test_class_b_with_h4_also_allowed():
    # User's call: "include H4 — trust Claude's HARD RULE #8 to handle"
    d = classify_catalyst(
        fail_reasons="H3,H4",
        market_cap_usd=300_000_000,
        next_catalyst_type="Interim Data",
    )
    assert d.rescued
    assert d.classes == ("B",)


# ───────────────────── Class C: H5 trimmed scope ────────────────────────


def test_class_c_regulatory_decision_is_rescued():
    d = classify_catalyst(
        fail_reasons="H5",
        market_cap_usd=900_000_000,
        next_catalyst_type="Regulatory Decision",   # explicit allow
    )
    assert d.rescued
    assert d.classes == ("C",)


def test_class_c_null_type_is_rescued():
    d = classify_catalyst(
        fail_reasons="H5",
        market_cap_usd=900_000_000,
        next_catalyst_type=None,                    # NULL — allowed
    )
    assert d.rescued
    assert d.classes == ("C",)


def test_class_c_submission_is_dropped():
    # User's trimmed scope: drop Submission (not a near-term binary).
    d = classify_catalyst(
        fail_reasons="H5",
        market_cap_usd=900_000_000,
        next_catalyst_type="Submission",
    )
    assert not d.rescued


def test_class_c_end_of_phase_meeting_dropped():
    d = classify_catalyst(
        fail_reasons="H5",
        market_cap_usd=900_000_000,
        next_catalyst_type="End of Phase Meeting",
    )
    assert not d.rescued


def test_class_c_phase0_conference_dropped():
    d = classify_catalyst(
        fail_reasons="H5",
        market_cap_usd=900_000_000,
        next_catalyst_type="Conference Presentation",
    )
    assert not d.rescued


# ───────────────────── Combined / overlap classes ───────────────────────


def test_combined_bc_when_h3_and_h5_both_fail():
    d = classify_catalyst(
        fail_reasons="H3,H5",
        market_cap_usd=500_000_000,
        next_catalyst_type="Regulatory Decision",
    )
    assert d.rescued
    assert d.classes == ("B", "C")
    assert d.rescue_class_str == "BC"


def test_combined_abc_when_h1_h3_h5_all_fail_and_smallcap():
    d = classify_catalyst(
        fail_reasons="H1,H3,H5",
        market_cap_usd=20_000_000,        # tiny — A applies
        next_catalyst_type="Regulatory Decision",
    )
    # H1 fails AND H1-small-cap → A applies; H3 fail but H1 also fails → B
    # suppressed; same for C. So only A.
    assert d.classes == ("A",)


# ───────────────────── Hard blocks: H2 + H6 ─────────────────────────────


def test_h2_blocks_all_rescue():
    # H2 means timing precision is 'unknown' — Claude has nothing to anchor on.
    d = classify_catalyst(
        fail_reasons="H1,H2,H3,H5",
        market_cap_usd=20_000_000,        # would otherwise qualify for A
        next_catalyst_type="Regulatory Decision",
    )
    assert not d.rescued
    assert "H2" in d.reason_notes[0]


def test_h6_blocks_all_rescue():
    d = classify_catalyst(
        fail_reasons="H6",
        market_cap_usd=500_000_000,
        next_catalyst_type="Initial Data",
    )
    assert not d.rescued
    assert "H6" in d.reason_notes[0]


# ───────────────────── Bulk classifier ──────────────────────────────────


def test_classify_rows_bulk():
    rows = [
        {"ticker": "AAA", "fail_reasons": "H1",   "next_catalyst_type": "Initial Data"},
        {"ticker": "BBB", "fail_reasons": "H3",   "next_catalyst_type": "Interim Data"},
        {"ticker": "CCC", "fail_reasons": "H5",   "next_catalyst_type": "Regulatory Decision"},
        {"ticker": "DDD", "fail_reasons": "H2",   "next_catalyst_type": "Initial Data"},
    ]
    mcap = {"AAA": 25_000_000, "BBB": 400_000_000, "CCC": 800_000_000, "DDD": 100_000_000}
    results = classify_rows(rows, mcap_lookup=mcap)
    decisions = [d.rescue_class_str for _, d in results]
    assert decisions == ["A", "B", "C", None]


# ───────────────────── No-fail row is never rescued ─────────────────────


def test_passing_row_is_never_rescued():
    d = classify_catalyst(
        fail_reasons=None,
        market_cap_usd=400_000_000,
        next_catalyst_type="Initial Data",
    )
    assert not d.rescued
    assert d.rescue_class_str is None
