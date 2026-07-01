"""Store schema v7 — the top-down tables migrate idempotently + round-trip (M11, §4b / §10 v0.4)."""

from momentum_parser.store import SCHEMA_VERSION, Store
from momentum_parser.taxonomy import load_taxonomy


def _uv(s):
    return s.conn.execute("PRAGMA user_version").fetchone()[0]


def test_migration_reaches_v7_and_is_idempotent(tmp_path):
    p = tmp_path / "t.db"
    s = Store(p)
    assert SCHEMA_VERSION >= 7 and _uv(s) == SCHEMA_VERSION      # v7 tables exist at/after v7
    s.close()
    s2 = Store(p)                                          # re-open: migration must be a no-op
    assert _uv(s2) == SCHEMA_VERSION
    for t in ("macro_signals", "signal_weights", "ticker_loadings", "attribution"):
        assert s2.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()


def test_macro_signal_roundtrip_and_active_filter(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_macro_signal("2026-06-30", "fomc", active=True, surprise=0.3, regime="risk_on", horizon_days=3)
    s.upsert_macro_signal("2026-06-30", "risk_regime", active=False, surprise=-0.1, regime="neutral")
    allrows = s.get_macro_signals("2026-06-30")
    assert {r["signal_id"] for r in allrows} == {"fomc", "risk_regime"}
    active = s.get_macro_signals("2026-06-30", active_only=True)
    assert [r["signal_id"] for r in active] == ["fomc"]
    # upsert overwrites in place (idempotent per asof/signal)
    s.upsert_macro_signal("2026-06-30", "fomc", active=True, surprise=0.9, regime="risk_on", horizon_days=1)
    assert s.get_macro_signals("2026-06-30", active_only=True)[0]["surprise"] == 0.9


def test_seed_weights_from_priors_then_never_clobbered(tmp_path):
    s = Store(tmp_path / "t.db")
    tax = load_taxonomy()
    n = s.seed_signal_weights(tax)
    assert n > 0
    # seeded 'all' + each regime for every signal
    from momentum_parser.taxonomy import regime_buckets
    assert n == len(tax["signals"]) * len(regime_buckets(tax))
    w0 = s.get_signal_weight("fomc", "all")
    assert w0["w"] == w0["w_prior"] and w0["n"] == 0     # starts at the prior

    # the loop learns a value...
    s.set_signal_weight("fomc", "all", w=0.42, w_prior=w0["w_prior"], n=120, updated_at="t1")
    # ...and a later re-seed must NOT overwrite it
    s.seed_signal_weights(tax)
    learned = s.get_signal_weight("fomc", "all")
    assert learned["w"] == 0.42 and learned["n"] == 120


def test_signal_weight_regime_fallback(tmp_path):
    s = Store(tmp_path / "t.db")
    s.set_signal_weight("cpi", "all", w=0.15, w_prior=0.15, n=0, updated_at="t")
    # no risk_off-specific row yet -> falls back to 'all'
    assert s.get_signal_weight("cpi", "risk_off")["w"] == 0.15
    s.set_signal_weight("cpi", "risk_off", w=0.25, w_prior=0.15, n=40, updated_at="t")
    assert s.get_signal_weight("cpi", "risk_off")["w"] == 0.25   # now the specific one wins


def test_ticker_loading_and_attribution_roundtrip(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_ticker_loading("NVDA", "ai_crowding", beta=1.8, beta_prior=1.0, n=30, updated_at="t")
    s.upsert_ticker_loading("NVDA", "rates_usd", beta=-0.4, beta_prior=0.0, n=30, updated_at="t")
    loadings = {r["signal_id"]: r["beta"] for r in s.get_ticker_loadings("NVDA")}
    assert loadings == {"ai_crowding": 1.8, "rates_usd": -0.4}

    s.upsert_attribution("NVDA", "2026-06-30", "run1", realized_return=0.05,
                         market_comp=0.02, factor_comp=0.025, idio_comp=0.005,
                         contributions={"ai_crowding": 0.02}, settled_at="t")
    a = s.get_attribution("run1")[0]
    assert a["market_comp"] == 0.02 and a["idio_comp"] == 0.005
