"""Stage-3 bundle / schema / request builder / clamp — pure, offline."""

from momentum_parser.models import Bar
from momentum_parser.scoring.rubric import (
    OUTPUT_SCHEMA,
    build_bundle,
    build_request,
    clamp_parsed,
    system_prompt,
)
from momentum_parser.store import Store

CFG = {"signals": {}, "probability": {"target": {"vol_band_mult": 0.5}},
       "claude": {"max_output_tokens": 12500, "web_search_variant": "web_search_20250305",
                  "max_searches_per_company": 5, "allowed_domains": []}}


def _store(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_bars("AAA", [Bar(f"2025-01-0{i+1}", 1, 1, 1, 100 + i, 1000) for i in range(3)])
    s.upsert_evidence("AAA", "2025-01-03", "media", 0.6, {"surge_ratio": 2.0})
    s.upsert_catalysts("AAA", [("2025-01-10", "pdufa", "FDA", "fda")])
    return s


def test_build_bundle_has_dimensions(tmp_path):
    b = build_bundle(_store(tmp_path), "AAA", "2025-01-03", CFG)
    assert b["ticker"] == "AAA" and b["last_close"] == 102
    assert "composite" in b["technical"]
    assert b["media"]["score"] == 0.6
    assert b["next_catalyst"]["kind"] == "pdufa"


def test_build_request_triage_vs_rubric(tmp_path):
    b = build_bundle(_store(tmp_path), "AAA", "2025-01-03", CFG)
    triage = build_request(b, {"model": "claude-haiku-4-5-20251001", "use_search": False, "thinking": False}, CFG)
    rubric = build_request(b, {"model": "claude-sonnet-4-6", "use_search": True, "thinking": True}, CFG)
    assert triage["custom_id"] == "AAA"
    # caching breakpoint on the stable system prefix + strict schema
    assert triage["params"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert triage["params"]["output_config"]["format"]["schema"] is OUTPUT_SCHEMA
    assert "tools" not in triage["params"]                      # triage is search-free
    # rubric carries the BASIC web_search variant with bounded max_uses + adaptive thinking
    tool = rubric["params"]["tools"][0]
    assert tool["type"] == "web_search_20250305" and tool["max_uses"] == 5
    assert "allowed_domains" not in tool                        # empty allow-list -> omitted
    assert rubric["params"]["thinking"] == {"type": "adaptive"}
    assert rubric["params"]["max_tokens"] == 12500


def test_allowed_domains_applied_when_set(tmp_path):
    cfg = {**CFG, "claude": {**CFG["claude"], "allowed_domains": ["sec.gov"]}}
    b = build_bundle(_store(tmp_path), "AAA", "2025-01-03", cfg)
    r = build_request(b, {"model": "claude-sonnet-4-6", "use_search": True}, cfg)
    assert r["params"]["tools"][0]["allowed_domains"] == ["sec.gov"]


def test_clamp_parsed_clamps_and_renormalizes():
    out = clamp_parsed({"p_up": 1.5, "p_down": -0.2, "p_flat": 0.0})
    assert 0.01 <= out["p_up"] <= 0.99
    assert abs(out["p_up"] + out["p_down"] + out["p_flat"] - 1.0) < 1e-9


def test_system_prompt_carries_band():
    assert "0.5" in system_prompt(CFG)
