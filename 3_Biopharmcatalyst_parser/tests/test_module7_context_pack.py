"""Module 7 — context_pack unit tests.

Critical contract checks (spec §5.1):
  • Pack does NOT contain insider/funds/momentum/M6-composite signals.
  • FDA designations parsed out of the BPC `drug` field.
  • Authoritative FDSC market cap from fundamentals.db (when available).
  • Graceful degradation when fundamentals.db is missing/empty.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6_5.fundamentals_db import (  # noqa: E402
    db_connect as fund_connect,
    init_fundamentals_db,
    upsert_financials_row,
)
from module_7.context_pack import (  # noqa: E402
    build_context_pack,
    catalyst_signature_for_pack,
    extract_fda_designations,
    fetch_hard_pass_candidates,
)
from database.db import get_connection  # noqa: E402


# ─────────────────── extract_fda_designations ──────────────────────


def test_fda_extract_no_badges():
    name, badges = extract_fda_designations("TX45")
    assert name == "TX45"
    assert badges == []


def test_fda_extract_single_badge():
    name, badges = extract_fda_designations("TX45 FTD")
    assert name == "TX45"
    assert badges == ["FTD"]


def test_fda_extract_multiple_badges():
    name, badges = extract_fda_designations("TX45 FTD ODD BTD")
    assert name == "TX45"
    assert set(badges) == {"FTD", "ODD", "BTD"}


def test_fda_extract_drug_with_dash_unaffected():
    name, badges = extract_fda_designations("CGT-115 ODD")
    assert name == "CGT-115"
    assert badges == ["ODD"]


def test_fda_extract_only_known_designations():
    """Random uppercase tokens that aren't in _KNOWN_FDA_DESIGNATIONS stay in the name."""
    name, badges = extract_fda_designations("ABC-XYZ Drug-1")
    assert "ABC-XYZ" in name
    assert badges == []


def test_fda_extract_empty():
    assert extract_fda_designations("") == ("", [])


# ───────────────────── build_context_pack ──────────────────────────


