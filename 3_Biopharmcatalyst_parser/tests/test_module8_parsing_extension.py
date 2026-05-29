"""Module 8 (D35) — parser tolerates Claude-resolved date + source fields."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.parsing import ParseError, parse_deep_dive  # noqa: E402

# Reuse the minimal_valid fixture by importing it from the M7 parser test.
sys.path.insert(0, str(PROJECT_ROOT / "tests"))
from test_module7_parsing import _minimal_valid  # type: ignore  # noqa: E402


def _wrap(obj: dict) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


def test_m8_resolved_date_round_trips():
    payload = _minimal_valid(
        claude_resolved_catalyst_date="2026-08-15",
        catalyst_date_source="per Q1 2026 8-K filed 2026-04-30: 'expected H2 2026'",
    )
    p = parse_deep_dive(_wrap(payload))
    assert p.claude_resolved_catalyst_date == "2026-08-15"
    assert "Q1 2026 8-K" in p.catalyst_date_source


def test_m8_resolved_date_optional_default_none():
    # Standard M7 responses omit the fields entirely; parser must tolerate.
    payload = _minimal_valid()
    payload.pop("claude_resolved_catalyst_date", None)
    payload.pop("catalyst_date_source", None)
    p = parse_deep_dive(_wrap(payload))
    assert p.claude_resolved_catalyst_date is None
    assert p.catalyst_date_source is None


def test_m8_resolved_date_must_be_string_when_present():
    payload = _minimal_valid(claude_resolved_catalyst_date=12345)
    with pytest.raises(ParseError) as excinfo:
        parse_deep_dive(_wrap(payload))
    assert excinfo.value.kind == "schema_violation"
    assert "claude_resolved_catalyst_date" in excinfo.value.detail


def test_m8_date_source_must_be_string_when_present():
    payload = _minimal_valid(catalyst_date_source={"bad": "nested"})
    with pytest.raises(ParseError) as excinfo:
        parse_deep_dive(_wrap(payload))
    assert excinfo.value.kind == "schema_violation"
    assert "catalyst_date_source" in excinfo.value.detail


def test_m8_resolved_date_null_is_accepted():
    payload = _minimal_valid(
        claude_resolved_catalyst_date=None,
        catalyst_date_source=None,
    )
    p = parse_deep_dive(_wrap(payload))
    assert p.claude_resolved_catalyst_date is None
    assert p.catalyst_date_source is None
