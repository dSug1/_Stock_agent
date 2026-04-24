"""For each institution with no filings_log row, query EDGAR's full-text
search for recent 13F-HR filings and print candidate CIKs with the
filer names EDGAR returns.

The intent is human-in-the-loop: this script lists candidates; you
verify on EDGAR and update institution_registry.py manually.

Run from 1_not_used/ with PYTHONPATH=src:

    python scripts/find_cik_candidates.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

from layer_minus1.edgar_13f_parser import _default_http_get


EDGAR_FULL_TEXT_SEARCH = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q={q}&forms=13F-HR&dateRange=custom"
    "&startdt={startdt}&enddt={enddt}"
)

# Strip legal-entity suffixes so quoted search matches more filer names.
SUFFIXES = re.compile(
    r"\b(LP|L\.P\.|LLC|L\.L\.C\.|Inc|Inc\.|Ltd|Ltd\.|"
    r"Corporation|Corp|SAS|LLP|Group)\b\.?",
    re.IGNORECASE,
)

# Name overrides when DB-stored label does not match EDGAR's filer name.
# Keyed by the DB `name` column. Value is the query string sent to EDGAR.
NAME_OVERRIDES: dict[str, str] = {
    "Boxer Capital (Tavistock)": "Boxer Capital",
    "Goehring & Rozencwajg": "Goehring Rozencwajg",
    "BVF Inc.": "BVF Partners",
    "Fidelity active funds": "Fidelity Management Research",
    "T. Rowe Price active": "T Rowe Price Associates",
    "Wellington Management active": "Wellington Management",
    "Vanguard index": "Vanguard Group",
    "SPDR index products": "State Street",
}


def _query_name(inst_name: str, edgar_name: str | None) -> str:
    if inst_name in NAME_OVERRIDES:
        return NAME_OVERRIDES[inst_name]
    base = edgar_name or inst_name
    # Drop parenthetical qualifiers and legal suffixes.
    base = re.sub(r"\([^)]*\)", "", base)
    base = SUFFIXES.sub("", base)
    return re.sub(r"\s+", " ", base).strip()


def _search_candidates(
    query: str,
    startdt: str = "2024-01-01",
    enddt: str = "2026-12-31",
) -> list[tuple[str, str, int]]:
    """Return [(cik, display_name, hits)] sorted by hit count desc."""
    url = EDGAR_FULL_TEXT_SEARCH.format(
        q=urllib.parse.quote(f'"{query}"'),
        startdt=startdt,
        enddt=enddt,
    )
    try:
        body = _default_http_get(url)
        payload = json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"    [warn] search failed: {type(exc).__name__}: {exc}")
        return []

    counter: Counter[tuple[str, str]] = Counter()
    for hit in payload.get("hits", {}).get("hits", []):
        src = hit.get("_source", {}) or {}
        ciks = src.get("ciks") or []
        names = src.get("display_names") or []
        # Pair each CIK with its matching display name when possible.
        for i, cik in enumerate(ciks):
            display = (
                names[i] if i < len(names) and names[i] else "(unnamed)"
            )
            counter[(cik, display)] += 1

    return [
        (cik, name, n)
        for (cik, name), n in counter.most_common(5)
    ]


def main(db_path: str = "stockpicker.db") -> int:
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT i.tier, i.name, i.cik, i.edgar_name
        FROM institutions i
        WHERE i.cik IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM filings_log fl
            WHERE fl.institution_id = i.id
          )
        ORDER BY i.tier, i.name
        """
    ).fetchall()
    conn.close()

    print(
        f"Searching EDGAR 13F-HR filings for {len(rows)} institutions"
        "  (2024-01-01 to 2026-12-31)\n"
    )

    for r in rows:
        name = r["name"]
        query = _query_name(name, r["edgar_name"])
        print(
            f"[{r['tier']}] {name}  "
            f"current_cik={r['cik']}  query={query!r}"
        )
        cands = _search_candidates(query)
        if not cands:
            print("    (no 13F-HR results — try a narrower/wider query)")
        for cik, display, n in cands:
            print(f"    CIK={cik}  hits={n:3}  {display}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(sys.argv[1] if len(sys.argv) > 1 else "stockpicker.db")
    )
