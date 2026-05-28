"""Module 6.5 — fundamentals.db schema + write helpers unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_6_5.fundamentals_db import (  # noqa: E402
    capital_raises_for_tickers_since,
    db_connect,
    fetch_log_for_tickers,
    get_fetch_log,
    init_fundamentals_db,
    latest_financials_for_tickers,
    upsert_capital_raise,
    upsert_fetch_log,
    upsert_financials_row,
)


def test_schema_creates_three_tables(tmp_path: Path):
    db = tmp_path / "fundamentals.db"
    init_fundamentals_db(db)
    with db_connect(db) as cx:
        rows = cx.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    names = {r["name"] for r in rows}
    expected = {"financials", "capital_raises", "fetch_log"}
    assert expected.issubset(names)


def test_init_is_idempotent(tmp_path: Path):
    db = tmp_path / "fundamentals.db"
    init_fundamentals_db(db)
    init_fundamentals_db(db)


def test_upsert_financials_round_trip(tmp_path: Path):
    db = tmp_path / "fundamentals.db"
    init_fundamentals_db(db)
    row = {
        "ticker": "TCRX", "cik": "0001234567",
        "period": "2026-Q1", "period_end_date": "2026-03-31",
        "form": "10-Q",
        "cash_and_equivalents_usd": 100_000_000,
        "short_term_investments_usd": 78_000_000,
        "cash_total_usd": 178_000_000,
        "basic_shares_count": 38_500_000,
        "prefunded_warrants_count": 4_200_000,
        "fully_diluted_shares_count": 42_700_000,
        "pfw_source": "capital_raises_sum_2yr",
        "pfw_share_dilution_warning": 0,
        "last_price_usd": 24.13,
        "last_price_as_of": "2026-05-28",
        "market_cap_fdsc_usd": 1_030_351_000.0,
        "companyfacts_raw_json": '{"facts": {}}',
        "fetched_at": "2026-05-28T18:00:00",
        "fetch_status": "ok",
    }
    with db_connect(db) as cx:
        upsert_financials_row(cx, row)
        latest = latest_financials_for_tickers(cx, ["TCRX"])
    assert "TCRX" in latest
    assert latest["TCRX"]["basic_shares_count"] == 38_500_000
    assert latest["TCRX"]["market_cap_fdsc_usd"] == 1_030_351_000.0
    assert latest["TCRX"]["fully_diluted_shares_count"] == 42_700_000


def test_upsert_capital_raise_round_trip(tmp_path: Path):
    db = tmp_path / "fundamentals.db"
    init_fundamentals_db(db)
    row = {
        "ticker": "TCRX", "cik": "0001234567",
        "accession_number": "0001234567-26-000001",
        "filing_date": "2026-04-15", "event_date": "2026-04-15",
        "form": "424B5", "raise_type": "pfw",
        "gross_proceeds_usd": 120_000_000,
        "shares_issued": 6_000_000,
        "price_per_share_usd": 20.0,
        "description": "PFW offering",
        "raw_filing_url": "https://www.sec.gov/...",
        "fetched_at": "2026-05-28T18:00:00",
        "fetch_status": "ok",
    }
    with db_connect(db) as cx:
        upsert_capital_raise(cx, row)
        out = capital_raises_for_tickers_since(cx, ["TCRX"], "2026-01-01")
    assert len(out["TCRX"]) == 1
    assert out["TCRX"][0]["raise_type"] == "pfw"
    assert out["TCRX"][0]["shares_issued"] == 6_000_000


def test_capital_raises_filter_by_type(tmp_path: Path):
    db = tmp_path / "fundamentals.db"
    init_fundamentals_db(db)
    base = {
        "ticker": "TCRX", "cik": "0001234567",
        "filing_date": "2026-04-15", "event_date": "2026-04-15",
        "fetched_at": "2026-05-28T18:00:00", "fetch_status": "ok",
        "gross_proceeds_usd": 100, "shares_issued": 10,
    }
    with db_connect(db) as cx:
        upsert_capital_raise(cx, {**base, "accession_number": "a1",
                                  "form": "424B5", "raise_type": "pfw"})
        upsert_capital_raise(cx, {**base, "accession_number": "a2",
                                  "form": "S-3", "raise_type": "shelf"})
        upsert_capital_raise(cx, {**base, "accession_number": "a3",
                                  "form": "424B5", "raise_type": "equity"})
        only_pfw = capital_raises_for_tickers_since(
            cx, ["TCRX"], "2026-01-01", raise_type="pfw",
        )
    assert len(only_pfw["TCRX"]) == 1
    assert only_pfw["TCRX"][0]["accession_number"] == "a1"


def test_fetch_log_upsert_coalesces_etag(tmp_path: Path):
    """A second upsert with etag=None must NOT clobber a previously stored etag."""
    db = tmp_path / "fundamentals.db"
    init_fundamentals_db(db)
    with db_connect(db) as cx:
        upsert_fetch_log(cx, ticker="TCRX", source="companyfacts",
                         last_fetched_at="2026-05-28T18:00:00",
                         last_status="ok", etag='"abc"', last_modified="Wed")
        upsert_fetch_log(cx, ticker="TCRX", source="companyfacts",
                         last_fetched_at="2026-05-29T18:00:00",
                         last_status="ok", etag=None, last_modified=None)
        log = get_fetch_log(cx, ["TCRX"])
    row = log[("TCRX", "companyfacts")]
    assert row["last_fetched_at"] == "2026-05-29T18:00:00"
    assert row["etag"] == '"abc"'
    assert row["last_modified"] == "Wed"


def test_fetch_log_for_tickers_groups_by_source(tmp_path: Path):
    db = tmp_path / "fundamentals.db"
    init_fundamentals_db(db)
    with db_connect(db) as cx:
        upsert_fetch_log(cx, ticker="TCRX", source="companyfacts",
                         last_fetched_at="2026-05-28T18:00:00", last_status="ok")
        upsert_fetch_log(cx, ticker="TCRX", source="capital_raises",
                         last_fetched_at="2026-05-28T18:01:00", last_status="ok")
        out = fetch_log_for_tickers(cx, ["TCRX"])
    assert set(out["TCRX"].keys()) == {"companyfacts", "capital_raises"}
