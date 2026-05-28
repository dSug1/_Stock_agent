"""Convert BPC catalyst .docx exports to the M1-compatible .csv shape.

The user pastes BPC's website data table into a Word document; that
.docx then contains raw HTML markup (table + tr + td) inside its
paragraphs. This package parses the HTML, prefers each cell's
``blurred-text`` attribute (BPC's canonical machine-readable value) over
the human-display text, and writes a 19-column CSV identical in shape
to the file M1's pydantic schema expects.

See ``spec/biotech_pipeline_spec.md`` §2 (Module 0) and ``spec/decisions.md`` D14.
"""
from .convert import (
    COLUMN_MAP,
    auto_convert_directory,
    convert,
    extract_table,
)

__all__ = ["COLUMN_MAP", "auto_convert_directory", "convert", "extract_table"]
