"""Stage 0a discovery + 0b gate — offline (discovery via a fake scorer)."""

from momentum_parser import stage0_discovery, stage0_gate
from momentum_parser.models import Bar
from momentum_parser.scoring import discovery
from momentum_parser.store import Store

CFG = {
    "universe": {"discovery": {"max_candidates": 3}, "min_price": 1.0, "min_weekly_vol": 0.04},
    "liquidity": {"min_session_usd": 100_000, "safety_mult": 20, "adv_window": 20},
    "claude": {"rubric_model": "claude-sonnet-4-6", "discovery_model": "claude-sonnet-4-6",
               "max_output_tokens": 12500, "web_search_variant": "web_search_20250305",
               "discovery_searches": 8, "allowed_domains": [],
               "cost": {"cost_calibration_factor": 0.10}},
}


# --- discovery prompt/schema/clean (pure) -------------------------------------------------

def test_build_request_has_schema_and_search():
    req = discovery.build_request(CFG)
    assert req["custom_id"] == "discovery"
    assert req["params"]["output_config"]["format"]["schema"] is discovery.DISCOVERY_SCHEMA
    assert req["params"]["tools"][0]["type"] == "web_search_20250305"
    assert req["params"]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_clean_dedupes_uppercases_and_caps():
    raw = [{"ticker": "aaa", "name": "A"}, {"ticker": "AAA", "name": "dup"},
           {"ticker": "bbb"}, {"ticker": ""}, {"ticker": "ccc"}, {"ticker": "ddd"}]
    out = discovery.clean(raw, CFG)                  # max_candidates 3
    assert [c["ticker"] for c in out] == ["AAA", "BBB", "CCC"]


# --- discovery orchestration (fake scorer) ------------------------------------------------

class FakeScorer:
    def __init__(self, candidates):
        self.candidates = candidates
        self.web_searches = 4

    def complete(self, params, timeout=None):
        return {"parsed": {"candidates": self.candidates}, "web_searches": 4, "error": None}


def test_discovery_dry_run_no_writes(tmp_path):
    s = Store(tmp_path / "t.db")
    out = stage0_discovery.run(s, CFG, "run1", FakeScorer([{"ticker": "AAA"}]), dispatch=False)
    assert out["dispatched"] is False and out["discovered"] == 0
    assert s.latest_discovery() == []


def test_discovery_persists_candidates(tmp_path):
    s = Store(tmp_path / "t.db")
    fake = FakeScorer([{"ticker": "mrna", "name": "Moderna", "reason": "buzz", "tags": ["biotech"]},
                       {"ticker": "MRNA"}, {"ticker": "SAVA", "name": "Cassava"}])
    out = stage0_discovery.run(s, CFG, "run1", fake, dispatch=True)
    assert out["discovered"] == 2 and out["dispatched"] is True
    got = {r["ticker"] for r in s.latest_discovery()}
    assert got == {"MRNA", "SAVA"}


# --- gate (pure + persist) ----------------------------------------------------------------

def _bars(closes, vol):
    return [Bar(f"2025-{(i//28)+1:02d}-{(i%28)+1:02d}", c, c, c, c, vol) for i, c in enumerate(closes)]


def test_gate_statuses():
    # liquid + volatile + above penny -> pass (zig-zag gives volatility; high $ volume)
    volatile = [100 + (5 if i % 2 else -5) + i for i in range(60)]
    assert stage0_gate.gate_ticker(_bars(volatile, 100_000), CFG)[0] == "pass"
    # penny
    assert stage0_gate.gate_ticker(_bars([0.5] * 60, 1_000_000), CFG)[0] == "penny"
    # illiquid (tiny volume)
    assert stage0_gate.gate_ticker(_bars(volatile, 1), CFG)[0] == "illiquid"
    # placid (flat -> sigma ~0)
    assert stage0_gate.gate_ticker(_bars([100.0] * 60, 100_000), CFG)[0] == "placid"
    # no data
    assert stage0_gate.gate_ticker(_bars([100, 101], 100_000), CFG)[0] == "no_data"


def test_gate_run_persists_and_investable(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_bars("GOOD", _bars([100 + (5 if i % 2 else -5) + i for i in range(60)], 100_000))
    s.upsert_bars("PENNY", _bars([0.5] * 60, 1_000_000))
    out = stage0_gate.run(s, ["GOOD", "PENNY"], CFG)
    assert out["passed"] == 1 and out["tickers"] == ["GOOD"]
    assert s.investable_tickers() == ["GOOD"]       # only the pass goes to scoring
