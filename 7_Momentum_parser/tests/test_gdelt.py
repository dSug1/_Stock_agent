"""GDELT news provider — parse, windowing, 429-avoidance (cache + fail-open). Offline via injected http_get."""

import json
import urllib.error

from momentum_parser.clients import gdelt


def _payload(daily):
    """Build a GDELT timeline JSON from {YYYYMMDD: value}."""
    return json.dumps({"timeline": [{"data": [{"date": f"{d}000000", "value": v}
                                               for d, v in daily.items()]}]})


def _reset(tmp_path, **opts):
    gdelt.OPTS = {"cache_dir": str(tmp_path / "cache"), "min_interval_s": 0, "lookback": 5, **opts}
    gdelt._last_call[0] = 0.0


def test_parse_timeline_daily():
    out = gdelt.parse_timeline_daily(_payload({"20250101": 3, "20250102": 5}))
    assert out == {"2025-01-01": 3.0, "2025-01-02": 5.0}


def test_parse_bad_payload_empty():
    assert gdelt.parse_timeline_daily("not json") == {}


def test_daily_counts_window_fill(tmp_path):
    # asof 2025-01-05, lookback 5 -> series for 2024-12-31 .. 2025-01-05 (6 points), missing days = 0
    calls = []
    def fake(url):
        calls.append(url)
        return _payload({"20250103": 4, "20250105": 10})
    _reset(tmp_path, http_get=fake)
    s = gdelt.daily_counts("GME", "2025-01-05", lookback=5)
    assert len(s) == 6 and s[-1] == 10.0 and s[3] == 4.0 and s[0] == 0.0
    assert len(calls) == 1


def test_cache_prevents_second_hit(tmp_path):
    n = {"c": 0}
    def fake(url):
        n["c"] += 1
        return _payload({"20250105": 7})
    _reset(tmp_path, http_get=fake)
    gdelt.daily_counts("AMC", "2025-01-05", lookback=5)
    gdelt.daily_counts("AMC", "2025-01-05", lookback=5)        # same query -> served from cache
    assert n["c"] == 1                                          # the 429-avoidance: never re-hit


def test_429_retries_then_fails_open(tmp_path):
    attempts = {"n": 0}
    def fake(url):
        attempts["n"] += 1
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
    _reset(tmp_path, http_get=fake, retries=2, backoff_s=0)
    out = gdelt.daily_counts("HOOD", "2025-01-05", lookback=5)
    assert out == [] and attempts["n"] == 3                     # 1 + 2 retries, then fail open


def test_query_prefers_company_name(tmp_path):
    _reset(tmp_path, names={"GME": "GameStop"})
    assert gdelt._query("GME") == '"GameStop"'             # name phrase, not "GME stock"
    assert gdelt._query("AMC") == "AMC stock"               # no name -> ticker fallback


def test_tone_normalized(tmp_path):
    _reset(tmp_path, http_get=lambda url: _payload({"20250105": 25.0}))   # tone 25 -> clamp to 1.0
    t = gdelt.daily_tone("TSLA", "2025-01-05", lookback=5)
    assert t[-1] == 1.0
