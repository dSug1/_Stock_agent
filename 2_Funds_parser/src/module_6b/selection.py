"""Module 6b Part (b) — parse the user's per-ticker selection from a saved
final-ranking HTML report.

The HTML report rendered by `module_6.reports.render_final_ranking_html`
gains a leftmost checkbox column. When the user clicks "Save selection"
in the browser, the JS rewrites the `checked` HTML attribute on every
`<input name="ticker_select">` to match its current state and saves the
file back to disk. This module reads that file and returns the list of
checked tickers — used by `scripts/6_score.py --selection-from-html`.

Stdlib `re` only — no BeautifulSoup, no extra deps. Tolerant of
attribute reordering (`value="X" checked` and `checked value="X"` both
work) and quote style.

Spec: 2_Funds_parser/spec/module_6b_spec.md § Part (b).
Decision: 2_Funds_parser/spec/decisions.md § D48.
"""
from __future__ import annotations

import re
from pathlib import Path

# Match an <input> tag whose `name` attr equals "ticker_select" AND that
# carries `checked` (with or without value). Attribute order is irrelevant;
# `[^>]*` consumes any other attrs in between.
_TICKER_CHECKBOX_RE = re.compile(
    r"<input\b[^>]*\bname=['\"]?ticker_select['\"]?[^>]*\bchecked\b[^>]*?>",
    re.IGNORECASE,
)
# Same shape but `checked` appears BEFORE `name` — second pass ensures we
# don't miss tags where the attribute order is reversed by serialisation.
_TICKER_CHECKBOX_RE_REV = re.compile(
    r"<input\b[^>]*\bchecked\b[^>]*\bname=['\"]?ticker_select['\"]?[^>]*?>",
    re.IGNORECASE,
)
_VALUE_RE = re.compile(r"\bvalue=['\"]([A-Za-z0-9.\-]+)['\"]", re.IGNORECASE)


def parse_selected_tickers(html_path: Path) -> list[str]:
    """Return the list of tickers whose checkbox is `checked` in the HTML.

    Order matches first-seen position in the document; duplicates removed.
    Returns an empty list if no `ticker_select` checkboxes exist (e.g. the
    file is not a final-ranking report) — caller should treat that as
    "nothing selected" and bail rather than dispatching everything.
    """
    text = Path(html_path).read_text(encoding="utf-8")
    tags: list[str] = []
    tags.extend(_TICKER_CHECKBOX_RE.findall(text))
    # Second pass for reverse attr order; dedupe via offsets isn't needed
    # because `_VALUE_RE` extraction + ordered set below collapses duplicates.
    tags.extend(_TICKER_CHECKBOX_RE_REV.findall(text))

    seen: set[str] = set()
    ordered: list[str] = []
    for tag in tags:
        m = _VALUE_RE.search(tag)
        if not m:
            continue
        t = m.group(1).upper()
        if t in seen:
            continue
        seen.add(t)
        ordered.append(t)
    return ordered


def parse_all_tickers(html_path: Path) -> list[str]:
    """Return every ticker whose checkbox is rendered in the HTML, regardless
    of `checked` state. Used to compute "X of Y" for the pre-flight summary.
    """
    text = Path(html_path).read_text(encoding="utf-8")
    pattern = re.compile(
        r"<input\b[^>]*\bname=['\"]?ticker_select['\"]?[^>]*?>",
        re.IGNORECASE,
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in pattern.findall(text):
        m = _VALUE_RE.search(tag)
        if not m:
            continue
        t = m.group(1).upper()
        if t in seen:
            continue
        seen.add(t)
        ordered.append(t)
    return ordered
