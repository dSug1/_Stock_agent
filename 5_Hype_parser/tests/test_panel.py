"""Tests for the labeled panel, forward-return engine, and kill-switch harness."""

import numpy as np
import pytest

from hype_parser import db, killswitch, panel, prices


def _conn(tmp_path):
    return db.connect(tmp_path / "p.db")


# ---------- forward-return engine ----------

def _series(start_iso, prices_list, step_days=7):
    from datetime import date, timedelta
    d = date.fromisoformat(start_iso)
    out = []
    for i, p in enumerate(prices_list):
        out.append(((d + timedelta(days=i * step_days)).isoformat(), float(p)))
    return out


def test_forward_returns_basic():
    # weekly closes from t0; 100 -> ... so 13w later is index 13
    series = _series("2024-01-01", [100 + i for i in range(60)])  # 100,101,...
    fetch = lambda tk, s, e: [(d, p) for d, p in series if d <= e]
    res = prices.forward_returns("X", "2024-01-01", [13, 26], fetch=fetch, today="2025-01-01")
    assert set(res) == {13, 26}
    assert res[13]["start_price"] == 100
    assert res[13]["fwd_return"] == pytest.approx((113 - 100) / 100, abs=1e-6)
    assert res[13]["max_drawup"] >= res[13]["fwd_return"]
    assert res[13]["max_drawdown"] <= 0 + 1e-9            # monotonic up -> ~0 drawdown


def test_forward_returns_skips_unelapsed_horizon():
    # only ~10 weeks of data -> 26w horizon must be skipped
    series = _series("2024-01-01", [100 + i for i in range(10)])
    fetch = lambda tk, s, e: [(d, p) for d, p in series if d <= e]
    res = prices.forward_returns("X", "2024-01-01", [4, 26], fetch=fetch, today="2024-04-01")
    assert 4 in res and 26 not in res


def test_forward_returns_empty():
    assert prices.forward_returns("X", "2024-01-01", [13], fetch=lambda *a: []) == {}


def test_forward_returns_captures_drawup_drawdown():
    series = _series("2024-01-01", [100, 80, 200, 150])    # down then up
    fetch = lambda tk, s, e: [(d, p) for d, p in series if d <= e]
    res = prices.forward_returns("X", "2024-01-01", [3], fetch=fetch, today="2025-01-01")
    assert res[3]["max_drawdown"] == pytest.approx(-0.20)
    assert res[3]["max_drawup"] == pytest.approx(1.00)


# ---------- panel storage ----------

def test_seed_and_label_validation(tmp_path):
    conn = _conn(tmp_path)
    pid = panel.upsert_panel_row(conn, {"ticker": "ELTX", "t0_date": "2024-06-03",
                                        "label": "positive", "regime": "A"})
    assert isinstance(pid, int)
    with pytest.raises(ValueError):
        panel.upsert_panel_row(conn, {"ticker": "X", "t0_date": "2024-01-01", "label": "bogus"})
    conn.close()


def test_seed_anchors_idempotent(tmp_path):
    conn = _conn(tmp_path)
    anchors = [{"ticker": "ROKU", "t0_date": "2019-01-02", "label": "positive"},
               {"ticker": "TCRX", "t0_date": "2025-12-01", "label": "hard_negative"}]
    panel.seed_anchors(conn, anchors)
    panel.seed_anchors(conn, anchors)
    rows = panel.list_panel(conn)
    assert len(rows) == 2
    assert all(r["label_source"] == "pre_registered" for r in rows)
    conn.close()


def test_returns_and_features_storage(tmp_path):
    conn = _conn(tmp_path)
    pid = panel.upsert_panel_row(conn, {"ticker": "NET", "t0_date": "2020-01-02",
                                        "label": "positive"})
    panel.write_returns(conn, pid, 52, {"start_price": 18.0, "end_price": 75.0,
                                        "fwd_return": 3.17, "max_drawup": 4.0,
                                        "max_drawdown": -0.1})
    rr = panel.read_returns(conn, pid)
    assert rr[0]["fwd_return"] == pytest.approx(3.17)
    panel.set_feature(conn, pid, "narrative", 0.8)
    panel.set_feature(conn, pid, "narrative", 0.9)            # upsert
    assert panel.read_features(conn, pid)["narrative"] == 0.9
    conn.close()


def test_schema_v6(tmp_path):
    conn = _conn(tmp_path)
    assert db.current_version(conn) >= 6
    conn.close()


# ---------- kill-switch harness ----------

def test_ols_recovers_known_slope():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 1, 200)
    y = 2.0 + 3.0 * x + rng.normal(0, 0.01, 200)
    X = np.column_stack([np.ones(200), x])
    beta, se, t = killswitch.ols(X, y)
    assert beta[1] == pytest.approx(3.0, abs=0.05)
    assert abs(t[1]) > 10


def test_killswitch_underpowered_below_min_n():
    rows = [{"fwd_return": 1.0, "narrative": 0.9}, {"fwd_return": -0.1, "narrative": 0.1}]
    v = killswitch.run(rows, narrative_key="narrative", min_n=100)
    assert v["underpowered"] is True and v["passed"] is False


def test_killswitch_detects_positive_signal():
    # narrative strongly predicts forward return; n>=min_n -> should pass
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(150):
        nar = float(rng.uniform(0, 1))
        rows.append({"fwd_return": 2.0 * nar + float(rng.normal(0, 0.1)), "narrative": nar})
    v = killswitch.run(rows, narrative_key="narrative", min_n=100, t_threshold=2.0)
    assert v["sign_ok"] and not v["underpowered"] and v["passed"]
    assert v["narrative_coef"] == pytest.approx(2.0, abs=0.2)


def test_killswitch_rejects_no_signal():
    rng = np.random.default_rng(2)
    rows = [{"fwd_return": float(rng.normal(0, 1)), "narrative": float(rng.uniform(0, 1))}
            for _ in range(150)]
    v = killswitch.run(rows, narrative_key="narrative", min_n=100)
    assert not v["passed"]            # random -> no significant positive coefficient


def test_killswitch_no_features():
    rows = [{"fwd_return": 1.0, "narrative": None}]
    v = killswitch.run(rows, narrative_key="narrative")
    assert v["n"] == 0 and v["passed"] is False
