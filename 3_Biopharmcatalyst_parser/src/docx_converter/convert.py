"""Core docx → csv conversion logic.

Pure functions — no CLI, no print(). Tests in
``tests/test_docx_converter.py``; CLI wrapper in
``scripts/3_0_convert_docx_to_csv.py``.

The docx contains a single HTML table with 20 columns; the target CSV
has 19 columns (the "Options" column at docx index 9 is dropped).
For each row, canonical machine-readable values come from each cell's
inner ``<div blurred-text="…">`` attribute (BPC's data-attribute
convention used to feed JS sort/export). When the attribute is missing
(some cells have only display text), fall back to ``cell.get_text()``.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import docx
from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)


# (csv_column_name, docx_column_index, source_mode)
# Modes:
#   blurred_or_text — read inner element's blurred-text attr, else cell text
#   text            — just visible cell text
#   price_history   — comma-separated "price,unix_ts,price,unix_ts,…";
#                     keep even-indexed values (prices) and join with "; "
#   sentiment       — "Community 50% 50% 0% …" → "Bull 50% / Neutral 50% / Bear 0%"
COLUMN_MAP: list[tuple[str, int, str]] = [
    ("Ticker",              0,  "blurred_or_text"),
    ("Name",                1,  "blurred_or_text"),
    ("Price",               2,  "blurred_or_text"),
    ("30 Day Price Change", 3,  "price_history"),
    # Drug must use blurred_or_text — the visible cell text appends FDA
    # designation badges ("FTD", "BTD", "ODD") and "View Clinical Trial Data"
    # link text after the drug name, which pollutes the PK.
    ("Drug",                4,  "blurred_or_text"),
    ("NCT Number",          5,  "text"),
    ("Indication",          6,  "text"),
    ("Stage",               7,  "blurred_or_text"),
    ("Status",              8,  "text"),
    # docx col 9 ("Options") is dropped — not in the M1 CSV schema
    ("Next Catalyst",       10, "text"),
    ("Catalyst Date",       11, "blurred_or_text"),
    # Catalyst must use blurred_or_text — long catalyst text is truncated
    # in the visible cell with "… read more"; the blurred attribute carries
    # the full text.
    ("Catalyst",            12, "blurred_or_text"),
    ("Conference",          13, "text"),
    ("Historical LOA",      14, "blurred_or_text"),
    ("Historical POP",      15, "blurred_or_text"),
    ("Bullish or Bearish",  16, "sentiment"),
    ("Market Cap",          17, "blurred_or_text"),
    ("Last Updated",        18, "blurred_or_text"),
    ("No Of Shares",        19, "blurred_or_text"),
]

EXPECTED_DOCX_COLS = 20

_SENTIMENT_RE = re.compile(
    r"Community\s+(-?\d+|-)%\s+(-?\d+|-)%\s+(-?\d+|-)%",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConversionStats:
    source: Path
    target: Path
    rows_written: int
    rows_skipped: int


# -----------------------------------------------------------------------------
# Low-level extractors
# -----------------------------------------------------------------------------

def extract_table(docx_path: Path) -> Tag:
    """Read the .docx, concatenate all paragraph text (which contains the
    pasted HTML), parse with BeautifulSoup, and return the first <table>."""
    doc = docx.Document(docx_path)
    html_raw = "\n".join(p.text for p in doc.paragraphs)
    soup = BeautifulSoup(html_raw, "html.parser")
    tbl = soup.find("table")
    if tbl is None:
        raise ValueError(f"no <table> element found in {docx_path}")
    return tbl


def _cell_blurred_or_text(cell: Tag) -> str:
    """Prefer inner <div blurred-text='…'> attribute; fall back to text."""
    bt = cell.find(attrs={"blurred-text": True})
    if bt is not None:
        val = bt.get("blurred-text")
        if val is not None:
            return val.strip()
    return cell.get_text(" ", strip=True)


def _cell_text(cell: Tag) -> str:
    return cell.get_text(" ", strip=True)


def _parse_price_history(cell: Tag) -> str:
    """price_history blurred-text is 'p,ts,p,ts,…'. Keep prices (even
    indices), join with '; '. If blurred-text missing, return ''."""
    bt = cell.find(attrs={"blurred-text": True})
    if bt is None:
        return ""
    raw = bt.get("blurred-text") or ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    prices = parts[::2]   # 0, 2, 4, …
    return "; ".join(prices)


def _parse_sentiment(cell: Tag) -> str:
    """Extract '(bull, neutral, bear)' from 'Community NN% NN% NN% …'.
    BPC uses '-' for 'no votes'."""
    text = cell.get_text(" ", strip=True)
    m = _SENTIMENT_RE.search(text)
    if not m:
        return "Bull -% / Neutral -% / Bear -%"
    bull, neutral, bear = m.group(1), m.group(2), m.group(3)
    return f"Bull {bull}% / Neutral {neutral}% / Bear {bear}%"


_MODE_HANDLERS = {
    "blurred_or_text": _cell_blurred_or_text,
    "text": _cell_text,
    "price_history": _parse_price_history,
    "sentiment": _parse_sentiment,
}


# -----------------------------------------------------------------------------
# Row + file conversion
# -----------------------------------------------------------------------------

def row_to_dict(tds: list[Tag]) -> dict[str, str]:
    """Apply COLUMN_MAP to one <tr>'s cell list. Returns the
    19-column dict ready for csv.DictWriter."""
    out: dict[str, str] = {}
    for csv_col, docx_idx, mode in COLUMN_MAP:
        cell = tds[docx_idx]
        out[csv_col] = _MODE_HANDLERS[mode](cell)
    return out


def convert(docx_path: Path, csv_path: Path | None = None) -> ConversionStats:
    """Convert one docx to one CSV. Overwrites the CSV if it exists.

    Returns ``ConversionStats`` with row counts and the target path.
    """
    if csv_path is None:
        csv_path = docx_path.with_suffix(".csv")

    tbl = extract_table(docx_path)
    body = tbl.find("tbody")
    if body is None:
        raise ValueError(f"<table> in {docx_path} has no <tbody>")

    rows_out: list[dict[str, str]] = []
    skipped = 0
    for tr in body.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < EXPECTED_DOCX_COLS:
            log.warning(
                "row in %s has %d cells; expected %d — skipping",
                docx_path.name, len(tds), EXPECTED_DOCX_COLS,
            )
            skipped += 1
            continue
        rows_out.append(row_to_dict(tds))

    fieldnames = [c[0] for c in COLUMN_MAP]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows_out:
            w.writerow(r)

    return ConversionStats(
        source=docx_path, target=csv_path,
        rows_written=len(rows_out), rows_skipped=skipped,
    )


def auto_convert_directory(source_dir: Path, force: bool = False) -> list[ConversionStats]:
    """For each ``*.docx`` in ``source_dir``, convert to ``*.csv`` if the
    CSV is missing or older than the docx (or ``force`` is True).

    Skips files whose name starts with ``~$`` (Word lock files).
    """
    results: list[ConversionStats] = []
    for docx_file in sorted(source_dir.glob("*.docx")):
        if docx_file.name.startswith("~$"):
            continue
        csv_file = docx_file.with_suffix(".csv")
        if (not force) and csv_file.exists() and (
            csv_file.stat().st_mtime >= docx_file.stat().st_mtime
        ):
            log.info("skip %s — CSV is up-to-date", docx_file.name)
            continue
        log.info("converting %s → %s", docx_file.name, csv_file.name)
        results.append(convert(docx_file, csv_file))
    return results
