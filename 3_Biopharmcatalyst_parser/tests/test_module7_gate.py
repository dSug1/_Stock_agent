"""D38 — pre-dispatch ticker gate unit tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.gate import (  # noqa: E402
    apply_ticker_gate,
    load_acknowledged_tickers,
    save_acknowledged_tickers,
)


# ─── apply_ticker_gate ───────────────────────────────────────────────


def test_empty_ack_is_noop_pass_through():
    cands = [{"ticker": "AAA"}, {"ticker": "BBB"}]
    kept, dropped = apply_ticker_gate(cands, frozenset())
    assert kept == cands
    assert dropped == []


def test_drops_matching_ticker():
    cands = [{"ticker": "AAA"}, {"ticker": "BBB"}, {"ticker": "CCC"}]
    kept, dropped = apply_ticker_gate(cands, frozenset({"BBB"}))
    assert len(kept) == 2
    assert {c["ticker"] for c in kept} == {"AAA", "CCC"}
    assert len(dropped) == 1
    assert dropped[0]["ticker"] == "BBB"


def test_case_insensitive_ticker_match():
    cands = [{"ticker": "aaa"}, {"ticker": "BBB"}]
    kept, dropped = apply_ticker_gate(cands, frozenset({"AAA", "bbb"}))
    assert len(kept) == 0
    assert len(dropped) == 2


def test_drops_all_rows_of_a_ticker():
    """Multiple catalysts/drugs for the same ticker — all dropped."""
    cands = [
        {"ticker": "MRK", "drug": "KEYTRUDA"},
        {"ticker": "MRK", "drug": "WELIREG"},
        {"ticker": "MRK", "drug": "REYVOW"},
        {"ticker": "PFE", "drug": "IBRANCE"},
    ]
    kept, dropped = apply_ticker_gate(cands, frozenset({"MRK"}))
    assert {c["drug"] for c in dropped} == {"KEYTRUDA", "WELIREG", "REYVOW"}
    assert [c["ticker"] for c in kept] == ["PFE"]


def test_missing_ticker_field_kept_defensively():
    cands = [{"drug": "no_ticker"}, {"ticker": "AAA"}]
    kept, dropped = apply_ticker_gate(cands, frozenset({"AAA"}))
    assert len(kept) == 1
    assert kept[0].get("drug") == "no_ticker"
    assert dropped[0]["ticker"] == "AAA"


def test_blank_ticker_field_kept_defensively():
    cands = [{"ticker": "   "}, {"ticker": "AAA"}]
    kept, dropped = apply_ticker_gate(cands, frozenset({"AAA"}))
    assert any((c.get("ticker") or "").strip() == "" for c in kept)


def test_custom_ticker_key():
    cands = [{"symbol": "AAA"}, {"symbol": "BBB"}]
    kept, dropped = apply_ticker_gate(
        cands, frozenset({"AAA"}), ticker_key="symbol",
    )
    assert kept == [{"symbol": "BBB"}]
    assert dropped == [{"symbol": "AAA"}]


# ─── load/save round-trip ────────────────────────────────────────────


def test_load_returns_empty_when_file_missing(tmp_path):
    path = tmp_path / "missing.json"
    assert load_acknowledged_tickers(path) == frozenset()


def test_load_returns_empty_on_corrupt_json(tmp_path):
    path = tmp_path / "corrupt.json"
    path.write_text("{this is not json", encoding="utf-8")
    assert load_acknowledged_tickers(path) == frozenset()


def test_load_returns_empty_on_wrong_shape(tmp_path):
    """Body must be an object with 'tickers' = list."""
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(["AAA", "BBB"]), encoding="utf-8")
    assert load_acknowledged_tickers(path) == frozenset()


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "ack.json"
    save_acknowledged_tickers({"AAA", "bbb", "  CCC  "}, path=path)
    loaded = load_acknowledged_tickers(path)
    assert loaded == frozenset({"AAA", "BBB", "CCC"})


def test_save_dedupes_and_normalises(tmp_path):
    path = tmp_path / "ack.json"
    save_acknowledged_tickers(
        ["aaa", "AAA", "Aaa", "BBB", "  "], path=path,
    )
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["tickers"] == ["AAA", "BBB"]
    assert "saved_at_utc" in body


def test_save_atomic_via_tmp_file(tmp_path):
    """Verify the .tmp file gets cleaned up via rename."""
    path = tmp_path / "ack.json"
    save_acknowledged_tickers({"AAA"}, path=path)
    assert path.exists()
    assert not (tmp_path / "ack.json.tmp").exists()


def test_load_ignores_non_string_entries(tmp_path):
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps({
        "saved_at_utc": "2026-01-01T00:00:00",
        "tickers": ["AAA", 123, None, "BBB", "", "  ", "ccc"],
    }), encoding="utf-8")
    assert load_acknowledged_tickers(path) == frozenset({"AAA", "BBB", "CCC"})
