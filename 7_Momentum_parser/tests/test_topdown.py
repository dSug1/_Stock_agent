"""M12 — top-down harvest: pure surprise math, forward-only calendar parse, Stage-2b write path. Offline."""

from momentum_parser import stage2b_topdown, topdown
from momentum_parser.clients import econ_calendar
from momentum_parser.store import Store

CFG = {
    "topdown": {
        "horizon_days": 5, "lookback_days": 10, "squash_scale": 0.05,
        "proxies": {"vix": "^VIX", "credit_hy": "HYG", "credit_ig": "LQD", "tnx": "^TNX",
                    "dxy": "DXY", "oil": "CL=F", "gold": "GC=F", "ai_basket": "BOTZ",
                    "market": "SPY", "growth": "IWF", "value": "IWD", "hibeta": "SPHB", "lowvol": "SPLV"},
        "regime": {"vix_high": 22.0, "vix_low": 15.0, "credit_lookback": 10, "credit_widen": -0.01},
        "econ_calendar": {"enabled": True, "searches": 2, "timeout_s": 5},
        "geopolitical_stub": True,
        "taxonomy_file": None,
    },
    "claude": {"rubric_model": "claude-sonnet-4-6"},
}


# --- pure math ----------------------------------------------------------------------------------
def test_lookback_return_and_squash_bounds():
    assert abs(topdown.lookback_return([100, 110], 1) - 0.10) < 1e-9
    assert topdown.lookback_return([100], 5) is None            # not enough data
    assert -1.0 <= topdown.squash(999, 0.05) <= 1.0
    assert topdown.squash(None, 0.05) == 0.0                    # absence -> 0, not a signal


def test_regime_classification():
    flat = [100.0] * 30
    # calm VIX + stable credit -> risk_on
    assert topdown.classify_regime([12.0] * 30, flat, flat, CFG["topdown"]) == "risk_on"
    # spiking VIX -> risk_off regardless of credit
    assert topdown.classify_regime([30.0] * 30, flat, flat, CFG["topdown"]) == "risk_off"
    # widening credit (HY falling vs IG flat) -> risk_off
    hy = [100 - i for i in range(30)]
    assert topdown.classify_regime([18.0] * 30, hy, flat, CFG["topdown"]) == "risk_off"
    # empty VIX -> neutral (fail-open, never a lean)
    assert topdown.classify_regime([], flat, flat, CFG["topdown"]) == "neutral"


def test_continuous_surprise_signs():
    flat = [100.0] * 30
    rising = [100.0 + i for i in range(30)]
    s = topdown.continuous_surprises(
        {"vix": [12.0] * 30, "credit_hy": flat, "credit_ig": flat,
         "tnx": rising, "dxy": flat, "oil": flat, "gold": flat,
         "ai_basket": rising, "market": flat, "growth": rising, "value": flat,
         "hibeta": flat, "lowvol": flat}, CFG["topdown"])
    assert s["_regime"] == "risk_on"
    assert s["ai_crowding"] > 0        # AI basket outrunning market -> positive
    assert s["style_factors"] > 0      # growth outrunning value -> positive
    assert s["rates_usd"] < 0          # rising 10Y -> headwind for high-beta longs
    assert s["risk_regime"] > 0        # risk_on lean
    # a wholly-missing proxy set -> all zeros, no crash (fail-open)
    z = topdown.continuous_surprises({}, CFG["topdown"])
    assert z["ai_crowding"] == 0.0 and z["_regime"] == "neutral"


