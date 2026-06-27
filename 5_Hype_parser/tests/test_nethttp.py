"""Tests for the shared response size cap (security hardening, audit S10)."""

import pytest

from hype_parser.nethttp import capped_read


class _FakeResp:
    def __init__(self, body):
        self._b = body

    def read(self, n=-1):
        return self._b[:n] if (n is not None and n >= 0) else self._b


def test_capped_read_rejects_oversized():
    with pytest.raises(ValueError):
        capped_read(_FakeResp(b"x" * 100), max_bytes=50)


def test_capped_read_passes_under_cap():
    assert capped_read(_FakeResp(b"x" * 30), max_bytes=50) == b"x" * 30


def test_capped_read_at_exact_cap():
    assert capped_read(_FakeResp(b"x" * 50), max_bytes=50) == b"x" * 50
