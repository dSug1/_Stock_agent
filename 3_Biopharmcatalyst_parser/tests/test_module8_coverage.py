"""D39 — ticker-coverage gate unit tests."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_8.coverage import (  # noqa: E402
    apply_coverage_gate,
    tickers_with_existing_dispatch,
)


# ─── tickers_with_existing_dispatch ──────────────────────────────────


def test_returns_empty_when_db_missing(tmp_path):
    assert tickers_with_existing_dispatch(tmp_path / "missing.db") == frozenset()


def test_returns_empty_when_table_missing(tmp_path):
    db = tmp_path / "no_table.db"
    cx = sqlite3.connect(db)
    cx.execute("CREATE TABLE unrelated (x INTEGER)")
    cx.commit()
    cx.close()
    assert tickers_with_existing_dispatch(db) == frozenset()


@pytest.fixture
def dd_db(tmp_path):
    """A minimal deep_dives DB with a ticker column."""
    db = tmp_path / "dd.db"
    cx = sqlite3.connect(db)
    cx.execute("CREATE TABLE deep_dives (ticker TEXT, run_id INTEGER)")
    cx.commit()
    yield cx, db
    cx.close()


def test_returns_distinct_tickers(dd_db):
    cx, db_path = dd_db
    cx.executemany(
        "INSERT INTO deep_dives (ticker, run_id) VALUES (?, ?)",
        [("AAA", 1), ("AAA", 2), ("BBB", 1), ("ccc", 3), ("BBB", 4)],
    )
    cx.commit()
    out = tickers_with_existing_dispatch(db_path)
    assert out == frozenset({"AAA", "BBB", "CCC"})


def test_skips_blank_and_none(dd_db):
    cx, db_path = dd_db
    cx.executemany(
        "INSERT INTO deep_dives (ticker, run_id) VALUES (?, ?)",
        [("AAA", 1), ("", 2), ("  ", 3), (None, 4)],
    )
    cx.commit()
    out = tickers_with_existing_dispatch(db_path)
    assert out == frozenset({"AAA"})


def test_trims_whitespace_and_normalises_case(dd_db):
    cx, db_path = dd_db
    cx.executemany(
        "INSERT INTO deep_dives (ticker, run_id) VALUES (?, ?)",
        [("  aaa  ", 1), ("BBB", 2), ("Ccc", 3)],
    )
    cx.commit()
    assert tickers_with_existing_dispatch(db_path) == frozenset({"AAA", "BBB", "CCC"})


# ─── apply_coverage_gate ──────────────────────────────────────────────


def test_empty_covered_is_noop():
    cands = [{"ticker": "AAA"}, {"ticker": "BBB"}]
    kept, dropped = apply_coverage_gate(cands, frozenset())
    assert kept == cands
    assert dropped == []


def test_drops_covered_ticker():
    cands = [{"ticker": "AAA"}, {"ticker": "BBB"}, {"ticker": "CCC"}]
    kept, dropped = apply_coverage_gate(cands, frozenset({"BBB"}))
    assert len(kept) == 2
    assert {c["ticker"] for c in kept} == {"AAA", "CCC"}
    assert dropped[0]["ticker"] == "BBB"


def test_case_insensitive():
    cands = [{"ticker": "aaa"}, {"ticker": "BBB"}]
    kept, dropped = apply_coverage_gate(cands, frozenset({"AAA", "bbb"}))
    assert kept == []
    assert len(dropped) == 2


def test_drops_all_drugs_of_a_covered_ticker():
    """A ticker with multiple drug rows — all drugs drop when the ticker
    is in the covered set. Mirrors D38's ticker-level ack semantics."""
    cands = [
        {"ticker": "MRK", "drug": "X1"},
        {"ticker": "MRK", "drug": "X2"},
        {"ticker": "MRK", "drug": "X3"},
        {"ticker": "PFE", "drug": "Y1"},
    ]
    kept, dropped = apply_coverage_gate(cands, frozenset({"MRK"}))
    assert {c["drug"] for c in dropped} == {"X1", "X2", "X3"}
    assert [c["ticker"] for c in kept] == ["PFE"]


def test_missing_ticker_field_kept_defensively():
    cands = [{"drug": "no_ticker"}, {"ticker": "AAA"}]
    kept, dropped = apply_coverage_gate(cands, frozenset({"AAA"}))
    assert len(kept) == 1
    assert kept[0].get("drug") == "no_ticker"
    assert dropped[0]["ticker"] == "AAA"


def test_custom_ticker_key():
    cands = [{"sym": "AAA"}, {"sym": "BBB"}]
    kept, dropped = apply_coverage_gate(
        cands, frozenset({"AAA"}), ticker_key="sym",
    )
    assert kept == [{"sym": "BBB"}]
    assert dropped == [{"sym": "AAA"}]
