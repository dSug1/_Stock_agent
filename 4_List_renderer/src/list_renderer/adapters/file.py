"""Adapter: a JSON payload file -> list_renderer Highlight list.

The file holds `{ query, brand, results[] }`; for registry use only `results`
is consumed (query/brand come from the board). See data/sample_input.json.
"""

from __future__ import annotations

import json
from pathlib import Path


def fetch_results(input_path: Path | str) -> list[dict]:
    """Return the `results` list from a payload JSON file."""
    data = json.loads(Path(input_path).read_text(encoding="utf-8"))
    return list(data.get("results", []))
