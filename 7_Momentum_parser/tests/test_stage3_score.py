"""Stage-3 tiering + incremental persist + skip + Batch resume — offline via a FakeScorer."""

from momentum_parser import stage3_score
from momentum_parser.models import Bar
from momentum_parser.store import Store

CFG = {
    "signals": {}, "probability": {"target": {"vol_band_mult": 0.5}}, "blend": {}, "harvest": {},
    "sources": {},
    "claude": {
        "triage_model": "claude-haiku-4-5-20251001", "rubric_model": "claude-sonnet-4-6",
        "finalize_model": "claude-opus-4-8", "contested_band": [0.45, 0.65], "use_batch": True,
        "max_output_tokens": 12500, "web_search_variant": "web_search_20250305",
        "max_searches_per_company": 5, "allowed_domains": [], "triage_floor": 0.35,
        "cost": {"max_usd_per_run": 100.0, "cost_calibration_factor": 0.10},
    },
}


class FakeScorer:
    """Deterministic, synchronous stand-in for AnthropicScorer (same 3-method interface)."""

    def __init__(self, pmap):
        self.pmap = pmap
        self.web_searches = 0
        self.batches: dict = {}

    def _result(self, req):
        t = req["custom_id"]
        pu = self.pmap.get(t, 0.5)
        parsed = {"p_up": pu, "p_down": (1 - pu) / 2, "p_flat": (1 - pu) / 2,
                  "expected_return": 0.01, "direction": "up" if pu > 0.5 else "flat",
                  "conviction": 0.7,
                  "dimensions": {"technical": 0.1, "media": 0.2, "search": 0.0, "catalyst": 0.0},
                  "memo": "m"}
        return {"custom_id": t, "parsed": parsed, "usage": {}, "web_searches": 1, "error": None}

    def score_many_realtime(self, requests, on_result, concurrency=None):
        for r in requests:
            on_result(self._result(r))

    def submit_batch(self, requests):
        bid = f"bid-{len(self.batches) + 1}"
        self.batches[bid] = requests
        return bid

    def poll_batch(self, batch_id):
        return [self._result(r) for r in self.batches.get(batch_id, [])]


def _seed(s, tickers):
    for t in tickers:
        s.upsert_bars(t, [Bar("2025-01-03", 1, 1, 1, 100, 1000)])


def _store(tmp_path):
    return Store(tmp_path / "t.db")


def test_dry_run_spends_nothing(tmp_path):
    s = _store(tmp_path)
    _seed(s, ["AAA"])
    out = stage3_score.run(s, ["AAA"], CFG, "run1", FakeScorer({"AAA": 0.8}), dispatch=False)
    assert out["dispatched"] is False and out["scored"] == 0
    assert s.scores_for_run("run1") == []


def test_full_tiering_persist_and_contested(tmp_path):
    s = _store(tmp_path)
    _seed(s, ["AAA", "BBB", "CCC"])
    # M17: Opus vets the ACTIONABLE survivors (p_claude >= finalize_min_p 0.45), not a symmetric band.
    # AAA(0.8) + BBB(0.5) are both actionable -> both finalized; CCC(0.2) triaged out.
    out = stage3_score.run(s, ["AAA", "BBB", "CCC"], CFG, "run1",
                           FakeScorer({"AAA": 0.8, "BBB": 0.5, "CCC": 0.2}), dispatch=True)
    assert out["survivors"] == 2 and out["contested"] == 2 and out["dispatched"] is True

    rows = {r["ticker"]: r for r in s.scores_for_run("run1")}
    assert set(rows) == {"AAA", "BBB", "CCC"}
    assert rows["CCC"]["tier"] == "triage"      # never escalated
    assert rows["AAA"]["tier"] == "finalize"    # actionable -> Opus adversarial pass
    assert rows["BBB"]["tier"] == "finalize"    # actionable -> Opus adversarial pass
    # batch was recorded then marked done (crash-safe lifecycle)
    assert s.open_batches("run1") == []
    job = s.conn.execute("SELECT status FROM batch_jobs WHERE run_id='run1' AND tier='rubric'").fetchone()
    assert job["status"] == "done"


def test_skip_already_scored(tmp_path):
    s = _store(tmp_path)
    _seed(s, ["AAA"])
    stage3_score.run(s, ["AAA"], CFG, "run1", FakeScorer({"AAA": 0.8}), dispatch=True)
    again = stage3_score.run(s, ["AAA"], CFG, "run2", FakeScorer({"AAA": 0.8}), dispatch=True)
    assert again["candidates"] == 0 and again["scored"] == 0      # unchanged config+evidence -> skipped


def test_over_budget_aborts(tmp_path):
    s = _store(tmp_path)
    _seed(s, ["AAA"])
    cfg = {**CFG, "claude": {**CFG["claude"], "cost": {"max_usd_per_run": 1e-6,
                                                       "cost_calibration_factor": 0.10}}}
    out = stage3_score.run(s, ["AAA"], cfg, "run1", FakeScorer({"AAA": 0.8}), dispatch=True)
    assert out.get("aborted") == "over_budget" and out["scored"] == 0
    assert s.scores_for_run("run1") == []


def test_debug_ignores_budget_and_caps_names(tmp_path):
    s = _store(tmp_path)
    _seed(s, ["A", "B", "C", "D", "E"])
    # tiny budget that would normally abort; debug bypasses it and caps to max_names=2
    cfg = {**CFG, "claude": {**CFG["claude"],
                             "cost": {"max_usd_per_run": 1e-9, "cost_calibration_factor": 0.10},
                             "debug": {"max_names": 2, "force_realtime": True, "ignore_budget": True}}}
    out = stage3_score.run(s, ["A", "B", "C", "D", "E"], cfg, "run1",
                           FakeScorer({k: 0.8 for k in "ABCDE"}), dispatch=True, debug=True)
    assert "aborted" not in out and out["dispatched"] is True
    assert out["candidates"] == 2                 # capped despite 5 tickers
    assert len(s.scores_for_run("run1")) == 2
    # debug forced real-time -> no batch job was created
    assert s.conn.execute("SELECT COUNT(*) FROM batch_jobs").fetchone()[0] == 0


def test_resume_drains_open_batch(tmp_path):
    s = _store(tmp_path)
    _seed(s, ["RES"])
    fake = FakeScorer({"RES": 0.7})
    # simulate a prior killed run: a submitted-but-unpolled batch
    fake.batches["bidR"] = [{"custom_id": "RES", "params": {}}]
    s.record_batch("runX", "rubric", "bidR", "2025-01-03T00:00:00Z")
    out = stage3_score.run(s, ["RES"], CFG, "runX", fake, dispatch=False, resume=True)
    # resume persisted the batch result even on a dry (no new dispatch) run
    rows = {r["ticker"]: r for r in s.scores_for_run("runX")}
    assert rows["RES"]["tier"] == "rubric"
    assert s.open_batches("runX") == []          # marked done
    assert out["dispatched"] is False
