"""M15 — catalyst as forward fact + falsifiable hypothesis: pure signal, persistence, harvest wiring. Offline."""

from momentum_parser import catalyst_signal as cs
from momentum_parser import stage2_harvest
from momentum_parser.models import Bar
from momentum_parser.store import Store

H = {"catalyst_horizon_days": 21, "catalyst_accum_window": 5, "catalyst_accum_scale": 0.1}


def _bars(closes, vols=None):
    vols = vols or [1e6] * len(closes)
    return [Bar(f"2026-06-{(i % 28) + 1:02d}", c, c, c, c, v) for i, (c, v) in enumerate(zip(closes, vols))]


# --- forward fact: pre-event accumulation -------------------------------------------------------
def test_accumulation_positive_on_rising_price_and_volume():
    closes = [100.0] * 15 + [100.0, 102, 104, 106, 108]              # ramp up in the last 5 (the window)
    vols = [1e6] * 15 + [3e6] * 5                                    # volume surges vs the prior window
    acc = cs.pre_event_accumulation(_bars(closes, vols), window=5, scale=0.1)
    assert acc > 0.3                                                 # strong accumulation
    # flat price -> ~0 regardless of volume (absence of drift ≠ signal)
    assert abs(cs.pre_event_accumulation(_bars([100.0] * 20, vols), 5, 0.1)) < 1e-6


def test_accumulation_damped_when_volume_not_confirming():
    closes = [100.0] * 15 + [100.0, 102, 104, 106, 108]
    hi = cs.pre_event_accumulation(_bars(closes, [1e6] * 15 + [3e6] * 5), 5, 0.1)
    lo = cs.pre_event_accumulation(_bars(closes, [1e6] * 20), 5, 0.1)  # flat volume
    assert hi > lo > 0                                               # volume confirmation amplifies


# --- falsifiable hypothesis: analog drift + assembly --------------------------------------------
def test_analog_drift_and_hypothesis_window():
    bars = _bars([100 + i for i in range(40)])                       # steady uptrend
    drift, disp = cs.analog_drift(bars, horizon=5)
    assert drift > 0 and disp >= 0
    # event inside the horizon -> a hypothesis; beyond -> None
    hyp = cs.hypothesis(bars, "2026-07-05", "2026-07-01", H, horizon=5)
    assert hyp and hyp["days_to_catalyst"] == 4 and hyp["falsifiable"] is True
    assert cs.hypothesis(bars, "2026-09-01", "2026-07-01", H, horizon=5) is None   # beyond horizon
    assert cs.hypothesis(bars, "2026-06-01", "2026-07-01", H, horizon=5) is None   # past -> not forward


def test_catalyst_score_zero_without_hypothesis():
    assert cs.catalyst_score(None, H) == 0.0


# --- persistence + harvest wiring ---------------------------------------------------------------
def test_hypothesis_persists_and_is_open(tmp_path):
    s = Store(tmp_path / "t.db")
    hyp = {"days_to_catalyst": 3, "accumulation": 0.5, "expected_drift": 0.02,
           "dispersion": 0.05, "horizon_days": 5}
    s.upsert_catalyst_hypothesis("AAA", "2026-07-01", "2026-07-04", "earnings", hyp)
    rows = s.open_catalyst_hypotheses()
    assert len(rows) == 1 and rows[0]["expected_drift"] == 0.02 and rows[0]["kind"] == "earnings"
    s.settle_catalyst_hypothesis("AAA", "2026-07-01", "2026-07-04", realized_drift=0.03, settled_at="t")
    assert s.open_catalyst_hypotheses() == []


class _NoNews:
    def daily_counts(self, t, a): return []
    def daily_tone(self, t, a): return []
    def daily_interest(self, t, a): return []


class _Cat:
    def upcoming(self, t, a): return [("2026-07-06", "earnings", "Q2", "test")]


def test_harvest_writes_enriched_catalyst_and_hypothesis(tmp_path):
    s = Store(tmp_path / "t.db")
    s.upsert_bars("AAA", _bars([100 + 0.5 * i for i in range(40)], [1e6] * 30 + [4e6] * 10))
    cfg = {"harvest": H, "probability": {"horizon_days": 5}, "sources": {"media_provider": "none"}}
    clients = {"news": _NoNews(), "search": _NoNews(), "catalysts": _Cat()}
    stage2_harvest.run(s, ["AAA"], cfg, asof="2026-07-01", clients=clients, log=lambda m: None)

    ev = {r["dimension"]: r for r in s.get_evidence("AAA", "2026-07-01")}
    import json
    cat_feats = json.loads(ev["catalyst"]["features_json"])
    assert cat_feats["falsifiable"] is True and cat_feats["days_to_catalyst"] == 5
    assert "expected_drift" in cat_feats and "accumulation" in cat_feats
    assert len(s.open_catalyst_hypotheses()) == 1                     # hypothesis recorded for settlement
