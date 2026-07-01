"""M13 — top-down term in p_model: logit apply, score math, β-init regression, end-to-end shift. Offline."""

from momentum_parser import loadings, model, probability, topdown_model
from momentum_parser.models import Bar
from momentum_parser.store import Store
from momentum_parser.taxonomy import load_taxonomy

CFG = {
    "signals": {"min_bars": 40},
    "probability": {"horizon_days": 5, "target": {"vol_band_mult": 0.5, "vol_window_weeks": 12},
                    "beta": 2.2, "use_empirical": False},
    "topdown": {"model_gain": 1.5, "beta_default": 0.0, "loadings_window": 40, "taxonomy_file": None},
}


# --- primitives ---------------------------------------------------------------------------------
def test_apply_logit_delta_is_monotone_and_noop_at_zero():
    assert probability.apply_logit_delta(0.5, 0.0) == 0.5           # exact no-op
    assert probability.apply_logit_delta(0.5, 1.0) > 0.5           # positive -> up
    assert probability.apply_logit_delta(0.5, -1.0) < 0.5          # negative -> down
    assert 0.01 <= probability.apply_logit_delta(0.5, 99) <= 0.99  # clamped


def test_ols_beta_recovers_slope():
    x = [0.01, -0.02, 0.03, -0.01, 0.02]
    y = [2 * v for v in x]                                         # perfect β=2
    assert abs(loadings.ols_beta(y, x) - 2.0) < 1e-6
    assert loadings.ols_beta([1, 1, 1], [0, 0, 0]) is None         # constant x -> None


# --- score math ---------------------------------------------------------------------------------
def test_topdown_score_market_beta_one_factor_uses_loading():
    active = [{"signal_id": "risk_regime", "surprise": 0.4},        # market scope -> β=1
              {"signal_id": "ai_crowding", "surprise": 0.5}]        # factor scope -> loading
    weights = {"risk_regime": 0.12, "ai_crowding": 0.14}
    scope = {"risk_regime": "market", "ai_crowding": "factor"}
    # no loading for ai_crowding -> beta_default 0 -> only the market signal contributes
    sc0, bd0 = topdown_model.topdown_score(active, weights, {}, scope, CFG)
    assert bd0 == {"risk_regime": round(0.12 * 1.0 * 0.4, 4)}
    # with a positive loading, the factor signal now contributes
    sc1, bd1 = topdown_model.topdown_score(active, weights, {"ai_crowding": 1.8}, scope, CFG)
    assert sc1 > sc0 and "ai_crowding" in bd1


def test_topdown_score_skips_missing_surprise():
    active = [{"signal_id": "fomc", "surprise": None}]
    sc, bd = topdown_model.topdown_score(active, {"fomc": 0.18}, {}, {"fomc": "market"}, CFG)
    assert sc == 0.0 and bd == {}


# --- β init regression --------------------------------------------------------------------------
def test_init_ticker_loadings_positive_for_correlated_name():
    tax = load_taxonomy()
    # AI basket rises, market flat -> ai spread positive; ticker tracks the AI basket -> β>0
    ai = [100.0 + i for i in range(50)]
    flat = [100.0] * 50
    series = {"ai_basket": ai, "market": flat, "growth": flat, "value": flat,
              "hibeta": flat, "lowvol": flat}
    loads = loadings.init_ticker_loadings(ai, series, tax, CFG)
    assert loads.get("ai_crowding", 0) > 0


# --- end-to-end: the harvest actually shifts p_model --------------------------------------------
def _load_ticker(s, t, closes):
    s.upsert_bars(t, [Bar(f"2025-{(i//28)+1:02d}-{(i%28)+1:02d}", c, c, c, c, 1e6)
                      for i, c in enumerate(closes)])


def test_stage5_style_topdown_shifts_p_model(tmp_path):
    s = Store(tmp_path / "t.db")
    tax = load_taxonomy(CFG)
    closes = [100.0 + (0.5 if i % 2 else -0.5) for i in range(60)]   # flat/oscillating -> p_plain ~ 0.5
    _load_ticker(s, "AAA", closes)

    # baseline logit (no harvest yet)
    base, _ = topdown_model.logit_for(s, "AAA", CFG, tax)
    assert base == 0.0

    # harvest a strong positive market-regime surprise + seed its weight
    s.upsert_macro_signal("2026-06-30", "risk_regime", active=True, surprise=0.4, regime="risk_on")
    s.set_signal_weight("risk_regime", "risk_on", w=0.12, w_prior=0.12, n=0, updated_at="t")
    logit, bd = topdown_model.logit_for(s, "AAA", CFG, tax)
    assert logit > 0.0 and "risk_regime" in bd

    bars = s.get_bars("AAA")
    p_plain, _, _ = model.p_up(bars, CFG, topdown_logit=0.0)
    p_td, _, comps = model.p_up(bars, CFG, topdown_logit=logit)
    assert p_td > p_plain                                          # risk-on tailwind lifted p_model
    assert comps["topdown_logit"] == logit
