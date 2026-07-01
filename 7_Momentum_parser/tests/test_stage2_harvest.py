"""Stage 2 harvest write-path — offline, via injected fake clients."""

import json
from types import SimpleNamespace

from momentum_parser import stage2_harvest
from momentum_parser.store import Store


def _store(tmp_path):
    return Store(tmp_path / "t.db")


def _fake_clients(counts, tones, interest, events):
    return {
        "news": SimpleNamespace(
            daily_counts=lambda t, a=None, lookback=60: counts,
            daily_tone=lambda t, a=None, lookback=60: tones,
        ),
        "search": SimpleNamespace(daily_interest=lambda t, a=None, lookback=60: interest),
        "catalysts": SimpleNamespace(upcoming=lambda t, a=None, horizon_days=90: events),
    }


def test_harvest_writes_three_dimensions_and_catalysts(tmp_path):
    s = _store(tmp_path)
    clients = _fake_clients(
        counts=[10] * 20 + [40],
        tones=[0.4] * 5,
        interest=[5] * 20 + [15],
        events=[("2025-01-20", "pdufa", "FDA decision", "fda")],
    )
    funnel = stage2_harvest.run(s, ["AAA"], {"harvest": {"baseline_window": 20, "catalyst_horizon_days": 30}},
                                asof="2025-01-10", clients=clients)
    assert funnel == {"tickers": 1, "media": 1, "search": 1, "catalysts": 1, "skipped": 0}

    dims = {r["dimension"]: r for r in s.get_evidence("AAA", "2025-01-10")}
    assert set(dims) == {"media", "search", "catalyst"}
    assert dims["media"]["score"] > 0                      # volume surge + positive tone
    assert dims["search"]["score"] > 0                     # search surge
    cat_feats = json.loads(dims["catalyst"]["features_json"])
    assert cat_feats["days_to_catalyst"] == 10             # 2025-01-10 -> 2025-01-20
    # M15: the catalyst dimension is now a forward FACT + falsifiable HYPOTHESIS, not bare proximity
    # (a known date with no accumulation/drift is not a bullish signal by itself — that was the recap).
    assert cat_feats["falsifiable"] is True
    assert len(s.open_catalyst_hypotheses()) == 1          # the quantified prediction is recorded
    assert s.next_catalyst("AAA", "2025-01-10")["kind"] == "pdufa"


def test_harvest_failopen_empty_clients(tmp_path):
    s = _store(tmp_path)
    # default real clients return [] -> evidence still written with neutral scores, no crash
    funnel = stage2_harvest.run(s, ["AAA"], {"harvest": {}}, asof="2025-01-10")
    assert funnel == {"tickers": 1, "media": 0, "search": 0, "catalysts": 0, "skipped": 0}
    dims = {r["dimension"]: r for r in s.get_evidence("AAA", "2025-01-10")}
    assert set(dims) == {"media", "search", "catalyst"}
    assert dims["catalyst"]["score"] == 0.0


def test_harvest_skips_ticker_without_asof(tmp_path):
    s = _store(tmp_path)
    funnel = stage2_harvest.run(s, ["NOPE"], {"harvest": {}})   # no asof, no bars -> skip
    assert funnel["skipped"] == 1 and funnel["tickers"] == 1
    assert s.get_evidence("NOPE", "2025-01-10") == []
