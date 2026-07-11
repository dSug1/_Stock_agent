"""Offline tests for the _net retry-on-429/5xx helper (mocked urllib; no network, no real sleeps).

Guards the fix for OpenAlex hard-throttling: a bare 429 used to fail-open to None on the first hit,
silently dropping a founder's citation signal. get_json_retry now backs off (honoring Retry-After) and
retries; safe_json_retry only returns None once retries are exhausted."""

from __future__ import annotations

import urllib.error

import pytest

from early_detection.clients import _net


class _Resp:
    def __init__(self, body: bytes):
        self._b = body

    def read(self, n: int = -1) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code: int, retry_after: str | None = None):
    hdrs = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://api.openalex.org/x", code, "err", hdrs, None)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(_net.time, "sleep", lambda s: None)   # never actually sleep in tests


def test_recovers_after_429(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(429, retry_after="0")           # first hit throttled
        return _Resp(b'{"ok": true}')

    monkeypatch.setattr(_net.urllib.request, "urlopen", fake_urlopen)
    assert _net.get_json_retry("https://api.openalex.org/x", retries=3) == {"ok": True}
    assert calls["n"] == 2                                     # retried once, then succeeded


def test_gives_up_after_retries_then_safe_wrapper_returns_none(monkeypatch):
    calls = {"n": 0}

    def always_429(req, timeout=30):
        calls["n"] += 1
        raise _http_error(429, retry_after="0")

    monkeypatch.setattr(_net.urllib.request, "urlopen", always_429)
    with pytest.raises(urllib.error.HTTPError):
        _net.get_json_retry("https://api.openalex.org/x", retries=2)
    assert calls["n"] == 3                                     # initial + 2 retries
    # the fail-open wrapper swallows it to None only after exhausting retries
    assert _net.safe_json_retry("https://api.openalex.org/x", retries=2) is None


def test_does_not_retry_non_transient_status(monkeypatch):
    calls = {"n": 0}

    def fake(req, timeout=30):
        calls["n"] += 1
        raise _http_error(404)

    monkeypatch.setattr(_net.urllib.request, "urlopen", fake)
    with pytest.raises(urllib.error.HTTPError):
        _net.get_json_retry("https://api.openalex.org/x", retries=3)
    assert calls["n"] == 1                                     # 404 is not retried
