"""Module 6 — end-to-end ingest test against an isolated synthetic DB."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from database.db import get_connection  # noqa: E402
from module_6.config import default_config_path, load_scoring_config  # noqa: E402
from module_6.ingest import score_snapshot  # noqa: E402


SNAP = date(2026, 5, 27)


def _make_isolated_biotech_db(tmp_path: Path) -> Path:
    """Create a fresh biotech.db at a tmp path via get_connection()."""
    db_path = tmp_path / "biotech.db"
    conn = get_connection(db_path)
    conn.close()
    return db_path


def _make_synthetic_funds_db(tmp_path: Path) -> Path:
    p = tmp_path / "mini_funds.db"
    fconn = sqlite3.connect(p)
    fconn.executescript("""
    CREATE TABLE holdings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fund_id INTEGER,
        period_of_report TEXT,
        ticker TEXT,
        shares INTEGER,
        market_value INTEGER
    );
    INSERT INTO holdings (fund_id, period_of_report, ticker, shares, market_value) VALUES
        (1, '2025-12-31', 'AAA', 10000, 100000),
        (1, '2026-03-31', 'AAA', 20000, 200000),
        (1, '2025-12-31', 'BBB', 5000, 50000),
        (1, '2026-03-31', 'BBB', 5000, 50000);
    """)
    fconn.commit()
    fconn.close()
    return p


def _insert_catalyst(conn, *, ticker, drug, stage, market_cap, next_type="Interim Data",
                     price_history="10.0;10.5;11.0", nct="NCT00000001"):
    conn.execute(
        """
        INSERT INTO catalyst_snapshots
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
             name, stage, market_cap_usd, price_history_30d)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (SNAP.isoformat(), ticker, drug, nct, next_type, ticker + " Co",
         stage, market_cap, price_history),
    )


