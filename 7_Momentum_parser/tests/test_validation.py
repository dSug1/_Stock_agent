"""§9 validation — metrics, ledger settle pass, PIT backtest — offline."""

from momentum_parser import backtest, metrics, validation
from momentum_parser.models import Bar
from momentum_parser.store import Store

CFG = {
    "signals": {"min_bars": 40},
    "probability": {"horizon_days": 5, "target": {"vol_band_mult": 0.5, "vol_window_weeks": 12},
                    "beta": 2.2, "use_empirical": True, "empirical_min_samples": 5},
    "validation": {"min_samples": 100, "up_call_threshold": 0.5},
}


# --- metrics (pure) -----------------------------------------------------------------------

def test_brier_known_values():
    assert metrics.brier([(1.0, 1), (0.0, 0)]) == 0.0
    assert metrics.brier([(0.5, 1), (0.5, 0)]) == 0.25


def test_summary_beats_base_and_flags_underpowered():
    rows = [{"p_up": 0.8, "predicted_label": "up", "realized_label": "up"},
            {"p_up": 0.7, "predicted_label": "up", "realized_label": "up"},
            {"p_up": 0.2, "predicted_label": "flat", "realized_label": "flat"}]
    s = metrics.summary(rows, min_n=100)
    assert s["n"] == 3 and s["underpowered"] is True
    assert s["up_calls"] == 2 and s["up_call_hit_rate"] == 1.0
    assert s["beats_base_rate"] is True            # hit 1.0 > base 2/3
    assert sum(b["n"] for b in s["reliability"]) == 3


# --- settle pass --------------------------------------------------------------------------

def _store(tmp_path):
    return Store(tmp_path / "t.db")


def test_settle_pass_resolves_elapsed_prediction(tmp_path):
    s = _store(tmp_path)
    # 10 rising bars; predict at bar 0, horizon 5 -> resolvable
    s.upsert_bars("AAA", [Bar(f"2025-01-{i+1:02d}", 100 + 3 * i, 0, 0, 100 + 3 * i, 1000) for i in range(10)])
    s.append_ledger("AAA", "2025-01-01", "run1", 5, p_up=0.8, p_final=0.8,
                    predicted_label="up", sigma_week=0.02)
    out = validation.settle_pass(s, CFG)
    assert out["settled"] == 1 and out["still_open"] == 0
    led = s.settled_ledger()[0]
    # realized = close[5]/close[0]-1 = 115/100-1 = 0.15 > 0.5*0.02 -> up
    assert led["realized_label"] == "up" and abs(led["realized_return"] - 0.15) < 1e-9

    path, summ = validation.build_report(s, CFG)
    assert summ["n"] == 1 and summ["underpowered"] is True
    assert path.exists()


def test_settle_leaves_unelapsed_open(tmp_path):
    s = _store(tmp_path)
    s.upsert_bars("BBB", [Bar(f"2025-01-{i+1:02d}", 100, 0, 0, 100, 1000) for i in range(3)])
    s.append_ledger("BBB", "2025-01-01", "run1", 5, p_up=0.6, p_final=0.6,
                    predicted_label="up", sigma_week=0.02)
    out = validation.settle_pass(s, CFG)             # only 3 bars, horizon 5 -> can't settle
    assert out["settled"] == 0 and out["still_open"] == 1
    assert s.open_ledger() and not s.settled_ledger()


# --- backtest -----------------------------------------------------------------------------

def test_backtest_uptrend_high_up_rate(tmp_path):
    s = _store(tmp_path)
    s.upsert_bars("UP", [Bar(f"2025-{(i//28)+1:02d}-{(i%28)+1:02d}", 100 + i, 0, 0, 100 + i, 1000)
                         for i in range(120)])
    rows = backtest.backtest_ticker(s.get_bars("UP"), CFG)
    assert len(rows) > 0
    summ = metrics.summary(rows, min_n=100)
    assert summ["base_rate"] > 0.8                   # steady uptrend -> mostly 'up' outcomes
    assert summ["brier"] is not None