# --- econ calendar parse (forward-only) ---------------------------------------------------------
def test_parse_events_forward_only_and_mapping():
    parsed = {"events": [
        {"category": "monetary_policy", "name": "FOMC", "date": "2026-07-03", "consensus": "hold",
         "surprise": 0.2, "note": ""},                                        # +3d -> kept
        {"category": "inflation", "name": "CPI", "date": "2026-06-28", "consensus": "x",
         "surprise": -0.5, "note": ""},                                       # past -> dropped
        {"category": "labor", "name": "NFP", "date": "2026-07-20", "consensus": "x",
         "surprise": 0.9, "note": ""},                                        # +20d beyond horizon -> dropped
        {"category": "not_a_category", "name": "?", "date": "2026-07-01", "consensus": "",
         "surprise": 0.0, "note": ""},                                        # unknown category -> dropped
    ]}
    rows = econ_calendar.parse_events(parsed, "2026-06-30", CFG)
    assert [r["signal_id"] for r in rows] == ["fomc"]
    assert rows[0]["days_out"] == 3 and rows[0]["surprise"] == 0.2


def test_parse_events_dedupes_to_soonest_and_clamps():
    parsed = {"events": [
        {"category": "inflation", "name": "PPI", "date": "2026-07-04", "consensus": "", "surprise": 5.0, "note": ""},
        {"category": "inflation", "name": "CPI", "date": "2026-07-02", "consensus": "", "surprise": 0.3, "note": ""},
    ]}
    rows = econ_calendar.parse_events(parsed, "2026-06-30", CFG)
    assert len(rows) == 1 and rows[0]["days_out"] == 2      # soonest wins
    assert rows[0]["surprise"] == 0.3


# --- Stage 2b write path (injected fakes) -------------------------------------------------------
class _FakeScorer:
    def complete(self, params, timeout=None):
        return {"parsed": {"events": [
            {"category": "monetary_policy", "name": "FOMC", "date": "2026-07-02", "consensus": "hold",
             "surprise": 0.15, "note": ""}]}, "error": None}


def _fake_fetch_factory():
    flat = [100.0] * 30
    rising = [100.0 + i for i in range(30)]
    table = {"^VIX": [12.0] * 30, "HYG": flat, "LQD": flat, "^TNX": rising, "DXY": flat,
             "CL=F": flat, "GC=F": flat, "BOTZ": rising, "SPY": flat, "IWF": rising, "IWD": flat,
             "SPHB": flat, "SPLV": flat}
    return lambda tk: table.get(tk, [])


def test_stage2b_writes_continuous_dated_and_stub(tmp_path):
    s = Store(tmp_path / "t.db")
    clients = {"macro_series": _fake_fetch_factory()}
    funnel = stage2b_topdown.run(s, CFG, "2026-06-30", clients=clients, scorer=_FakeScorer(),
                                 log=lambda m: None)
    assert funnel["regime"] == "risk_on"
    assert funnel["continuous"] == 6 and funnel["dated"] == 1 and funnel["geopolitical_stub"] == 5

    rows = {r["signal_id"]: r for r in s.get_macro_signals("2026-06-30")}
    assert rows["ai_crowding"]["active"] == 1 and rows["ai_crowding"]["surprise"] > 0
    assert rows["fomc"]["active"] == 1 and rows["fomc"]["horizon_days"] == 2
    assert rows["war_peace"]["active"] == 0                 # geopolitical stubbed
    active = s.get_macro_signals("2026-06-30", active_only=True)
    assert {r["signal_id"] for r in active} == {"risk_regime", "rates_usd", "commodities",
                                                "ai_crowding", "style_factors", "sector_flows", "fomc"}


def test_stage2b_dry_skips_dated_and_is_fail_open(tmp_path):
    s = Store(tmp_path / "t.db")
    # no scorer (dry) + a fetch that raises for one role -> still completes, no dated signals
    def flaky(tk):
        if tk == "^VIX":
            raise RuntimeError("network")
        return [100.0] * 30
    funnel = stage2b_topdown.run(s, CFG, "2026-06-30", clients={"macro_series": flaky},
                                 scorer=None, log=lambda m: None)
    assert funnel["dated"] == 0                             # dry: no econ-calendar spend
    assert funnel["continuous"] == 6                        # still wrote continuous (fail-open on VIX)
    assert funnel["regime"] == "neutral"                    # VIX failed -> neutral, no lean