def _insert_timing(conn, *, ticker, drug, nct="NCT00000001", next_type="Interim Data",
                   date_min, date_max, tier="conference", lane="conference"):
    conn.execute(
        """
        INSERT INTO catalyst_timing
            (snapshot_date, ticker, drug, nct_number, next_catalyst_type,
             date_min, date_max, precision_tier, source_lane, matched_phrase,
             computed_at, rules_version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (SNAP.isoformat(), ticker, drug, nct, next_type,
         date_min.isoformat() if date_min else None,
         date_max.isoformat() if date_max else None,
         tier, lane, None,
         datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "v1.0"),
    )


def _seed(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    biotech_db = _make_isolated_biotech_db(tmp_path)
    conn = get_connection(biotech_db)

    # Catalyst 1: AAA — small/mid cap, valid clinical readout, future date
    _insert_catalyst(conn, ticker="AAA", drug="DrugA", stage="phase2",
                     market_cap=500_000_000, nct="NCT00000001")
    _insert_timing(conn, ticker="AAA", drug="DrugA", nct="NCT00000001",
                   date_min=date(2026, 7, 1), date_max=date(2026, 7, 31))

    # Catalyst 2: BBB — small cap, BUT phase4 (H5 fail)
    _insert_catalyst(conn, ticker="BBB", drug="DrugB", stage="phase4",
                     market_cap=500_000_000, nct="NCT00000002")
    _insert_timing(conn, ticker="BBB", drug="DrugB", nct="NCT00000002",
                   date_min=date(2026, 7, 1), date_max=date(2026, 7, 31))

    # Catalyst 3: CCC — large cap (H1 fail)
    _insert_catalyst(conn, ticker="CCC", drug="DrugC", stage="phase2",
                     market_cap=10_000_000_000, nct="NCT00000003")
    _insert_timing(conn, ticker="CCC", drug="DrugC", nct="NCT00000003",
                   date_min=date(2026, 7, 1), date_max=date(2026, 7, 31))

    # Catalyst 4: DDD — past window (H3 + H4 fail)
    _insert_catalyst(conn, ticker="DDD", drug="DrugD", stage="phase2",
                     market_cap=500_000_000, nct="NCT00000004")
    _insert_timing(conn, ticker="DDD", drug="DrugD", nct="NCT00000004",
                   date_min=date(2026, 4, 1), date_max=date(2026, 4, 30))

    # Catalyst 5: EEE — undefined timing bucket (year tier)
    _insert_catalyst(conn, ticker="EEE", drug="DrugE", stage="phase3",
                     market_cap=800_000_000, nct="NCT00000005")
    _insert_timing(conn, ticker="EEE", drug="DrugE", nct="NCT00000005",
                   date_min=date(2026, 8, 1), date_max=date(2026, 12, 31),
                   tier="year", lane="text_parse")

    # Add an insider buy for AAA (CEO, $1M)
    # First insert an EDGAR Form 4 filing + transaction so the view picks it up.
    conn.execute("""
        INSERT INTO edgar_form4_filings
        (accession_number, cik_issuer, ticker, issuer_name,
         reporting_owner_cik, reporting_owner_name,
         is_director, is_officer, is_ten_percent_owner, officer_title,
         filed_date, fetched_at)
        VALUES ('0000-AAA-CEO', '0000001', 'AAA', 'AAA Co',
                '0000002', 'Jane CEO', 0, 1, 0, 'Chief Executive Officer',
                '2026-04-01', '2026-04-01T00:00:00')
    """)
    conn.execute("""
        INSERT INTO edgar_form4_transactions
        (accession_number, transaction_date, transaction_code, transaction_code_meaning,
         acquired_disposed, shares, price_per_share, shares_owned_following,
         is_open_market, direct_or_indirect)
        VALUES ('0000-AAA-CEO', '2026-04-01', 'P', 'Open market purchase',
                'A', 100000, 10.0, 100000, 1, 'D')
    """)
    conn.commit()
    return biotech_db, conn


def test_score_snapshot_end_to_end(tmp_path: Path):
    biotech_db, conn = _seed(tmp_path)
    funds_db = _make_synthetic_funds_db(tmp_path)

    cfg = load_scoring_config(default_config_path())
    # point cfg at our test funds DB by overriding the path on the model
    cfg.funds.db_path_relative_to_repo_root = str(funds_db.resolve())

    stats = score_snapshot(
        SNAP, conn, cfg,
        project_root=PROJECT_ROOT, skip_funds=False,
    )

    assert stats.rows_in == 5
    assert stats.rows_inserted == 5
    assert stats.rows_updated == 0
    # AAA + EEE should hard_pass
    assert stats.hard_pass_count == 2
    assert stats.bucket_counts.get("catalyst_date_defined") == 1
    assert stats.bucket_counts.get("catalyst_date_undefined") == 1

    rows = {r["ticker"]: r for r in conn.execute(
        "SELECT * FROM catalyst_scores WHERE snapshot_date = ?",
        (SNAP.isoformat(),),
    ).fetchall()}

    aaa = rows["AAA"]
    assert aaa["hard_pass"] == 1
    assert aaa["timing_bucket"] == "catalyst_date_defined"
    assert aaa["composite_score"] is not None
    assert aaa["insider_score"] > 0
    assert aaa["fund_accumulation_score"] > 0
    assert aaa["fund_quarter_latest"] == "2026-03-31"

    bbb = rows["BBB"]
    assert bbb["hard_pass"] == 0
    assert "H5" in bbb["fail_reasons"]
    # D35 — composite_score is now computed for ALL rows (not just
    # hard_pass) so the Rescued tab can sort + display by composite.
    # Signal scores (insider/momentum/funds) were always populated;
    # we just stopped gating the composite math on hard_pass.
    assert bbb["composite_score"] is not None
    assert 0 <= bbb["composite_score"] <= 100

    ccc = rows["CCC"]
    assert ccc["hard_pass"] == 0
    assert "H1" in ccc["fail_reasons"]

    ddd = rows["DDD"]
    assert ddd["hard_pass"] == 0
    # H3 and H4 should both flag
    assert "H3" in ddd["fail_reasons"]
    assert "H4" in ddd["fail_reasons"]


def test_score_snapshot_idempotent(tmp_path: Path):
    biotech_db, conn = _seed(tmp_path)
    funds_db = _make_synthetic_funds_db(tmp_path)
    cfg = load_scoring_config(default_config_path())
    cfg.funds.db_path_relative_to_repo_root = str(funds_db.resolve())

    score_snapshot(SNAP, conn, cfg, project_root=PROJECT_ROOT)
    stats2 = score_snapshot(SNAP, conn, cfg, project_root=PROJECT_ROOT)
    assert stats2.rows_inserted == 0
    assert stats2.rows_updated == 5


def test_skip_funds_mode_renormalises(tmp_path: Path):
    biotech_db, conn = _seed(tmp_path)
    cfg = load_scoring_config(default_config_path())
    stats = score_snapshot(
        SNAP, conn, cfg,
        project_root=PROJECT_ROOT, skip_funds=True,
    )
    assert stats.funds_skipped is True
    aaa = conn.execute(
        "SELECT * FROM catalyst_scores WHERE ticker='AAA'"
    ).fetchone()
    assert aaa["fund_quarter_latest"] is None
    assert aaa["fund_accumulation_score"] == 0.0
    # composite must still be in [0, 100]
    assert 0 <= aaa["composite_score"] <= 100


def test_missing_funds_db_falls_back_to_skip(tmp_path: Path):
    biotech_db, conn = _seed(tmp_path)
    cfg = load_scoring_config(default_config_path())
    cfg.funds.db_path_relative_to_repo_root = str(tmp_path / "does_not_exist.db")
    stats = score_snapshot(SNAP, conn, cfg, project_root=PROJECT_ROOT, skip_funds=False)
    # should not raise; falls back to skip
    assert stats.funds_skipped is True
    assert stats.hard_pass_count == 2


def test_ingest_log_row_written(tmp_path: Path):
    biotech_db, conn = _seed(tmp_path)
    funds_db = _make_synthetic_funds_db(tmp_path)
    cfg = load_scoring_config(default_config_path())
    cfg.funds.db_path_relative_to_repo_root = str(funds_db.resolve())

    score_snapshot(SNAP, conn, cfg, project_root=PROJECT_ROOT)
    log_row = conn.execute(
        "SELECT module, status, rows_in, rows_inserted FROM ingest_log "
        "WHERE module = 'score_catalysts' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    assert log_row is not None
    assert log_row["module"] == "score_catalysts"
    assert log_row["status"] in ("success", "partial")
    assert log_row["rows_in"] == 5
    assert log_row["rows_inserted"] == 5
