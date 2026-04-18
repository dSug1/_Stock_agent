from __future__ import annotations

import sqlite3
from types import MappingProxyType
from typing import Optional

from database.db import now_iso


# Immutable spec-derived registry. CIKs verified against EDGAR
# full-text 13F-HR search and stored zero-padded to 10 digits.
_INSTITUTIONS: tuple[dict, ...] = (
    {"name": "Baker Bros. Advisors", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0,
     "primary_coverage":
         "Biotech — clinical stage, long hold through binary events",
     "cik": "0001263508",
     "edgar_name": "Baker Bros. Advisors LP"},
    {"name": "RA Capital Management", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0,
     "primary_coverage":
         "Biotech/medtech — public/private crossover, early clinical",
     "cik": "0001346824",
     "edgar_name": "RA Capital Management L.P."},
    {"name": "Perceptive Advisors", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0,
     "primary_coverage":
         "Clinical-stage biotech, oncology and rare disease",
     "cik": "0001224962",
     "edgar_name": "Perceptive Advisors LLC"},
    {"name": "Boxer Capital (Tavistock)", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0,
     "primary_coverage": "Small/microcap biotech",
     "cik": "0002018299",
     "edgar_name": "Boxer Capital Management, LLC"},
    {"name": "Deerfield Management", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0,
     "primary_coverage":
         "Healthcare — equity and royalty structures",
     "cik": "0001009258",
     "edgar_name": "Deerfield Management Company, L.P."},
    {"name": "Goehring & Rozencwajg", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0,
     "primary_coverage":
         "Natural resources, commodity royalties",
     "cik": "0001863154",
     "edgar_name": "Goehring & Rozencwajg Associates, LLC"},
    {"name": "OrbiMed Advisors", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001055951", "edgar_name": "OrbiMed Advisors LLC"},
    {"name": "BVF Inc.", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001056807", "edgar_name": "BVF Inc/IL"},
    {"name": "RTW Investments", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001493215", "edgar_name": "RTW Investments, LP"},
    {"name": "Redmile Group", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001425738", "edgar_name": "Redmile Group, LLC"},
    {"name": "Cormorant Asset Management", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001583977",
     "edgar_name": "Cormorant Asset Management, LP"},
    {"name": "Sio Capital Management", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001482416",
     "edgar_name": "Sio Capital Management, LLC"},
    {"name": "ARCH Venture Partners", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001274403",
     "edgar_name": "ARCH Venture Management, LLC"},
    {"name": "Samsara BioCapital", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001744967", "edgar_name": "Samsara BioCapital LLC"},
    {"name": "Sofinnova Partners", "tier": "1A",
     "tier_label": "Sector specialist, deep diligence",
     "multiplier": 4.0, "primary_coverage": None,
     "cik": "0001631134", "edgar_name": "Sofinnova Partners SAS"},

    {"name": "Baupost Group", "tier": "1B",
     "tier_label": "High-conviction generalist superinvestor",
     "multiplier": 3.5,
     "primary_coverage": "Deep value, cross-sector distress",
     "cik": "0001061768", "edgar_name": "Baupost Group LLC/MA"},
    {"name": "Pershing Square", "tier": "1B",
     "tier_label": "High-conviction generalist superinvestor",
     "multiplier": 3.5,
     "primary_coverage": "Concentrated activist, cross-sector",
     "cik": "0001336528",
     "edgar_name": "Pershing Square Capital Mgmt"},
    {"name": "Appaloosa Management", "tier": "1B",
     "tier_label": "High-conviction generalist superinvestor",
     "multiplier": 3.5,
     "primary_coverage": "Macro-aware, cross-sector",
     "cik": "0001004244",
     "edgar_name": "Appaloosa Management LP"},
    {"name": "Third Point", "tier": "1B",
     "tier_label": "High-conviction generalist superinvestor",
     "multiplier": 3.5,
     "primary_coverage":
         "Activist, cross-sector, corporate events",
     "cik": "0001040273", "edgar_name": "Third Point LLC"},
    {"name": "Berkshire Hathaway", "tier": "1B",
     "tier_label": "High-conviction generalist superinvestor",
     "multiplier": 3.5,
     "primary_coverage":
         "Consumer staples, financials, energy, insurance",
     "cik": "0001067983", "edgar_name": "Berkshire Hathaway Inc"},

    {"name": "Greenlight Capital", "tier": "2A",
     "tier_label": "Generalist deep-value, concentrated",
     "multiplier": 2.5, "primary_coverage": None,
     "cik": "0001489933",
     "edgar_name": "DME Capital Management, LP"},
    {"name": "Gotham Asset Management", "tier": "2A",
     "tier_label": "Generalist deep-value, concentrated",
     "multiplier": 2.5, "primary_coverage": None,
     "cik": "0001510387",
     "edgar_name": "Gotham Asset Management, LLC"},
    {"name": "Ariel Investments", "tier": "2A",
     "tier_label": "Generalist deep-value, concentrated",
     "multiplier": 2.5, "primary_coverage": None,
     "cik": "0000936753", "edgar_name": "Ariel Investments, LLC"},
    {"name": "Oakmark Funds", "tier": "2A",
     "tier_label": "Generalist deep-value, concentrated",
     "multiplier": 2.5, "primary_coverage": None,
     "cik": "0000813917", "edgar_name": "Harris Associates L.P."},

    {"name": "Coatue Management", "tier": "2B",
     "tier_label": "Sector specialist, broader mandate",
     "multiplier": 2.0, "primary_coverage": None,
     "cik": "0001135730", "edgar_name": "Coatue Management LLC"},
    {"name": "Whale Rock Capital", "tier": "2B",
     "tier_label": "Sector specialist, broader mandate",
     "multiplier": 2.0, "primary_coverage": None,
     "cik": "0001387322",
     "edgar_name": "Whale Rock Capital Management LLC"},
    {"name": "Horizon Kinetics", "tier": "2B",
     "tier_label": "Sector specialist, broader mandate",
     "multiplier": 2.0, "primary_coverage": None,
     "cik": "0001056823",
     "edgar_name": "Horizon Kinetics Asset Management LLC"},
    {"name": "Orbis Investment Management", "tier": "2B",
     "tier_label": "Sector specialist, broader mandate",
     "multiplier": 2.0, "primary_coverage": None,
     "cik": "0001663865",
     "edgar_name": "Orbis Allan Gray Ltd"},

    {"name": "Fidelity active funds", "tier": "3",
     "tier_label": "Quality institutional, active management",
     "multiplier": 1.0, "primary_coverage": None,
     "cik": "0000315066",
     "edgar_name": "Fidelity Management & Research"},
    {"name": "T. Rowe Price active", "tier": "3",
     "tier_label": "Quality institutional, active management",
     "multiplier": 1.0, "primary_coverage": None,
     "cik": "0001897612",
     "edgar_name": "T. Rowe Price Investment Management, Inc."},
    {"name": "Wellington Management active", "tier": "3",
     "tier_label": "Quality institutional, active management",
     "multiplier": 1.0, "primary_coverage": None,
     "cik": "0001080351",
     "edgar_name": "Wellington Management Group"},
    {"name": "Royce & Associates", "tier": "3",
     "tier_label": "Quality institutional, active management",
     "multiplier": 1.0, "primary_coverage": None,
     "cik": "0000906304", "edgar_name": "Royce & Associates, LP"},

    {"name": "Vanguard index", "tier": "4",
     "tier_label": "Large passive / index", "multiplier": 0.0,
     "primary_coverage": None,
     "cik": "0000102909", "edgar_name": "Vanguard Group Inc"},
    {"name": "SPDR index products", "tier": "4",
     "tier_label": "Large passive / index", "multiplier": 0.0,
     "primary_coverage": None,
     "cik": "0000093751",
     "edgar_name": "State Street Corporation"},
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


def get_all_institutions() -> list[dict]:
    return [dict(i) for i in _INSTITUTIONS]


def get_institutions_by_tier(tier: str) -> list[dict]:
    if tier not in VALID_TIERS:
        raise ValueError(
            f"unknown tier: {tier!r} "
            f"(expected one of {sorted(VALID_TIERS)})"
        )
    return [dict(i) for i in _INSTITUTIONS if i["tier"] == tier]


def get_institution_by_name(name: str) -> Optional[dict]:
    for i in _INSTITUTIONS:
        if i["name"] == name:
            return dict(i)
    return None


def get_institution_by_cik(cik: str) -> Optional[dict]:
    padded = cik.zfill(10)
    for i in _INSTITUTIONS:
        if i["cik"] == padded:
            return dict(i)
    return None


def get_multiplier_for_institution(name: str) -> float:
    inst = get_institution_by_name(name)
    if inst is None:
        raise KeyError(f"unknown institution: {name!r}")
    return float(inst["multiplier"])


def seed_institutions(conn: sqlite3.Connection) -> int:
    ts = now_iso()
    inserted = 0
    for i in _INSTITUTIONS:
        cur = conn.execute(
            "INSERT OR IGNORE INTO institutions "
            "(name, tier, tier_label, multiplier, primary_coverage, "
            " cik, edgar_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (i["name"], i["tier"], i["tier_label"], i["multiplier"],
             i["primary_coverage"], i["cik"], i["edgar_name"], ts, ts),
        )
        inserted += cur.rowcount
    # Update CIK / edgar_name on rows that predate v3 (where seed_institutions
    # was originally called without those fields).
    for i in _INSTITUTIONS:
        conn.execute(
            "UPDATE institutions "
            "SET cik = ?, edgar_name = ?, updated_at = ? "
            "WHERE name = ? AND (cik IS NULL OR edgar_name IS NULL)",
            (i["cik"], i["edgar_name"], ts, i["name"]),
        )
    conn.commit()
    return inserted
