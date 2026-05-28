"""Convert any BPC catalyst .docx files in ``_csv_source/`` to .csv.

Invoked at the top of the pipeline (right before Module 0's DB init).
The user pastes BPC's website data table into a Word document — the
.docx therefore carries raw HTML in its paragraph text. This script
parses that HTML, extracts the per-row canonical values, and writes a
19-column CSV that M1's pydantic schema can ingest.

Default behaviour: scan ``_csv_source/`` for ``*.docx`` files and
convert each that has no sibling ``*.csv`` (or whose ``*.csv`` is
older than the docx). Use ``--force`` to reconvert all.

Single-file mode: pass an explicit path to convert just that file.

Run from `3_Biopharmcatalyst_parser/`:
    PYTHONPATH=src ../.venv/Scripts/python.exe scripts/3_0_convert_docx_to_csv.py
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from docx_converter import auto_convert_directory, convert  # noqa: E402


SOURCE_DIR = PROJECT_ROOT / "_csv_source"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "docx", type=Path, nargs="?", default=None,
        help="single .docx to convert (default: scan _csv_source/ for any new docx)",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="output CSV path for single-file mode (default: same stem as docx)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="reconvert all docx files even if a fresh CSV already exists",
    )
    parser.add_argument(
        "--source-dir", type=Path, default=SOURCE_DIR,
        help=f"directory to scan (default: {SOURCE_DIR.relative_to(PROJECT_ROOT)})",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s docx_converter — %(message)s",
    )

    if args.docx is not None:
        out = args.out or args.docx.with_suffix(".csv")
        stats = convert(args.docx, out)
        size_kb = stats.target.stat().st_size / 1024
        print(f"[3_0_convert_docx] wrote {stats.target.relative_to(PROJECT_ROOT)} "
              f"({size_kb:.1f} KB, {stats.rows_written} rows, {stats.rows_skipped} skipped)")
        return 0

    if not args.source_dir.exists():
        print(f"error: source directory not found: {args.source_dir}", file=sys.stderr)
        return 2
    results = auto_convert_directory(args.source_dir, force=args.force)
    if not results:
        print("[3_0_convert_docx] all CSV files are up-to-date relative to their docx sources.")
        return 0
    for r in results:
        size_kb = r.target.stat().st_size / 1024
        print(f"[3_0_convert_docx] wrote {r.target.relative_to(PROJECT_ROOT)} "
              f"({size_kb:.1f} KB, {r.rows_written} rows, {r.rows_skipped} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
