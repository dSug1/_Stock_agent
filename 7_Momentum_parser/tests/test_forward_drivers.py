"""M19 — forward-driver generation: schema, novelty-gated conviction, persistence, render. Offline."""

import json

from momentum_parser import render
from momentum_parser.models import Prediction
from momentum_parser.scoring import rubric
from momentum_parser.store import Store

CFG = {"probability": {"target": {"vol_band_mult": 0.5}}}


def test_schema_and_prompt_demand_forward_driver_generation():
    props = rubric.OUTPUT_SCHEMA["properties"]
    assert "forward_drivers" in props and "forward_novelty" in props
    assert "forward_drivers" in rubric.OUTPUT_SCHEMA["required"]
    di = props["forward_drivers"]["items"]["properties"]
    for f in ("driver", "unpriced_why", "probability", "expected_impact", "novelty"):
        assert f in di
    sysp = rubric.system_prompt(CFG)
    assert "GENERATE FORWARD DRIVERS" in sysp and "NOT TO EVALUATE KNOWN CATALYSTS" in sysp
    assert "RECAP OF THE RUN-UP" in sysp


def test_conviction_gated_by_forward_novelty():
    # a milestone/recap thesis (forward_novelty ~0) collapses conviction toward base-rate...
    recap = rubric.clamp_parsed({"p_up": 0.6, "p_down": 0.2, "p_flat": 0.2, "conviction": 0.9,
                                 "forward_novelty": 0.05, "variant_strength": 0.8})
    assert recap["conviction"] < 0.1 and recap["raw_conviction"] == 0.9
    # ...a genuinely forward thesis keeps it
    fwd = rubric.clamp_parsed({"p_up": 0.6, "p_down": 0.2, "p_flat": 0.2, "conviction": 0.9,
                               "forward_novelty": 1.0, "variant_strength": 0.8})
    assert abs(fwd["conviction"] - 0.9) < 1e-9


def test_forward_novelty_dominates_even_high_variant_strength():
    # CLOV case: differentiated (high variant_strength) but backward (low forward_novelty) -> low conviction
    out = rubric.clamp_parsed({"p_up": 0.5, "p_down": 0.3, "p_flat": 0.2, "conviction": 0.8,
                               "forward_novelty": 0.1, "variant_strength": 0.9})
    assert out["conviction"] < 0.1                         # forward_novelty gate bites despite variant_strength


def test_falls_back_to_variant_strength_without_forward_novelty():
    out = rubric.clamp_parsed({"p_up": 0.6, "p_down": 0.2, "p_flat": 0.2, "conviction": 0.8,
                               "variant_strength": 0.5})   # legacy v0.4 shape
    assert abs(out["conviction"] - 0.4) < 1e-9


def test_drivers_persist_and_render(tmp_path):
    s = Store(tmp_path / "t.db")
    variant = {"our_view": "squeeze setup", "forward_novelty": 0.8,
               "forward_drivers": [{"driver": "borrow tightening into a low-float squeeze",
                                    "unpriced_why": "short interest rose while consensus fixates on the old ruling",
                                    "probability": 0.4, "expected_impact": 0.08, "novelty": 0.85}]}
    s.write_score("AAA", "2026-07-01", "run1", tier="rubric", p_up=0.6, conviction=0.5,
                  memo="forward-driven thesis", variant_json=json.dumps(variant))
    got = json.loads(s.latest_score("AAA", "2026-07-01")["variant_json"])
    assert got["forward_drivers"][0]["novelty"] == 0.85

    s.write_prediction(Prediction(ticker="AAA", asof_date="2026-07-01", horizon_days=5, p_up=0.6,
                                  expected_return=0.01, confidence=0.3, composite=0.2, signals=[],
                                  last_close=10.0, p_claude=0.6, p_model=0.6, disagreement=0.0), "run1")
    doc = render.render(s, {"outputs": {"dir": str(tmp_path)}}, "run1").read_text(encoding="utf-8")
    assert "Forward drivers:" in doc and "low-float squeeze" in doc and "forward novelty 0.80" in doc
