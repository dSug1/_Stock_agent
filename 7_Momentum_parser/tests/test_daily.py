"""Daily orchestrator — cadence gate + full dry-run sequence (offline, fake scorer)."""

from datetime import datetime, timedelta, timezone

from momentum_parser import daily
from momentum_parser.models import Bar
from momentum_parser.store import Store

CFG = {
    "signals": {"min_bars": 40},
    "probability": {"horizon_days": 5, "target": {"vol_band_mult": 0.5, "vol_window_weeks": 12},
                    "beta": 2.2, "use_empirical": True, "empirical_min_samples": 5},
    "blend": {"claude_weight": 0.6, "disagree_threshold": 0.25},
    "validation": {"min_samples": 100, "up_call_threshold": 0.5},
    "universe": {"discovery": {"cadence_days": 7, "max_candidates": 60}, "min_price": 1.0,
                 "min_weekly_vol": 0.04},
    "liquidity": {"min_session_usd": 100_000, "safety_mult": 20, "adv_window": 20},
    "claude": {"rubric_model": "claude-sonnet-4-6"},
}


def _store(tmp_path):
    return Store(tmp_path / "t.db")


def _volatile(t, s, vol=100_000):
    s.upsert_bars(t, [Bar(f"2025-{(i//28)+1:02d}-{(i%28)+1:02d}", 100 + (5 if i % 2 else -5) + i,
                          0, 0, 100 + (5 if i % 2 else -5) + i, vol) for i in range(60)])


class FakeScorer:
    web_searches = 0

    def complete(self, params, timeout=None):
        return {"parsed": {"candidates": []}, "web_searches": 0, "error": None}


# --- cadence -------------------------------------------------------------------------------

def test_discovery_due_when_empty(tmp_path):
    assert daily.discovery_due(_store(tmp_path), CFG) is True


def test_discovery_not_due_when_fresh(tmp_path):
    s = _store(tmp_path)
    s.write_discovery("r", [{"ticker": "AAA"}],
                      (datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    assert daily.discovery_due(s, CFG) is False         # 2 days < 7


def test_discovery_due_when_stale(tmp_path):
    s = Store(tmp_path / "stale.db")                     # separate store: latest discovery is old
    s.write_discovery("r", [{"ticker": "AAA"}],
                      (datetime.now(timezone.utc) - timedelta(days=10)).isoformat())
    assert daily.discovery_due(s, CFG) is True           # 10 days >= 7


# --- full dry run --------------------------------------------------------------------------

def test_daily_dry_run_end_to_end(tmp_path):
    s = _store(tmp_path)
    # pre-seed a recent discovery (so 0a is skipped) + bars for two gate-passing names
    s.write_discovery("disc", [{"ticker": "GOOD1"}, {"ticker": "GOOD2"}],
                      (datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    _volatile("GOOD1", s)
    _volatile("GOOD2", s)

    # route Outputs/ to tmp so the test never clobbers the real signals.md (post-mortem / D-6)
    cfg = {**CFG, "outputs": {"dir": str(tmp_path / "Outputs")}}
    funnel = daily.run(s, cfg, "day1", FakeScorer(), dispatch=False, fetch=False, log=lambda m: None)
    assert str(tmp_path) in funnel["signals_md"]          # wrote to tmp, not the real Outputs/
    assert "score" not in funnel                         # dry: no scoring
    assert funnel["gate"]["passed"] == 2

    preds = s.predictions_for_run("day1")
    assert {p["ticker"] for p in preds} == {"GOOD1", "GOOD2"}
    assert all(p["p_claude"] is None for p in preds)     # model-only (no Claude leg in dry run)
    assert s.open_ledger()                                # predictions opened in the ledger
