"""One-shot verification: compare each fund in
Input/list_of_funds.xlsx against 1_not_used/stockpicker.db
institutions table.

For each row:
  OK        = Excel CIK exists in DB institutions.cik (same fund, same CIK)
  MISMATCH  = fund appears in DB by name but with a different CIK
  NOT FOUND = no row in DB matches this fund by CIK or by name

Name matching is conservative: requires >=2 shared distinctive tokens
(stripped of entity suffixes and generic industry words) OR a single
long shared token (>=6 chars) with Jaccard >=0.5 on core tokens.

Run from repo root:
    python 2_Funds_parser/scripts/verify_ciks.py
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
XLSX = REPO_ROOT / "2_Funds_parser" / "Input" / "list_of_funds.xlsx"
DB = REPO_ROOT / "1_not_used" / "stockpicker.db"

# Tokens that carry no identity information in fund names.
STOPWORDS = {
    # entity suffixes
    "lp", "llc", "llp", "inc", "ltd", "limited",
    "corp", "corporation", "co", "company",
    "l", "p", "plc", "sas", "ag", "sa", "pte",
    # generic asset-manager words
    "capital", "management", "mgmt", "partners", "group",
    "holdings", "associates", "investments", "investment",
    "ventures", "venture", "fund", "funds", "advisors",
    "adviser", "advisor", "asset", "assets",
    # industry descriptors
    "healthcare", "health", "biotech", "biotechnology",
    "sciences", "science", "life", "biomedical", "pharma",
    "pharmaceuticals", "therapeutics", "medical", "medtech",
    "global", "international", "us", "usa", "american",
    # structural
    "the", "of", "and", "a", "an",
    # abbreviations that appear in DB edgar_names
    "co.", "ny", "ma", "il",
}


def tokenize(s: str) -> list[str]:
    """Lowercase, strip punctuation, split, drop stopwords and very
    short tokens."""
    if not s:
        return []
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return [t for t in s.split() if t and t not in STOPWORDS and len(t) >= 2]


def name_matches(
    fund_tokens: set[str], db_tokens: set[str]
) -> tuple[bool, set[str]]:
    """Return (match, shared_tokens) under a strict rule:
      * >=2 shared tokens, OR
      * 1 shared token of length >=6 AND jaccard(core) >= 0.5
    """
    if not fund_tokens or not db_tokens:
        return False, set()
    shared = fund_tokens & db_tokens
    if len(shared) >= 2:
        return True, shared
    if len(shared) == 1:
        only = next(iter(shared))
        if len(only) >= 6:
            union = fund_tokens | db_tokens
            jaccard = len(shared) / len(union)
            if jaccard >= 0.5:
                return True, shared
    return False, shared


def best_db_match_by_name(
    fund_orig: str, fund_legal: str, db_rows: list[dict]
) -> tuple[dict | None, set[str]]:
    fund_tokens = set(tokenize(fund_orig)) | set(tokenize(fund_legal))
    best = None
    best_shared: set[str] = set()
    best_score = -1
    for row in db_rows:
        db_tokens = set(tokenize(row["name"])) | set(
            tokenize(row["edgar_name"])
        )
        ok, shared = name_matches(fund_tokens, db_tokens)
        if not ok:
            continue
        # Score by shared-token count, then by longest shared token.
        score = (len(shared), max((len(t) for t in shared), default=0))
        if score > best_score:
            best_score = score
            best = row
            best_shared = shared
    return best, best_shared


def main() -> int:
    df = pd.read_excel(XLSX, dtype=str)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    db_rows = [
        dict(r)
        for r in conn.execute(
            "SELECT name, edgar_name, cik FROM institutions"
        ).fetchall()
    ]
    conn.close()

    cik_to_db_row = {r["cik"]: r for r in db_rows}

    print(
        f"{'#':>3}  {'Result':<10} {'Fund (Original)':<42} "
        f"{'Excel CIK':<12} {'DB match':<34} {'DB CIK':<12}"
    )
    print("-" * 125)
    summary = {"OK": 0, "MISMATCH": 0, "NOT FOUND": 0}

    for i, row in df.iterrows():
        fund_orig = str(row["Original Name"]).strip()
        fund_legal = str(row["Legal Name"]).strip()
        fund_cik = str(row["CIK"]).strip()

        # Primary: CIK lookup.
        if fund_cik in cik_to_db_row:
            db_hit = cik_to_db_row[fund_cik]
            result = "OK"
            db_label = db_hit["name"]
            db_cik = db_hit["cik"]
        else:
            # Fallback: strict name match.
            db_hit, _shared = best_db_match_by_name(
                fund_orig, fund_legal, db_rows
            )
            if db_hit is None:
                result = "NOT FOUND"
                db_label = "-"
                db_cik = "-"
            else:
                result = "MISMATCH"
                db_label = db_hit["name"]
                db_cik = db_hit["cik"]

        summary[result] += 1
        print(
            f"{i:>3}  {result:<10} {fund_orig[:42]:<42} "
            f"{fund_cik:<12} {db_label[:34]:<34} {db_cik:<12}"
        )

    print("-" * 125)
    print(
        f"TOTAL: {len(df)} rows | "
        f"OK={summary['OK']} "
        f"MISMATCH={summary['MISMATCH']} "
        f"NOT FOUND={summary['NOT FOUND']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
