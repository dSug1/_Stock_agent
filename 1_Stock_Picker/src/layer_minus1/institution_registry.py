from __future__ import annotations

import sqlite3
from types import MappingProxyType
from typing import Optional

from database.db import now_iso


# Immutable spec-derived registry.
# Format: (name, tier, tier_label, multiplier, primary_coverage)
_INSTITUTIONS: tuple[tuple[str, str, str, float, Optional[str]], ...] = (
    ("Baker Bros. Advisors",         "1A",
     "Sector specialist, deep diligence", 4.0,
     "Biotech — clinical stage, long hold through binary events"),
    ("RA Capital Management",        "1A",
     "Sector specialist, deep diligence", 4.0,
     "Biotech/medtech — public/private crossover, early clinical"),
    ("Perceptive Advisors",          "1A",
     "Sector specialist, deep diligence", 4.0,
     "Clinical-stage biotech, oncology and rare disease"),
    ("Boxer Capital (Tavistock)",    "1A",
     "Sector specialist, deep diligence", 4.0,
     "Small/microcap biotech"),
    ("Deerfield Management",         "1A",
     "Sector specialist, deep diligence", 4.0,
     "Healthcare — equity and royalty structures"),
    ("Goehring & Rozencwajg",        "1A",
     "Sector specialist, deep diligence", 4.0,
     "Natural resources, commodity royalties"),
    ("OrbiMed Advisors",             "1A",
     "Sector specialist, deep diligence", 4.0, None),
    ("BVF Inc.",                     "1A",
     "Sector specialist, deep diligence", 4.0, None),
    ("RTW Investments",              "1A",
     "Sector specialist, deep diligence", 4.0, None),
    ("Redmile Group",                "1A",
     "Sector specialist, deep diligence", 4.0, None),
    ("Cormorant Asset Management",   "1A",
     "Sector specialist, deep diligence", 4.0, None),
    ("Sio Capital Management",       "1A",
     "Sector specialist, deep diligence", 4.0, None),
    ("ARCH Venture Partners",        "1A",
     "Sector specialist, deep diligence", 4.0, None),
    ("Samsara BioCapital",           "1A",
     "Sector specialist, deep diligence", 4.0, None),
    ("Sofinnova Partners",           "1A",
     "Sector specialist, deep diligence", 4.0, None),

    ("Baupost Group",                "1B",
     "High-conviction generalist superinvestor", 3.5,
     "Deep value, cross-sector distress"),
    ("Pershing Square",              "1B",
     "High-conviction generalist superinvestor", 3.5,
     "Concentrated activist, cross-sector"),
    ("Appaloosa Management",         "1B",
     "High-conviction generalist superinvestor", 3.5,
     "Macro-aware, cross-sector"),
    ("Third Point",                  "1B",
     "High-conviction generalist superinvestor", 3.5,
     "Activist, cross-sector, corporate events"),
    ("Berkshire Hathaway",           "1B",
     "High-conviction generalist superinvestor", 3.5,
     "Consumer staples, financials, energy, insurance"),

    ("Greenlight Capital",           "2A",
     "Generalist deep-value, concentrated", 2.5, None),
    ("Gotham Asset Management",      "2A",
     "Generalist deep-value, concentrated", 2.5, None),
    ("Ariel Investments",            "2A",
     "Generalist deep-value, concentrated", 2.5, None),
    ("Oakmark Funds",                "2A",
     "Generalist deep-value, concentrated", 2.5, None),

    ("Coatue Management",            "2B",
     "Sector specialist, broader mandate", 2.0, None),
    ("Whale Rock Capital",           "2B",
     "Sector specialist, broader mandate", 2.0, None),
    ("Horizon Kinetics",             "2B",
     "Sector specialist, broader mandate", 2.0, None),
    ("Orbis Investment Management",  "2B",
     "Sector specialist, broader mandate", 2.0, None),

    ("Fidelity active funds",        "3",
     "Quality institutional, active management", 1.0, None),
    ("T. Rowe Price active",         "3",
     "Quality institutional, active management", 1.0, None),
    ("Wellington Management active", "3",
     "Quality institutional, active management", 1.0, None),
    ("Royce & Associates",           "3",
     "Quality institutional, active management", 1.0, None),

    ("Vanguard index",               "4",
     "Large passive / index", 0.0, None),
    ("BlackRock iShares",            "4",
     "Large passive / index", 0.0, None),
    ("SPDR index products",          "4",
     "Large passive / index", 0.0, None),
)


TIER_MULTIPLIERS: "MappingProxyType[str, float]" = MappingProxyType({
    "1A": 4.0,
    "1B": 3.5,
    "2A": 2.5,
    "2B": 2.0,
    "3":  1.0,
    "4":  0.0,
})

VALID_TIERS: frozenset[str] = frozenset(TIER_MULTIPLIERS)


def _row_to_dict(
    row: tuple[str, str, str, float, Optional[str]],
) -> dict:
    name, tier, tier_label, multiplier, coverage = row
    return {
        "name": name,
        "tier": tier,
        "tier_label": tier_label,
        "multiplier": multiplier,
        "primary_coverage": coverage,
    }


def get_all_institutions() -> list[dict]:
    return [_row_to_dict(r) for r in _INSTITUTIONS]


def get_institutions_by_tier(tier: str) -> list[dict]:
    if tier not in VALID_TIERS:
        raise ValueError(
            f"unknown tier: {tier!r} "
            f"(expected one of {sorted(VALID_TIERS)})"
        )
    return [_row_to_dict(r) for r in _INSTITUTIONS if r[1] == tier]


def get_institution_by_name(name: str) -> Optional[dict]:
    for r in _INSTITUTIONS:
        if r[0] == name:
            return _row_to_dict(r)
    return None


def get_multiplier_for_institution(name: str) -> float:
    inst = get_institution_by_name(name)
    if inst is None:
        raise KeyError(f"unknown institution: {name!r}")
    return float(inst["multiplier"])


def seed_institutions(conn: sqlite3.Connection) -> int:
    ts = now_iso()
    inserted = 0
    for name, tier, tier_label, mult, coverage in _INSTITUTIONS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO institutions "
            "(name, tier, tier_label, multiplier, primary_coverage, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, tier, tier_label, mult, coverage, ts, ts),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted
