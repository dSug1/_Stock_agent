"""Probe each institution that has a CIK but no filings_log row.

For each: hit EDGAR's submissions endpoint and classify it as

    HAS_13F-HR   — 13F-HR exists; parser failure, investigate
    NO_13F-HR    — seeded CIK never filed a 13F-HR (wrong entity)
    FETCH_ERROR  — submissions endpoint unreachable or malformed

Run from 1_not_used/ with PYTHONPATH=src so the edgar client
module resolves:

    python scripts/probe_missing_ciks.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from layer_minus1.edgar_13f_parser import (
    EDGAR_SUBMISSIONS_URL,
    _default_http_get,
    _pad_cik,
)

EDGAR_SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"

RELATED_FORMS = ("13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A", "NT 13F")


def _collect_all_filings(payload: dict) -> tuple[list[str], list[str]]:
    """Return (forms, filing_dates) across recent + all paginated files.

    Submissions JSON puts the ~1000 most recent filings in
    filings.recent and older ones in filings.files[*].name, each of
    which is a separate JSON page under /submissions/{name}. Long-lived
    filers (20+ years) need the paginated pages to see any 13F-HR
    history at all.
    """
    filings = payload.get("filings", {}) or {}
    recent = filings.get("recent", {}) or {}
    forms: list[str] = list(recent.get("form", []))
    dates: list[str] = list(recent.get("filingDate", []))

    for page in filings.get("files", []) or []:
        name = page.get("name")
        if not name:
            continue
        url = EDGAR_SUBMISSIONS_PAGE_URL.format(name=name)
        try:
            body = _default_http_get(url)
            page_payload = json.loads(body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(
                f"    [warn] page fetch failed {name}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        forms.extend(page_payload.get("form", []) or [])
        dates.extend(page_payload.get("filingDate", []) or [])

    return forms, dates


def main(db_path: str = "stockpicker.db") -> int:
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT i.tier, i.name, i.cik
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

    print(f"Probing {len(rows)} institutions via EDGAR submissions...\n")

    verdict_counts: dict[str, int] = {}
    retry_candidates: list[tuple[str, str, str, int, str]] = []
    wrong_cik: list[tuple[str, str, str]] = []

    for r in rows:
        cik = r["cik"]
        name = r["name"]
        tier = r["tier"]
        url = EDGAR_SUBMISSIONS_URL.format(cik=_pad_cik(cik))

        try:
            body = _default_http_get(url)
            payload = json.loads(body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            verdict_counts["FETCH_ERROR"] = (
                verdict_counts.get("FETCH_ERROR", 0) + 1
            )
            print(
                f"  {tier:3} {name:42} CIK={cik}  "
                f"FETCH_ERROR: {type(exc).__name__}: {exc}"
            )
            continue

        forms, filing_dates = _collect_all_filings(payload)

        counts: dict[str, int] = {}
        latest_13f: str | None = None
        for i, f in enumerate(forms):
            if f in RELATED_FORMS:
                counts[f] = counts.get(f, 0) + 1
            if f == "13F-HR":
                d = filing_dates[i] if i < len(filing_dates) else None
                if d is not None and (latest_13f is None or d > latest_13f):
                    latest_13f = d

        total_13f_hr = counts.get("13F-HR", 0)
        if total_13f_hr > 0:
            verdict = "HAS_13F-HR"
            retry_candidates.append(
                (tier, name, cik, total_13f_hr, latest_13f or "?")
            )
            summary = (
                f"HAS_13F-HR count={total_13f_hr} latest={latest_13f}"
            )
        else:
            verdict = "NO_13F-HR"
            wrong_cik.append((tier, name, cik))
            related = ", ".join(
                f"{k}:{v}" for k, v in counts.items() if v
            )
            summary = (
                f"NO_13F-HR"
                + (f"  (related: {related})" if related else "")
            )

        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        print(f"  {tier:3} {name:42} CIK={cik}  {summary}")

    print("\n=== Verdict totals ===")
    for k in sorted(verdict_counts):
        print(f"  {k:12} {verdict_counts[k]}")

    if retry_candidates:
        print(
            "\n=== HAS_13F-HR but unlogged — parser bug or "
            "race, worth a re-run ==="
        )
        for tier, name, cik, n, latest in retry_candidates:
            print(
                f"  {tier:3} {name:42} CIK={cik}  "
                f"13F-HR count={n} latest={latest}"
            )

    if wrong_cik:
        print(
            "\n=== NO_13F-HR — CIK likely points to wrong entity, "
            "reseed needed ==="
        )
        for tier, name, cik in wrong_cik:
            print(f"  {tier:3} {name:42} CIK={cik}")

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(sys.argv[1] if len(sys.argv) > 1 else "stockpicker.db")
    )
