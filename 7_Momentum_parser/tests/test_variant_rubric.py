"""M14 — variant-perception rubric: schema, delta-scaled conviction, top-down context, persistence. Offline."""

import json

from momentum_parser.models import Bar
from momentum_parser.scoring import rubric
from momentum_parser.store import Store

CFG = {"signals": {"min_bars": 40}, "probability": {"target": {"vol_band_mult": 0.5}},
       "topdown": {"taxonomy_file": None}}


def test_schema_and_prompt_demand_variant_perception():
    for f in ("consensus_view", "our_view", "mispricing", "why_now", "variant_strength", "macro_exposure"):
        assert f in rubric.OUTPUT_SCHEMA["properties"] and f in rubric.OUTPUT_SCHEMA["required"]
    sysp = rubric.system_prompt(CFG)
    # v0.5 reworded the lead to forward-driver generation, but the variant fields + top-down context remain
    assert "consensus_view" in sysp and "variant_strength" in sysp and "TOP-DOWN CONTEXT" in sysp


def test_conviction_is_delta_scaled_by_variant_strength():
    # a pure recap (variant_strength ~0) collapses conviction toward base-rate...
    recap = rubric.clamp_parsed({"p_up": 0.6, "p_down": 0.2, "p_flat": 0.2,
                                 "conviction": 0.9, "variant_strength": 0.05})
    assert recap["conviction"] < 0.1 and recap["raw_conviction"] == 0.9
    # ...while a genuinely differentiated view keeps it
    strong = rubric.clamp_parsed({"p_up": 0.6, "p_down": 0.2, "p_flat": 0.2,
                                  "conviction": 0.9, "variant_strength": 1.0})
    assert abs(strong["conviction"] - 0.9) < 1e-9


def test_clamp_parsed_backward_compatible_without_variant():
    out = rubric.clamp_parsed({"p_up": 1.5, "p_down": -0.2, "p_flat": 0.0})   # legacy shape
    assert abs(out["p_up"] + out["p_down"] + out["p_flat"] - 1.0) < 1e-9
    assert "variant_strength" not in out


def _seed_ticker(s, t):
    s.upsert_bars(t, [Bar(f"2025-01-{(i % 28) + 1:02d}", 100, 100, 100, 100, 1e6) for i in range(50)])


def test_bundle_includes_topdown_context_when_harvested(tmp_path):
    s = Store(tmp_path / "t.db")
    _seed_ticker(s, "AAA")
    # no harvest yet -> no top-down block (absence != signal)
    assert rubric.build_bundle(s, "AAA", "2025-01-28", CFG)["topdown"] is None
    # after a harvest + a loading, the bundle carries the anticipated read + this name's exposure
    s.upsert_macro_signal("2026-06-30", "risk_regime", active=True, surprise=0.4, regime="risk_on")
    s.upsert_ticker_loading("AAA", "ai_crowding", beta=1.6, updated_at="t")
    td = rubric.build_bundle(s, "AAA", "2025-01-28", CFG)["topdown"]
    assert td["regime"] == "risk_on"
    assert any(a["signal"] == "risk_regime" for a in td["anticipated_signals"])
    assert td["this_ticker_exposures"]["ai_crowding"] == 1.6


def test_variant_fields_persist_to_scores(tmp_path):
    s = Store(tmp_path / "t.db")
    p = rubric.clamp_parsed({"p_up": 0.6, "p_down": 0.2, "p_flat": 0.2, "conviction": 0.8,
                             "variant_strength": 0.5, "consensus_view": "priced for a hold",
                             "our_view": "hawkish surprise likely", "mispricing": "rates underpriced",
                             "why_now": "CPI in 3d", "macro_exposure": "long duration beta"})
    variant = {k: p.get(k, "") for k in ("consensus_view", "our_view", "mispricing", "why_now",
                                         "macro_exposure")}
    variant["variant_strength"] = p.get("variant_strength")
    variant["raw_conviction"] = p.get("raw_conviction")
    s.write_score("AAA", "2026-06-30", "run1", tier="rubric", p_up=p["p_up"], conviction=p["conviction"],
                  variant_json=json.dumps(variant))
    got = json.loads(s.latest_score("AAA", "2026-06-30")["variant_json"])
    assert got["our_view"] == "hawkish surprise likely" and got["variant_strength"] == 0.5
    assert got["raw_conviction"] == 0.8
