"""Module 2 — SEC Form 4 transaction code lookup.

Pins the spec §4.3.4 v1 minimum table: only P and S are open-market;
A, M, F, D, G, X, C are not.
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

from module_2.codes import is_open_market, meaning_for  # noqa: E402


@pytest.mark.parametrize("code, expected", [
    ("P", True),   # purchase
    ("S", True),   # sale
    ("p", True),   # case-insensitive
    ("s", True),
    ("A", False),  # grant
    ("M", False),  # option exercise
    ("F", False),  # tax withholding
    ("D", False),  # disposition to issuer
    ("G", False),  # gift
    ("X", False),  # ITM derivative exercise
    ("C", False),  # conversion
    ("Z", False),  # voting trust
    ("",  False),  # missing code → not open-market
    (None, False),  # None → not open-market
    ("XX", False),  # unknown multi-letter
])
def test_is_open_market(code, expected):
    assert is_open_market(code) is expected


def test_meaning_for_known_codes():
    assert "purchase" in meaning_for("P").lower()
    assert "sale" in meaning_for("S").lower()
    assert "grant" in meaning_for("A").lower()
    assert "exercise" in meaning_for("M").lower()


def test_meaning_for_unknown_returns_other():
    # An unknown code must not raise — generic 'Other (X)' fallback.
    assert meaning_for("ZZ") == "Other (ZZ)"


def test_meaning_for_empty_returns_empty():
    assert meaning_for("") == ""
    assert meaning_for(None) == ""
