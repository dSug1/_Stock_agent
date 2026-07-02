"""M22 — Tier-2 leading inputs (short interest + options-implied): pure features + opt-in harvest wiring. Offline."""

import json

from momentum_parser import microstructure as ms
from momentum_parser import stage2_harvest
from momentum_parser.models import Bar
from momentum_parser.scoring import rubric
from momentum_parser.store import Store

SO = {"squeeze_pct_float": 0.20, "squeeze_days_cover": 5.0, "horizon_days": 7}


# --- pure features ------------------------------------------------------------------------------
def test_short_features_squeeze_setup_and_building():
    f = ms.short_features({"short_pct_float": 0.30, "days_to_cover": 10.0,
                           "shares_short": 12e6, "shares_short_prior": 10e6}, SO)
    assert f["squeeze_setup"] == 1.0                       # both above the high thresholds -> capped 1.0
    assert f["short_building"] == 0.2                      # +20% vs prior settlement
    # low short interest -> low setup
    assert ms.short_features({"short_pct_float": 0.02, "days_to_cover": 1.0}, SO)["squeeze_setup"] < 0.2
    assert ms.short_features({}, SO) == {"no_data": True}  # absent -> no_data, not bearish


def test_options_features_passthrough_and_no_data():
    f = ms.options_features({"implied_move_pct": 0.08, "atm_iv": 0.9, "skew": 0.05}, SO)
    assert f["implied_move_pct"] == 0.08 and f["skew"] == 0.05
    assert ms.options_features({}, SO) == {"no_data": True}


# --- opt-in harvest + bundle wiring (injected fakes) --------------------------------------------
class _FakeShort:
    def fetch_short_stats(self, t): return {"short_pct_float": 0.28, "days_to_cover": 8.0,
                                            "shares_short": 11e6, "shares_short_prior": 9e6}
    def fetch_options_iv(self, t, horizon_days=7): return {"implied_move_pct": 0.11, "atm_iv": 1.2, "skew": -0.03}


class _NoNews:
    def daily_counts(self, t, a): return []
    def daily_tone(self, t, a): return []
    def daily_interest(self, t, a): return []


def _bars(t, s):
    s.upsert_bars(t, [Bar(f"2026-06-{(i % 28) + 1:02d}", 100, 101, 99, 100, 1e6) for i in range(40)])


def test_harvest_opt_in_writes_positioning_and_bundle_shows_it(tmp_path):
    s = Store(tmp_path / "t.db")
    _bars("AAA", s)
    cfg = {"harvest": {}, "probability": {"horizon_days": 5},
           "sources": {"media_provider": "none", "short_options": True}, "short_options": SO}
    fake = _FakeShort()
    clients = {"news": _NoNews(), "search": _NoNews(), "catalysts": type("C", (), {"upcoming": lambda self, t, a: []})(),
               "short": fake, "options": fake}
    funnel = stage2_harvest.run(s, ["AAA"], cfg, asof="2026-06-28", clients=clients, log=lambda m: None)
    assert funnel["positioning"] == 1

    ev = {r["dimension"]: json.loads(r["features_json"]) for r in s.get_evidence("AAA", "2026-06-28")}
    assert ev["short"]["squeeze_setup"] == 1.0 and ev["options"]["implied_move_pct"] == 0.11

    b = rubric.build_bundle(s, "AAA", "2026-06-28",
                            {"probability": {"target": {"vol_band_mult": 0.5}}, "leading": {"min_bars": 30}})
    assert b["leading"]["short"]["squeeze_setup"] == 1.0    # folded into the leading block
    assert b["leading"]["options"]["skew"] == -0.03
    assert "squeeze_setup" in rubric.system_prompt({})      # prompt tells Claude to use positioning


def test_harvest_positioning_off_by_default(tmp_path):
    s = Store(tmp_path / "t.db")
    _bars("BBB", s)
    cfg = {"harvest": {}, "probability": {"horizon_days": 5}, "sources": {"media_provider": "none"}}
    clients = {"news": _NoNews(), "search": _NoNews(),
               "catalysts": type("C", (), {"upcoming": lambda self, t, a: []})()}
    funnel = stage2_harvest.run(s, ["BBB"], cfg, asof="2026-06-28", clients=clients, log=lambda m: None)
    assert funnel["positioning"] == 0                       # opt-in: nothing harvested unless enabled
    assert "short" not in {r["dimension"] for r in s.get_evidence("BBB", "2026-06-28")}