def _seed_biotech_db(db_path: Path, *, snapshot, ticker, drug, nct, ctype,
                     stage="phase2", catalyst_date="2026-09-15",
                     catalyst_text="Topline Q3 2026", date_min="2026-07-01",
                     date_max="2026-09-30"):
    """Seed catalyst_snapshots + catalyst_timing with one row."""
    conn = get_connection(db_path)
    try:
        conn.execute(
            """INSERT INTO catalyst_snapshots
               (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
                name, price, indication, stage, status, catalyst_date,
                catalyst_text, conference, market_cap_usd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot, ticker, drug, nct, ctype,
             "Test Therapeutics", 24.13, "PAH", stage, "ongoing",
             catalyst_date, catalyst_text, "", 1_000_000_000),
        )
        # catalyst_timing has additional NOT NULL cols beyond the spec's
        # business fields. Use a wildcard INSERT only on the cols we touch
        # and let SQLite fill defaults; for fields we don't have a default
        # for (computed_at, rules_version), pass explicit values.
        conn.execute(
            """INSERT INTO catalyst_timing
               (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
                date_min, date_max, precision_tier, source_lane, matched_phrase,
                computed_at, rules_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (snapshot, ticker, drug, nct, ctype,
             date_min, date_max, "quarter", "text_parse", "",
             "2026-05-28T18:00:00", "v1.0"),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_fundamentals_db(db_path: Path, ticker: str, **fields):
    init_fundamentals_db(db_path)
    with fund_connect(db_path) as cx:
        base = {
            "ticker": ticker, "cik": "0000000001",
            "period": "2026-Q1", "period_end_date": "2026-03-31",
            "form": "10-Q", "fetched_at": "2026-05-28T18:00:00",
            "fetch_status": "ok",
            "basic_shares_count": 38_500_000,
            "prefunded_warrants_count": 4_200_000,
            "fully_diluted_shares_count": 42_700_000,
            "last_price_usd": 24.13, "last_price_as_of": "2026-05-27",
            "market_cap_fdsc_usd": 1_030_351_000,
            "pfw_source": "capital_raises_sum_2yr",
            "pfw_share_dilution_warning": 0,
            "cash_total_usd": 178_000_000, "runway_months": 24.3,
            "quarterly_burn_usd": 22_000_000,
            "operating_cf_ttm_usd": -88_000_000,
        }
        base.update(fields)
        upsert_financials_row(cx, base)


def test_build_pack_happy_path(tmp_path: Path):
    biotech = tmp_path / "biotech.db"
    fund = tmp_path / "fundamentals.db"
    _seed_biotech_db(biotech, snapshot="2026-05-28", ticker="TCRX",
                     drug="TX45 FTD ODD", nct="NCT99000001",
                     ctype="Topline Data")
    _seed_fundamentals_db(fund, "TCRX")
    pack = build_context_pack(
        biotech, fund,
        snapshot_date="2026-05-28", ticker="TCRX",
        drug="TX45 FTD ODD", nct_number="NCT99000001",
        next_catalyst_type="Topline Data",
    )
    assert pack is not None
    assert pack["identity"]["ticker"] == "TCRX"
    assert pack["identity"]["drug"] == "TX45"                    # cleaned
    assert pack["identity"]["drug_raw"] == "TX45 FTD ODD"
    assert pack["identity"]["stage"] == "phase2"
    assert set(pack["catalyst"]["fda_designations"]) == {"FTD", "ODD"}
    assert pack["market_snapshot"]["market_cap_fdsc_usd"] == 1_030_351_000
    assert pack["market_snapshot"]["basic_shares_count"] == 38_500_000
    assert pack["market_snapshot"]["prefunded_warrants_count"] == 4_200_000
    assert pack["fundamentals"]["runway_months"] == 24.3


def test_pack_strips_signals_per_spec_5_1(tmp_path: Path):
    """HARD spec rule: insider/funds/momentum/M6-composite MUST NOT appear."""
    biotech = tmp_path / "biotech.db"
    fund = tmp_path / "fundamentals.db"
    _seed_biotech_db(biotech, snapshot="2026-05-28", ticker="TCRX",
                     drug="TX45", nct="NCT99000001", ctype="Topline Data")
    _seed_fundamentals_db(fund, "TCRX")
    pack = build_context_pack(
        biotech, fund,
        snapshot_date="2026-05-28", ticker="TCRX",
        drug="TX45", nct_number="NCT99000001",
        next_catalyst_type="Topline Data",
    )
    import json as _json
    text = _json.dumps(pack, default=str).lower()
    for forbidden in (
        "insider_score", "fund_accumulation_score", "momentum_score",
        "composite_score", "hard_pass", "fail_reasons",
    ):
        assert forbidden not in text, (
            f"Pack contains forbidden signal '{forbidden}' — "
            f"this would feed back into M6 inputs and double-count."
        )


def test_pack_graceful_when_fundamentals_missing(tmp_path: Path):
    """When fundamentals.db is empty, pack still builds — fundamentals is
    marked unavailable so Claude knows to web-search."""
    biotech = tmp_path / "biotech.db"
    _seed_biotech_db(biotech, snapshot="2026-05-28", ticker="TCRX",
                     drug="TX45", nct="NCT99000001", ctype="Topline Data")
    pack = build_context_pack(
        biotech, tmp_path / "no_such.db",
        snapshot_date="2026-05-28", ticker="TCRX",
        drug="TX45", nct_number="NCT99000001",
        next_catalyst_type="Topline Data",
    )
    assert pack is not None
    assert pack["fundamentals"]["available"] is False


def test_pack_returns_none_for_missing_pk(tmp_path: Path):
    biotech = tmp_path / "biotech.db"
    _seed_biotech_db(biotech, snapshot="2026-05-28", ticker="TCRX",
                     drug="TX45", nct="NCT99000001", ctype="Topline Data")
    out = build_context_pack(
        biotech, tmp_path / "fund.db",
        snapshot_date="2026-05-28", ticker="NOPE",
        drug="X", nct_number="X", next_catalyst_type="X",
    )
    assert out is None


def test_pack_weeks_to_catalyst_computed(tmp_path: Path):
    biotech = tmp_path / "biotech.db"
    _seed_biotech_db(biotech, snapshot="2026-05-28", ticker="TCRX",
                     drug="TX45", nct="NCT99000001", ctype="Topline Data",
                     date_min="2026-07-15", date_max="2026-09-15")
    pack = build_context_pack(
        biotech, tmp_path / "fund.db",
        snapshot_date="2026-05-28", ticker="TCRX",
        drug="TX45", nct_number="NCT99000001",
        next_catalyst_type="Topline Data",
    )
    # 2026-05-28 → 2026-07-15 = 48 days = 6 weeks; → 2026-09-15 = 110 days = 15 weeks
    assert pack["catalyst"]["weeks_to_catalyst_min"] == 6
    assert pack["catalyst"]["weeks_to_catalyst_max"] == 15


def test_catalyst_signature_helper_pulls_from_pack(tmp_path: Path):
    biotech = tmp_path / "biotech.db"
    _seed_biotech_db(biotech, snapshot="2026-05-28", ticker="TCRX",
                     drug="TX45 FTD", nct="NCT99000001",
                     ctype="Topline Data", catalyst_date="2026-09-15")
    pack = build_context_pack(
        biotech, tmp_path / "fund.db",
        snapshot_date="2026-05-28", ticker="TCRX",
        drug="TX45 FTD", nct_number="NCT99000001",
        next_catalyst_type="Topline Data",
    )
    sig = catalyst_signature_for_pack(pack)
    # drug_raw is "TX45 FTD" — preserved in signature (lower-cased)
    assert "tx45 ftd" in sig
    assert "phase2" in sig
    assert "topline data" in sig


# ─────────────────── fetch_hard_pass_candidates ────────────────────


def _seed_score(conn, snapshot, ticker, drug, nct, ctype, hard_pass=1,
                insider=50, momentum=60, funds=40):
    conn.execute(
        """INSERT INTO catalyst_scores
           (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
            hard_pass, fail_reasons, timing_bucket,
            insider_gross_weighted_usd, insider_score,
            return_30d_pct, momentum_score,
            fund_quarter_latest, fund_quarter_previous,
            funds_holding_latest, funds_holding_previous,
            fund_accumulation_usd, fund_accumulation_score,
            composite_score, computed_at, rules_version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snapshot, ticker, drug, nct, ctype,
         hard_pass, None, "catalyst_date_defined",
         5_000_000, insider, 0.05, momentum,
         "2026Q1", "2025Q4", 12, 10,
         15_000_000, funds, 55.0, "2026-05-28T18:00:00", "v1:abc"),
    )


def test_fetch_hard_pass_rolling_view(tmp_path: Path):
    biotech = tmp_path / "biotech.db"
    # Two snapshots; ticker A's catalyst flipped from hard_pass=1 → 0
    # between them. Rolling-view should pick the LATEST, so A is excluded.
    _seed_biotech_db(biotech, snapshot="2026-05-27", ticker="AAA",
                     drug="DrugA", nct="NCT1", ctype="Topline Data")
    _seed_biotech_db(biotech, snapshot="2026-05-28", ticker="AAA",
                     drug="DrugA", nct="NCT1", ctype="Topline Data")
    _seed_biotech_db(biotech, snapshot="2026-05-28", ticker="BBB",
                     drug="DrugB", nct="NCT2", ctype="Topline Data")
    conn = get_connection(biotech)
    try:
        _seed_score(conn, "2026-05-27", "AAA", "DrugA", "NCT1", "Topline Data", hard_pass=1)
        _seed_score(conn, "2026-05-28", "AAA", "DrugA", "NCT1", "Topline Data", hard_pass=0)
        _seed_score(conn, "2026-05-28", "BBB", "DrugB", "NCT2", "Topline Data", hard_pass=1)
        conn.commit()
    finally:
        conn.close()
    cands = fetch_hard_pass_candidates(biotech)
    tickers = {c["ticker"] for c in cands}
    assert "BBB" in tickers
    assert "AAA" not in tickers, "rolling view should drop AAA (latest hard_pass=0)"


def test_fetch_hard_pass_explicit_tickers_filter(tmp_path: Path):
    biotech = tmp_path / "biotech.db"
    for t in ("AAA", "BBB", "CCC"):
        _seed_biotech_db(biotech, snapshot="2026-05-28", ticker=t,
                         drug=f"Drug{t}", nct=f"NCT{t}", ctype="Topline Data")
    conn = get_connection(biotech)
    try:
        for t in ("AAA", "BBB", "CCC"):
            _seed_score(conn, "2026-05-28", t, f"Drug{t}", f"NCT{t}",
                        "Topline Data", hard_pass=1)
        conn.commit()
    finally:
        conn.close()
    cands = fetch_hard_pass_candidates(biotech, explicit_tickers=["AAA", "CCC"])
    tickers = {c["ticker"] for c in cands}
    assert tickers == {"AAA", "CCC"}
