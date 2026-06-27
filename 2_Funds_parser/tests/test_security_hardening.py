"""Security-hardening regression tests (defensive audit follow-up).

Covers the fixes applied after the 2_Funds_parser security review:

  * S3  — `scripts/6_serve_report.py` quarter whitelist + Outputs/ containment
           (arbitrary-file-write / path-traversal guard).
  * S8  — CSRF Origin check helper on the local selection server.
  * S10 — response byte cap on the external EDGAR / OpenFIGI getters.
  * S11 — defusedxml used for the untrusted EDGAR XML parsers.

These exercise the pure helpers (no network, no live server) so they stay
fast and deterministic.
"""
from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load_serve_report():
    """Load scripts/6_serve_report.py as a module (filename isn't importable)."""
    spec = importlib.util.spec_from_file_location(
        "serve_report", str(PROJECT_ROOT / "scripts" / "6_serve_report.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────────────── S3 — quarter whitelist ──────────────────────────

@pytest.mark.parametrize("quarter", ["2025Q4", "2026Q1", "1999Q2", "2030Q3"])
def test_valid_quarter_accepts_canonical(quarter):
    srv = _load_serve_report()
    assert srv._valid_quarter(quarter) is True


@pytest.mark.parametrize("quarter", [
    "",
    "..",
    "2025Q5",                       # quarter out of 1-4 range
    "2025q4",                       # lowercase q
    "25Q4",                         # too few year digits
    "2025Q4x",                      # trailing junk
    "foo/../../evil",               # forward-slash traversal
    r"foo\..\..\..\Windows\evil",   # the audited Windows backslash escape
    "2025Q4\ninjected",             # newline (fullmatch must reject)
])
def test_valid_quarter_rejects_traversal_and_junk(quarter):
    srv = _load_serve_report()
    assert srv._valid_quarter(quarter) is False


def test_within_outputs_blocks_audited_escape():
    """The exact payload from the audit must resolve outside Outputs/ and be
    rejected by the containment guard."""
    srv = _load_serve_report()
    from module_6b.selection_io import selection_json_path

    evil = r"foo\..\..\..\Windows\evil"
    escaped = selection_json_path(srv.OUTPUTS / f"final_ranking_{evil}.html")
    # Sanity: the path genuinely escapes Outputs/ (the bug we're guarding).
    assert escaped.resolve().parent != srv.OUTPUTS.resolve()
    # The guard rejects it.
    assert srv._within_outputs(escaped) is False


def test_within_outputs_allows_canonical_sidecar():
    srv = _load_serve_report()
    from module_6b.selection_io import selection_json_path

    good = selection_json_path(srv.OUTPUTS / "final_ranking_2025Q4.html")
    assert srv._within_outputs(good) is True


# ─────────────────────────────── S8 — CSRF ───────────────────────────────────

class _FakeHeaders(dict):
    def get(self, key, default=None):  # mimic email.message.Message.get
        return super().get(key, default)


class _FakeServer:
    server_address = ("127.0.0.1", 4609)


def _origin_handler(origin):
    """Build a bare object exposing the unbound _origin_ok with faked state."""
    srv = _load_serve_report()
    handler = srv.SelectionHandler.__new__(srv.SelectionHandler)
    handler.headers = _FakeHeaders()
    if origin is not None:
        handler.headers["Origin"] = origin
    handler.server = _FakeServer()
    return handler


def test_origin_ok_allows_missing_origin_local_cli():
    # No Origin header → local tooling (curl/python) → allowed.
    assert _origin_handler(None)._origin_ok() is True


@pytest.mark.parametrize("origin", [
    "http://127.0.0.1:4609",
    "http://localhost:4609",
])
def test_origin_ok_allows_same_origin(origin):
    assert _origin_handler(origin)._origin_ok() is True


@pytest.mark.parametrize("origin", [
    "http://evil.example.com",
    "https://127.0.0.1:4609",       # wrong scheme
    "http://127.0.0.1:5000",        # wrong port
    "http://attacker.localhost:4609",
    "null",
])
def test_origin_ok_rejects_cross_origin(origin):
    assert _origin_handler(origin)._origin_ok() is False


# ─────────────────────────── S10 — response size cap ─────────────────────────

class _FakeResp:
    """Minimal stand-in for a urllib response: .read(n) returns bytes."""
    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)

    def read(self, n=-1):
        return self._buf.read(n)


def test_edgar_read_capped_accepts_under_limit():
    from layer_1 import edgar_13f
    body = edgar_13f._read_capped(_FakeResp(b"x" * 500), max_bytes=1000)
    assert body == b"x" * 500


def test_edgar_read_capped_accepts_exactly_at_limit():
    from layer_1 import edgar_13f
    body = edgar_13f._read_capped(_FakeResp(b"x" * 1000), max_bytes=1000)
    assert len(body) == 1000


def test_edgar_read_capped_rejects_over_limit():
    from layer_1 import edgar_13f
    with pytest.raises(ValueError):
        edgar_13f._read_capped(_FakeResp(b"x" * 1001), max_bytes=1000)


def test_sec_resolver_read_capped_rejects_over_limit():
    from layer_1 import sec_ticker_resolver
    with pytest.raises(ValueError):
        sec_ticker_resolver._read_capped(_FakeResp(b"x" * 2001), max_bytes=2000)


def test_size_cap_constants_present():
    """Every external getter module carries an explicit OOM cap constant."""
    from layer_1 import edgar_13f, sec_ticker_resolver, cusip_resolver
    from module_4c import edgar_client
    for mod in (edgar_13f, sec_ticker_resolver, cusip_resolver, edgar_client):
        assert mod.MAX_RESPONSE_BYTES == 64 * 1024 * 1024


# ─────────────────────────── S11 — defused XML ───────────────────────────────

def test_form4_parser_rejects_billion_laughs():
    """An entity-expansion bomb must not blow up memory; defusedxml raises and
    the parser fails open with empty rows + an error string."""
    from module_4c.edgar_client import _parse_form4_xml
    bomb = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b'<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
        b']><ownershipDocument>&lol3;</ownershipDocument>'
    )
    rows, err = _parse_form4_xml(bomb)
    assert rows == []
    assert err is not None


def test_13f_parser_rejects_entity_bomb():
    from layer_1.edgar_13f import parse_13f_xml
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        ']><informationTable>&lol2;</informationTable>'
    )
    # Fails open (empty list) rather than expanding the entity.
    assert parse_13f_xml(bomb) == []


def test_13f_parser_still_parses_valid_xml():
    """Defusing must not regress normal parsing of a benign info table."""
    from layer_1.edgar_13f import parse_13f_xml
    xml = (
        '<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">'
        '<infoTable>'
        '<nameOfIssuer>ACME CORP</nameOfIssuer>'
        '<titleOfClass>COM</titleOfClass>'
        '<cusip>000000000</cusip>'
        '<value>1234</value>'
        '<shrsOrPrnAmt><sshPrnamt>100</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>'
        '</infoTable>'
        '</informationTable>'
    )
    rows = parse_13f_xml(xml)
    assert len(rows) == 1
    assert rows[0]["cusip"] == "000000000"
    assert rows[0]["shares"] == 100
