"""M16 — the feedback loop: skill/weight math, attribution, catalyst settlement, end-to-end learning. Offline."""

from momentum_parser import feedback
from momentum_parser.models import Bar
from momentum_parser.store import Store
from momentum_parser.taxonomy import load_taxonomy


# --- pure math ----------------------------------------------------------------------------------
def test_signal_skill_shrinks_to_zero_at_small_n():
    assert feedback.signal_skill(0, 0, 30) == 0.0
    assert abs(feedback.signal_skill(2, 2, 30)) < 0.1          # 2/2 hits but shrunk hard -> ~0
    assert feedback.signal_skill(100, 100, 30) > 0.7          # lots of perfect hits -> high skill
    assert feedback.signal_skill(0, 100, 30) < -0.7          # always wrong -> negative skill


def test_updated_weight_rewards_skill_and_zeros_anti_signal():
    # no skill (50/50) -> weight ~ prior
    assert abs(feedback.updated_weight(0.1, 50, 100, 30, 0.5) - 0.1) < 0.02
    # strong skill -> weight rises, capped at w_max
    assert feedback.updated_weight(0.3, 100, 100, 30, 0.5) == 0.5
    # anti-predictive -> weight collapses toward 0
    assert feedback.updated_weight(0.2, 0, 100, 30, 0.5) < 0.05


def test_attribute_splits_market_and_factor():
    active = [{"signal_id": "risk_regime", "surprise": 0.4}, {"signal_id": "ai_crowding", "surprise": 0.5}]
    scope = {"risk_regime": "market", "ai_crowding": "factor"}
    att = feedback.attribute(active, {"risk_regime": 0.1, "ai_crowding": 0.1},
                             {"ai_crowding": 2.0}, scope, realized_return=0.03)
    assert att["market_comp"] == round(0.1 * 1.0 * 0.4, 4)
    assert att["factor_comp"] == round(0.1 * 2.0 * 0.5, 4)
    assert att["idio_comp"] == round(0.03 - att["market_comp"] - att["factor_comp"], 4)


# --- catalyst settlement ------------------------------------------------------------------------
def test_settle_catalyst_hypotheses(tmp_path):
    s = Store(tmp_path / "t.db")
    bars = [Bar(f"2026-06-{i + 1:02d}", 100 + i, 100 + i, 100 + i, 100 + i, 1e6) for i in range(10)]
    s.upsert_bars("AAA", bars)
    s.upsert_catalyst_hypothesis("AAA", "2026-06-01", "2026-06-04", "earnings",
                                 {"days_to_catalyst": 3, "accumulation": 0.4, "expected_drift": 0.02,
                                  "dispersion": 0.05, "horizon_days": 5})
    out = feedback.settle_catalyst_hypotheses(s, {}, now="t", log=lambda m: None)
    assert out["catalysts_settled"] == 1
    row = s.conn.execute("SELECT realized_drift FROM catalyst_hypotheses").fetchone()
    assert abs(row["realized_drift"] - (bars[5].close / bars[0].close - 1.0)) < 1e-6
    assert s.open_catalyst_hypotheses() == []


# --- end-to-end learning ------------------------------------------------------------------------
def _settled_pred(s, ticker, asof, run_id, realized):
    s.append_ledger(ticker, asof, run_id, 5, p_up=0.6, p_final=0.6, predicted_label="up", sigma_week=0.05)
    s.settle_ledger(ticker, asof, run_id, realized_return=realized, realized_label="up", scored_at="t")


def test_learn_updates_weights_and_records_attribution(tmp_path):
    s = Store(tmp_path / "t.db")
    tax = load_taxonomy()
    s.seed_signal_weights(tax)
    # a market-scope signal that anticipated the move (positive surprise, positive realized)
    s.upsert_macro_signal("2026-06-01", "risk_regime", active=True, surprise=0.4, regime="risk_on")
    _settled_pred(s, "AAA", "2026-06-01", "run1", realized=0.03)

    out = feedback.learn(s, {"topdown": {"weight_max": 0.5}}, tax, now="t", log=lambda m: None)
    assert out["settled_used"] == 1 and out["weights_updated"] >= 2   # risk_regime under both regime + 'all'

    w = s.get_signal_weight("risk_regime", "risk_on")
    assert w["n"] == 1 and w["w"] >= 0                                # a weight row was learned (n recorded)
    att = s.get_attribution("run1")
    assert att and att[0]["realized_return"] == 0.03                  # attribution recorded


def test_run_is_graceful_with_no_data(tmp_path):
    s = Store(tmp_path / "t.db")
    out = feedback.run(s, {"topdown": {"taxonomy_file": None}}, now="t", log=lambda m: None)
    assert out["catalysts_settled"] == 0 and out["settled_used"] == 0   # nothing to do -> no-op, no crash
