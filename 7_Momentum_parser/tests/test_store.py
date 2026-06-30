"""Store round-trip (in-memory / tmp sqlite)."""

from momentum_parser.models import Bar, Prediction, Signal
from momentum_parser.store import Store


def _store(tmp_path):
    return Store(tmp_path / "t.db")


def test_bars_upsert_and_read(tmp_path):
    s = _store(tmp_path)
    bars = [Bar("2025-01-01", 1, 2, 0.5, 1.5, 100), Bar("2025-01-02", 1.5, 2, 1, 1.8, 120)]
    assert s.upsert_bars("AAA", bars) == 2
    s.upsert_bars("AAA", [Bar("2025-01-02", 1.5, 2, 1, 9.9, 120)])   # overwrite same key
    got = s.get_bars("AAA")
    assert len(got) == 2 and got[-1].close == 9.9
    assert s.latest_bar_date("AAA") == "2025-01-02"


def test_prediction_roundtrip_and_ranking(tmp_path):
    s = _store(tmp_path)
    for tk, p in [("LOW", 0.4), ("HIGH", 0.8)]:
        s.write_prediction(Prediction(tk, "2025-01-02", 5, p, 0.01, 0.6, p,
                                      [Signal("roc", 0.1, 0.5, True)], 10.0), "run_x")
    rows = s.predictions_for_run("run_x")
    assert [r["ticker"] for r in rows] == ["HIGH", "LOW"]      # ordered by p_up desc
    assert s.latest_run_id() == "run_x"


def test_signals_upsert(tmp_path):
    s = _store(tmp_path)
    s.write_signals("AAA", "2025-01-02", [Signal("rsi", 70, 0.4, True, "RSI 70")])
    s.write_signals("AAA", "2025-01-02", [Signal("rsi", 30, -0.4, True, "RSI 30")])  # overwrite
    row = s.conn.execute("SELECT value FROM signals WHERE ticker='AAA' AND name='rsi'").fetchone()
    assert row["value"] == 30


# --- schema v2 (spec D-2) -----------------------------------------------------------------

def test_schema_version(tmp_path):
    from momentum_parser.store import SCHEMA_VERSION
    s = _store(tmp_path)
    assert s.conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_evidence_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.upsert_evidence("AAA", "2025-01-02", "media", 0.6, {"volume": 12, "tone": 0.3}, "rising")
    s.upsert_evidence("AAA", "2025-01-02", "media", 0.9, {"volume": 40}, "spike")  # overwrite
    rows = s.get_evidence("AAA", "2025-01-02")
    assert len(rows) == 1 and rows[0]["score"] == 0.9


def test_catalysts_next_is_forward(tmp_path):
    s = _store(tmp_path)
    s.upsert_catalysts("AAA", [
        ("2025-02-01", "pdufa", "FDA decision", "fda"),
        ("2025-01-10", "readout", "Phase 2 data", "ct.gov"),
    ])
    nxt = s.next_catalyst("AAA", "2025-01-05")
    assert nxt["event_date"] == "2025-01-10" and nxt["kind"] == "readout"
    assert s.next_catalyst("AAA", "2025-03-01") is None     # nothing forward


def test_scores_roundtrip_and_ranking(tmp_path):
    s = _store(tmp_path)
    for tk, p in [("LOW", 0.3), ("HIGH", 0.8)]:
        s.write_score(tk, "2025-01-02", "run_x", tier="sonnet", p_up=p, p_down=0.1, p_flat=0.1,
                      expected_return=0.02, conviction=0.7, memo="m", config_hash="h",
                      evidence_fingerprint="f", prompt_version="v1")
    rows = s.scores_for_run("run_x")
    assert [r["ticker"] for r in rows] == ["HIGH", "LOW"]


def test_ledger_open_then_settle(tmp_path):
    s = _store(tmp_path)
    s.append_ledger("AAA", "2025-01-02", "run_x", 5, p_up=0.7, p_final=0.66,
                    predicted_label="up", sigma_week=0.04)
    assert len(s.open_ledger()) == 1
    s.settle_ledger("AAA", "2025-01-02", "run_x", realized_return=0.06,
                    realized_label="up", scored_at="2025-01-09")
    assert s.open_ledger() == []
    row = s.conn.execute("SELECT realized_label FROM ledger WHERE ticker='AAA'").fetchone()
    assert row["realized_label"] == "up"
