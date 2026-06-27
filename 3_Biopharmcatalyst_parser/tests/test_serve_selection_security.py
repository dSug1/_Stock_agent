"""Module 7 — security guards on the selection-editor HTTP handler.

Covers:
  • path-traversal rejection in static serving (true ancestry check, not a
    `startswith` string compare that a sibling `Outputs*` dir would fool);
  • CSRF Origin allowlist on the POST endpoints;
  • POST body-size cap.

The handler is exercised without a real socket by stubbing the response
sinks (`_json`, `send_response`, `wfile`) on a bare instance.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# The serve script's filename starts with a digit → load it by path.
_SPEC = importlib.util.spec_from_file_location(
    "_serve_selection", PROJECT_ROOT / "scripts" / "3_7_serve_selection.py",
)
serve = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(serve)


class _StubHandler(serve._Handler):
    """Bypass BaseHTTPRequestHandler.__init__ (no socket); record outcomes."""

    def __init__(self, headers=None, port=7034):
        self._status = None
        self._payload = None
        self._headers = headers or {}
        self._port = port
        self._written = b""

    # capture the JSON error/Ok sink
    def _json(self, status, payload):
        self._status = status
        self._payload = payload

    # _serve_static success path calls these directly
    def send_response(self, status):
        self._status = status

    def send_header(self, *a, **k):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self._written += data

    # CSRF guard reads self.server.server_address[1]
    @property
    def server(self):
        port = self._port

        class _S:
            server_address = ("127.0.0.1", port)
        return _S()

    @property
    def headers(self):
        return self._headers


# ─────────────────────────── path traversal ────────────────────────


@pytest.mark.parametrize("path", [
    "/../secret.html",            # parent-dir escape
    "/../Outputs_x/y.html",       # sibling-prefix bypass of old startswith
    "/sub/../../etc.html",        # nested dotdot
    "/..\\secret.html",           # backslash variant
])
def test_static_rejects_traversal(path):
    h = _StubHandler()
    h._serve_static(path)
    assert h._status == 403, f"{path!r} should be forbidden, got {h._status}"


def test_static_allows_normal_file(tmp_path, monkeypatch):
    # Point OUTPUTS at a temp dir holding one real file.
    monkeypatch.setattr(serve, "OUTPUTS", tmp_path)
    (tmp_path / "catalyst_scores.html").write_text("<html>ok</html>", encoding="utf-8")
    h = _StubHandler()
    h._serve_static("/catalyst_scores.html")
    assert h._status == 200
    assert b"ok" in h._written


# ─────────────────────────── CSRF origin guard ─────────────────────


def test_origin_allowed_absent():
    # No Origin header (curl / some same-origin fetches) → allowed.
    assert _StubHandler(headers={})._origin_allowed() is True


def test_origin_allowed_same_loopback():
    h = _StubHandler(headers={"Origin": "http://127.0.0.1:7034"}, port=7034)
    assert h._origin_allowed() is True
    h2 = _StubHandler(headers={"Origin": "http://localhost:7034"}, port=7034)
    assert h2._origin_allowed() is True


def test_origin_rejected_cross_site():
    h = _StubHandler(headers={"Origin": "http://evil.example"}, port=7034)
    assert h._origin_allowed() is False
    # wrong port also rejected
    h2 = _StubHandler(headers={"Origin": "http://127.0.0.1:9999"}, port=7034)
    assert h2._origin_allowed() is False


# ─────────────────────────── body-size cap ─────────────────────────


def test_read_json_body_rejects_oversize():
    big = serve._MAX_POST_BYTES + 1
    h = _StubHandler(headers={"Content-Length": str(big)})
    obj, sent = h._read_json_body()
    assert sent is True
    assert h._status == 413
