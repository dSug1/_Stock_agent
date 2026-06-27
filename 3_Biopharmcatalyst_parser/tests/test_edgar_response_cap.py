"""Module 2 — response size-cap guard for the EDGAR HTTP layer.

Verifies `_read_capped` rejects an over-cap streamed body (OOM guard) and
passes normal bodies through unchanged. Reads one chunk past the limit so
an over-cap response is caught even when Content-Length is absent / lies.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_2 import edgar_client  # noqa: E402
from module_2.edgar_client import HttpError, _read_capped  # noqa: E402


class _FakeResp:
    """Minimal stand-in for a streamed requests.Response."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size=1 << 16):
        for c in self._chunks:
            yield c

    def close(self):
        self.closed = True


def test_read_capped_passes_small_body():
    resp = _FakeResp([b"hello ", b"world"])
    assert _read_capped(resp, "https://sec.gov/x") == b"hello world"


def test_read_capped_rejects_over_cap(monkeypatch):
    # Shrink the cap so the test stays cheap.
    monkeypatch.setattr(edgar_client, "_MAX_RESPONSE_BYTES", 10)
    resp = _FakeResp([b"a" * 6, b"b" * 6])   # 12 bytes > 10
    with pytest.raises(HttpError) as ei:
        _read_capped(resp, "https://sec.gov/x")
    assert "exceeds" in str(ei.value)
    assert resp.closed is True


def test_read_capped_at_exact_boundary(monkeypatch):
    monkeypatch.setattr(edgar_client, "_MAX_RESPONSE_BYTES", 10)
    resp = _FakeResp([b"a" * 10])            # exactly at cap → allowed
    assert _read_capped(resp, "https://sec.gov/x") == b"a" * 10
