"""Module 7 — per-ticker context pack builder.

Joins data from `biotech.db` + `fundamentals.db` (no funds DB — funds
holdings are deliberately stripped per spec §5.1 to avoid groupthink
with M6's `fund_accumulation_score`).

Per spec §5.1, the pack OMITS:
  • CEO/CFO insider trades            (used as `m_insider` modifier)
  • Specialist-fund position deltas   (used as `m_funds` modifier)
  • 30-day momentum                   (used as `m_momentum` modifier)
  • M6 composite_score / hard_pass    (selection input, not thesis evidence)

What we DO send Claude:
  • identity        — ticker / company / drug / indication / stage / NCT
  • catalyst        — text + window + FDA designations parsed from drug field
  • market_snapshot — last_price, shares (basic + PFW + FDSC), FDSC mcap
  • fundamentals    — cash, runway, R&D, capital-raise history

Output is a deterministic dict so two runs with unchanged biotech.db +
fundamentals.db content produce byte-identical packs (modulo `built_at`).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ───────────────────── FDA-designation extraction ──────────────────


# BPC's docx → csv path appends FDA designations to the `Drug` field as
# space-suffixed badges (`"TX45 FTD ODD"`). M0a captures the `blurred-text`
# attribute which carries the bare drug name, but the visible BPC `Drug`
# column on `catalyst_snapshots.drug` includes the suffixes. We need them
# AS structured fields in the pack so Claude can reason about them.
_KNOWN_FDA_DESIGNATIONS = (
    "BTD",       # Breakthrough Therapy Designation
    "FTD",       # Fast Track
    "ODD",       # Orphan Drug
    "RMAT",      # Regenerative Medicine Advanced Therapy
    "RPDD",      # Rare Pediatric Disease Designation
    "PRIME",     # EMA priority medicines
    "AA",        # Accelerated Approval pathway designation
    "QIDP",      # Qualified Infectious Disease Product
)
_FDA_BADGE_RE = re.compile(
    r"\b(" + "|".join(_KNOWN_FDA_DESIGNATIONS) + r")\b",
)


def extract_fda_designations(drug_field: str) -> tuple[str, list[str]]:
    """Split a BPC `Drug` field into (clean_drug_name, [designations]).

    >>> extract_fda_designations("TX45 FTD ODD")
    ('TX45', ['FTD', 'ODD'])
    >>> extract_fda_designations("CGT-115")
    ('CGT-115', [])
    """
    if not drug_field:
        return ("", [])
    designations: list[str] = []
    cleaned = drug_field
    for m in _FDA_BADGE_RE.finditer(drug_field):
        designations.append(m.group(1))
    # Remove the badge tokens from the drug name (preserve order otherwise).
    if designations:
        for d in designations:
            cleaned = re.sub(r"\s+" + re.escape(d) + r"\b", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return (cleaned, designations)


# ───────────────────── pack-building queries ───────────────────────


def _fetch_catalyst_row(
    conn: sqlite3.Connection,
    *,
    snapshot_date: str,
    ticker: str,
    drug: str,
    nct_number: str,
    next_catalyst_type: str,
) -> Optional[dict]:
    """One row from catalyst_snapshots LEFT JOIN catalyst_timing."""
    row = conn.execute(
        """
        SELECT
            s.snapshot_date, s.ticker, s.drug, s.nct_number, s.next_catalyst_type,
            s.name, s.indication, s.stage, s.status,
            s.price, s.market_cap_usd AS bpc_market_cap_usd,
            s.catalyst_date, s.catalyst_text, s.conference,
            s.historical_loa, s.historical_pop, s.sentiment,
            t.date_min, t.date_max, t.precision_tier, t.source_lane,
            t.matched_phrase
        FROM catalyst_snapshots s
        LEFT JOIN catalyst_timing t USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        WHERE s.snapshot_date = ?
          AND s.ticker = ? AND s.drug = ?
          AND s.nct_number = ? AND s.next_catalyst_type = ?
        """,
        (snapshot_date, ticker, drug, nct_number, next_catalyst_type),
    ).fetchone()
    return dict(row) if row else None


def _weeks_between_iso(d_iso_a: Optional[str], d_iso_b: Optional[str]) -> Optional[int]:
    if not d_iso_a or not d_iso_b:
        return None
    try:
        from datetime import date as _date
        a = _date.fromisoformat(d_iso_a)
        b = _date.fromisoformat(d_iso_b)
        return max(0, (a - b).days // 7)
    except (TypeError, ValueError):
        return None


def _fetch_fundamentals(
    fundamentals_db_path: Path, ticker: str,
) -> Optional[dict]:
    """Latest financials row + recent capital raises from fundamentals.db.

    Returns ``None`` when the ticker isn't in fundamentals.db (M6.5 didn't
    enrich it). Caller decides what to do — the dispatch path treats a
    missing pack-fundamentals block as "Claude will have to web-search".
    """
    if not fundamentals_db_path.exists():
        return None
    with sqlite3.connect(fundamentals_db_path) as cx:
        cx.row_factory = sqlite3.Row
        fin = cx.execute(
            """
            SELECT * FROM financials
            WHERE ticker = ?
            ORDER BY period_end_date DESC
            LIMIT 1
            """, (ticker,),
        ).fetchone()
        if not fin:
            return None
        # Last 8 capital raises in any category — Claude reads to assess
        # dilution overhang + PFW history.
        raises = cx.execute(
            """
            SELECT filing_date, form, raise_type,
                   gross_proceeds_usd, shares_issued, price_per_share_usd,
                   raw_filing_url
            FROM capital_raises
            WHERE ticker = ?
            ORDER BY filing_date DESC
            LIMIT 8
            """, (ticker,),
        ).fetchall()
    fin_d = dict(fin)
    return {
        "as_of_period_end": fin_d.get("period_end_date"),
        "form":             fin_d.get("form"),
        "cash_total_usd":   fin_d.get("cash_total_usd"),
        "cash_and_equivalents_usd":   fin_d.get("cash_and_equivalents_usd"),
        "short_term_investments_usd": fin_d.get("short_term_investments_usd"),
        "quarterly_burn_usd":         fin_d.get("quarterly_burn_usd"),
        "runway_months":              fin_d.get("runway_months"),
        "operating_cf_ttm_usd":       fin_d.get("operating_cf_ttm_usd"),
        "rd_expense_ttm_usd":         fin_d.get("rd_expense_ttm_usd"),
        "ga_expense_ttm_usd":         fin_d.get("ga_expense_ttm_usd"),
        "basic_shares_count":         fin_d.get("basic_shares_count"),
        "prefunded_warrants_count":   fin_d.get("prefunded_warrants_count"),
        "fully_diluted_shares_count": fin_d.get("fully_diluted_shares_count"),
        "pfw_source":                 fin_d.get("pfw_source"),
        "pfw_share_dilution_warning": bool(fin_d.get("pfw_share_dilution_warning"))
                                      if fin_d.get("pfw_share_dilution_warning") is not None
                                      else None,
        "last_price_usd":             fin_d.get("last_price_usd"),
        "last_price_as_of":           fin_d.get("last_price_as_of"),
        "market_cap_fdsc_usd":        fin_d.get("market_cap_fdsc_usd"),
        "recent_capital_raises":      [dict(r) for r in raises],
        "fundamentals_source":        "data/fundamentals.db (M6.5 — SEC XBRL + capital_raises + yfinance)",
    }


# ────────────────────────── public builder ─────────────────────────


def build_context_pack(
    biotech_db_path: Path,
    fundamentals_db_path: Path,
    *,
    snapshot_date: str,
    ticker: str,
    drug: str,
    nct_number: str,
    next_catalyst_type: str,
) -> Optional[dict]:
    """Build the per-ticker pack the M7 dispatcher feeds to Claude.

    Returns ``None`` when the (snapshot_date, ticker, drug, nct, type)
    composite PK doesn't exist in `catalyst_snapshots`. Caller treats
    that as "candidate dropped from feed".
    """
    # Local imports to avoid circular module init.
    from database.db import get_connection  # type: ignore

    conn = get_connection(biotech_db_path)
    try:
        row = _fetch_catalyst_row(
            conn, snapshot_date=snapshot_date, ticker=ticker, drug=drug,
            nct_number=nct_number, next_catalyst_type=next_catalyst_type,
        )
    finally:
        conn.close()
    if row is None:
        return None

    clean_drug, designations = extract_fda_designations(row["drug"])
    weeks_min = _weeks_between_iso(row.get("date_min"), snapshot_date)
    weeks_max = _weeks_between_iso(row.get("date_max"), snapshot_date)

    funds = _fetch_fundamentals(fundamentals_db_path, ticker)

    pack: dict = {
        "snapshot_date": snapshot_date,
        "identity": {
            "ticker":      row["ticker"],
            "company_name": row.get("name") or "",
            "drug":         clean_drug or row["drug"],
            "drug_raw":     row["drug"],
            "indication":   row.get("indication") or "",
            "stage":        row.get("stage") or "",
            "nct_number":   row["nct_number"],
            "status":       row.get("status") or "",
        },
        "catalyst": {
            "next_catalyst_type":      row["next_catalyst_type"],
            "catalyst_text":           row.get("catalyst_text") or "",
            "conference":              row.get("conference") or "",
            "snapshot_date":           snapshot_date,
            "catalyst_date":           row.get("catalyst_date"),
            "date_min":                row.get("date_min"),
            "date_max":                row.get("date_max"),
            "weeks_to_catalyst_min":   weeks_min,
            "weeks_to_catalyst_max":   weeks_max,
            "precision_tier":          row.get("precision_tier") or "",
            "fda_designations":        designations,
            "historical_loa":          row.get("historical_loa"),
            "historical_pop":          row.get("historical_pop"),
        },
        "market_snapshot": {
            "bpc_price":               row.get("price"),
            "bpc_market_cap_usd":      row.get("bpc_market_cap_usd"),
            # Authoritative FDSC values from M6.5 — Claude prefers these
            # per HARD RULE #4 (m7-v1).
            "last_price_usd":          (funds or {}).get("last_price_usd"),
            "last_price_as_of":        (funds or {}).get("last_price_as_of"),
            "basic_shares_count":      (funds or {}).get("basic_shares_count"),
            "prefunded_warrants_count": (funds or {}).get("prefunded_warrants_count"),
            "fully_diluted_shares_count": (funds or {}).get("fully_diluted_shares_count"),
            "market_cap_fdsc_usd":     (funds or {}).get("market_cap_fdsc_usd"),
            "pfw_source":              (funds or {}).get("pfw_source"),
            "pfw_share_dilution_warning": (funds or {}).get("pfw_share_dilution_warning"),
        },
        "fundamentals": funds if funds else {
            "available": False,
            "reason":    "M6.5 has not enriched this ticker; "
                         "Claude should web-search SEC EDGAR for shares + cash.",
        },
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Deliberately ABSENT (spec §5.1):
        #   insider_score / fund_accumulation_score / momentum_score
        #   composite_score / hard_pass / fail_reasons
        # These are applied as Python modifiers AFTER Claude returns.
    }
    return pack


# ───────────────── pack augmentation for D23 drug-dedup ────────────


def augment_pack_with_drug_siblings(
    pack: dict, drug_group_members: list[dict],
) -> dict:
    """D23 — annotate an anchor pack with the other catalysts of the same
    drug so Claude sees the full event schedule when one API call's
    output will be replicated across rows.

    `drug_group_members` is the candidate list returned by
    cache.group_candidates_by_drug — each entry carries at minimum
    next_catalyst_type, nct_number, catalyst_date_iso.

    The pack's primary `catalyst` block stays anchored to the original
    catalyst (the one the dispatcher chose as the API anchor). Siblings
    are added under `catalyst.sibling_catalysts`. Returns a shallow copy
    when siblings exist; otherwise returns the original pack unchanged.
    """
    anchor_nct = pack["identity"]["nct_number"]
    anchor_type = pack["catalyst"]["next_catalyst_type"]
    siblings: list[dict] = []
    for m in drug_group_members:
        if (m.get("nct_number") == anchor_nct
                and m.get("next_catalyst_type") == anchor_type):
            continue
        siblings.append({
            "nct_number":         m.get("nct_number"),
            "next_catalyst_type": m.get("next_catalyst_type"),
            "catalyst_date_iso":  m.get("catalyst_date_iso"),
        })
    if not siblings:
        return pack
    new_pack = dict(pack)
    new_pack["catalyst"] = dict(pack["catalyst"])
    new_pack["catalyst"]["sibling_catalysts"] = siblings
    return new_pack


# ───────────────── catalyst-signature helper for cache ─────────────


def catalyst_signature_for_pack(pack: dict) -> str:
    """Build the cache signature for one pack — wraps cache.compute_catalyst_signature
    so the dispatcher can compute it from the pack without duplicating
    field-extraction logic.
    """
    from .cache import compute_catalyst_signature
    catalyst_date_iso = (
        pack.get("catalyst", {}).get("catalyst_date")
        or pack.get("catalyst", {}).get("date_min")
        or pack.get("catalyst", {}).get("date_max")
    )
    return compute_catalyst_signature(
        drug=pack["identity"]["drug_raw"],
        stage=pack["identity"].get("stage"),
        next_catalyst_type=pack["catalyst"]["next_catalyst_type"],
        catalyst_date_iso=catalyst_date_iso,
    )


# ───────────────────────── feed-building ───────────────────────────


def fetch_hard_pass_candidates(
    biotech_db_path: Path,
    *,
    explicit_tickers: Optional[list[str]] = None,
    defined_timing_only: bool = False,
) -> list[dict]:
    """Return the rolling-view hard-pass feed used by M6.5 + M7.

    Each candidate dict carries the 5-tuple PK plus `stage` and a
    resolved `catalyst_date_iso` (catalyst_timing.date_min → date_max →
    catalyst_snapshots.catalyst_date) for the cache signature.

    When ``explicit_tickers`` is given, restricts to those tickers but
    still requires `hard_pass = 1`. To include hard-fail rows, the caller
    must use a `--include-soft-fail` flag in the dispatcher and bypass
    this helper.

    When ``defined_timing_only`` is True, restricts to rows with
    timing_bucket = 'catalyst_date_defined' — the same set the HTML
    report shows on its default "Defined timing" tab.
    """
    from database.db import get_connection  # type: ignore
    conn = get_connection(biotech_db_path)
    try:
        sql = """
        WITH latest_per_catalyst AS (
            SELECT ticker, drug, nct_number, next_catalyst_type,
                   MAX(snapshot_date) AS max_snap
            FROM catalyst_scores
            GROUP BY ticker, drug, nct_number, next_catalyst_type
        )
        SELECT
            cs.snapshot_date, cs.ticker, cs.drug, cs.nct_number, cs.next_catalyst_type,
            s.stage,
            COALESCE(t.date_min, t.date_max, s.catalyst_date) AS catalyst_date_iso,
            cs.insider_score, cs.momentum_score, cs.fund_accumulation_score
        FROM catalyst_scores cs
        JOIN latest_per_catalyst l USING (ticker, drug, nct_number, next_catalyst_type)
        LEFT JOIN catalyst_timing t USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        LEFT JOIN catalyst_snapshots s USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        WHERE cs.snapshot_date = l.max_snap
          AND cs.hard_pass = 1
        """
        params: list = []
        if explicit_tickers:
            placeholders = ",".join("?" * len(explicit_tickers))
            sql += f" AND cs.ticker IN ({placeholders})"
            params.extend(explicit_tickers)
        if defined_timing_only:
            sql += " AND cs.timing_bucket = 'catalyst_date_defined'"
        sql += " ORDER BY cs.ticker, cs.drug, cs.nct_number, cs.next_catalyst_type"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def fetch_rescue_candidates(
    biotech_db_path: Path,
    *,
    explicit_tickers: Optional[list[str]] = None,
    classes: Optional[list[str]] = None,
) -> list[dict]:
    """D35 — rolling-view rescue feed for M8.

    Returns one dict per catalyst whose `catalyst_scores.rescued = 1`,
    same columns as `fetch_hard_pass_candidates` plus a `rescue_class`
    string ('A'/'B'/'C'/'AB'/...). Caller (3_8_rescue_dispatch.py)
    builds packs and dispatches via the M7 machinery unchanged.

    When ``classes`` is given (e.g. ['A','B']), filters to catalysts
    whose rescue_class CONTAINS any of those letters. None = all.
    """
    from database.db import get_connection  # type: ignore
    conn = get_connection(biotech_db_path)
    try:
        sql = """
        WITH latest_per_catalyst AS (
            SELECT ticker, drug, nct_number, next_catalyst_type,
                   MAX(snapshot_date) AS max_snap
            FROM catalyst_scores
            GROUP BY ticker, drug, nct_number, next_catalyst_type
        )
        SELECT
            cs.snapshot_date, cs.ticker, cs.drug, cs.nct_number, cs.next_catalyst_type,
            cs.rescue_class, cs.fail_reasons,
            s.stage,
            COALESCE(t.date_min, t.date_max, s.catalyst_date) AS catalyst_date_iso,
            cs.insider_score, cs.momentum_score, cs.fund_accumulation_score
        FROM catalyst_scores cs
        JOIN latest_per_catalyst l USING (ticker, drug, nct_number, next_catalyst_type)
        LEFT JOIN catalyst_timing t USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        LEFT JOIN catalyst_snapshots s USING
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type)
        WHERE cs.snapshot_date = l.max_snap
          AND cs.rescued = 1
        """
        params: list = []
        if explicit_tickers:
            placeholders = ",".join("?" * len(explicit_tickers))
            sql += f" AND cs.ticker IN ({placeholders})"
            params.extend(explicit_tickers)
        if classes:
            # rescue_class is a sorted concat like 'A','BC','ABC'. Use
            # LIKE '%X%' for each requested class and combine with OR.
            class_clauses = " OR ".join("cs.rescue_class LIKE ?" for _ in classes)
            sql += f" AND ({class_clauses})"
            params.extend(f"%{c}%" for c in classes)
        sql += " ORDER BY cs.ticker, cs.drug, cs.nct_number, cs.next_catalyst_type"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def augment_pack_for_rescue(pack: dict, rescue_class: str) -> dict:
    """D35 — add `catalyst.rescue_class` to the pack so the M8 system
    prompt prefix can read it and adjust scoring guidance.

    Returns a shallow copy of the pack with the rescue context attached.
    Does NOT mutate the original.
    """
    new_pack = dict(pack)
    new_pack["catalyst"] = dict(pack["catalyst"])
    new_pack["catalyst"]["rescue_class"] = rescue_class
    new_pack["catalyst"]["rescue_mode"] = True
    return new_pack
