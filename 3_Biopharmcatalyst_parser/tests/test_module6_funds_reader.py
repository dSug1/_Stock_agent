"""Module 6 — funds_reader unit tests against a synthetic mini funds DB."""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6.funds_reader import (  # noqa: E402
    FundsDBError,
    attach_funds_db,
    detach_funds_db,
    load_fund_accumulation,
    resolve_funds_db_path,
)


def _make_funds_db(tmp_path: Path) -> Path:
    """Create a tiny funds DB with two quarters and three tickers."""
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
    -- Q_LATEST=2026-03-31, Q_PREV=2025-12-31
    -- AAA: fund 1 doubled (10k->20k @ $10 = +$100k), fund 2 new (0->5k @ $10 = +$50k)
    INSERT INTO holdings (fund_id, period_of_report, ticker, shares, market_value) VALUES
        (1, '2025-12-31', 'AAA', 10000, 100000),
        (1, '2026-03-31', 'AAA', 20000, 200000),
        (2, '2026-03-31', 'AAA',  5000,  50000),
    -- BBB: fund 1 trimmed (8k->4k); fund 3 unchanged (1k @ $5); no positive accumulation
        (1, '2025-12-31', 'BBB', 8000, 40000),
        (1, '2026-03-31', 'BBB', 4000, 20000),
        (3, '2025-12-31', 'BBB', 1000,  5000),
        (3, '2026-03-31', 'BBB', 1000,  5000),
    -- CCC: only in prev quarter (exited) — should produce 0 positive accumulation
        (2, '2025-12-31', 'CCC', 3000, 15000);
    """)
    fconn.commit()
    fconn.close()
    return p


def test_attach_missing_file_raises(tmp_path: Path):
    conn = sqlite3.connect(":memory:")
    bogus = tmp_path / "nope.db"
    with pytest.raises(FundsDBError):
        attach_funds_db(conn, bogus)
    conn.close()


def test_attach_and_detach_idempotent(tmp_path: Path):
    fpath = _make_funds_db(tmp_path)
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    attach_funds_db(conn, fpath)
    n = conn.execute("SELECT COUNT(*) FROM funds.holdings").fetchone()[0]
    assert n == 8
    detach_funds_db(conn)
    detach_funds_db(conn)  # second call must not raise
    conn.close()


def test_load_fund_accumulation_AAA_doubling_and_new_position(tmp_path: Path):
    fpath = _make_funds_db(tmp_path)
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    attach_funds_db(conn, fpath)
    ctx = load_fund_accumulation(
        conn,
        tickers=["AAA", "BBB", "CCC"],
        snapshot_date=date(2026, 5, 27),
        stale_warning_days=180,
    )
    assert ctx.quarter_latest == "2026-03-31"
    assert ctx.quarter_previous == "2025-12-31"
    assert ctx.stale is False

    aaa = ctx.rows_by_ticker["AAA"]
    # fund 1: +10k shares @ $10 ($200k/20k) = $100k
    # fund 2: +5k  shares @ $10 ($50k/5k)   = $50k
    assert aaa.fund_accumulation_usd == pytest.approx(150_000.0)
    assert aaa.funds_holding_latest == 2
    assert aaa.funds_holding_previous == 1


def test_load_fund_accumulation_BBB_trimmed_zero(tmp_path: Path):
    fpath = _make_funds_db(tmp_path)
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    attach_funds_db(conn, fpath)
    ctx = load_fund_accumulation(
        conn, tickers=["BBB"],
        snapshot_date=date(2026, 5, 27), stale_warning_days=180,
    )
    # fund 1: trimmed -> 0 contribution
    # fund 3: unchanged -> 0 contribution
    assert ctx.rows_by_ticker["BBB"].fund_accumulation_usd == 0.0
    assert ctx.rows_by_ticker["BBB"].funds_holding_latest == 2
    assert ctx.rows_by_ticker["BBB"].funds_holding_previous == 2


def test_load_fund_accumulation_CCC_exit_only_no_score(tmp_path: Path):
    fpath = _make_funds_db(tmp_path)
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    attach_funds_db(conn, fpath)
    ctx = load_fund_accumulation(
        conn, tickers=["CCC"],
        snapshot_date=date(2026, 5, 27), stale_warning_days=180,
    )
    # Exit only — no positive deltas
    row = ctx.rows_by_ticker["CCC"]
    assert row.fund_accumulation_usd == 0.0
    assert row.funds_holding_latest == 0
    assert row.funds_holding_previous == 1


def test_ticker_absent_from_funds_db_not_in_result(tmp_path: Path):
    fpath = _make_funds_db(tmp_path)
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    attach_funds_db(conn, fpath)
    ctx = load_fund_accumulation(
        conn, tickers=["ZZZ_NOTREAL"],
        snapshot_date=date(2026, 5, 27), stale_warning_days=180,
    )
    assert "ZZZ_NOTREAL" not in ctx.rows_by_ticker


def test_stale_flag_set_when_quarter_is_old(tmp_path: Path):
    fpath = _make_funds_db(tmp_path)
    conn = sqlite3.connect(":memory:"); conn.row_factory = sqlite3.Row
    attach_funds_db(conn, fpath)
    # snapshot 200 days after q_latest 2026-03-31
    ctx = load_fund_accumulation(
        conn, tickers=["AAA"],
        snapshot_date=date(2026, 12, 1), stale_warning_days=180,
    )
    assert ctx.stale is True


def test_resolve_funds_db_path():
    project_root = Path("/x/_Stock_agent/3_Biopharmcatalyst_parser")
    p = resolve_funds_db_path(project_root, "2_Funds_parser/2_fundparser.db")
    assert p == Path("/x/_Stock_agent/2_Funds_parser/2_fundparser.db")
