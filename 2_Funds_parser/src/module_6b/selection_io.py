"""Module 6b Part (b) — sidecar selection JSON I/O.

Selection state + Part (a) user-weight overrides live in a JSON file
alongside the rendered HTML report, e.g.
``Outputs/final_ranking_2025Q4_selection.json``. The local HTTP server
(``scripts/6_serve_report.py``) reads/writes this file in response to
GET/PUT from the browser; the pipeline (``scripts/6_score.py
--selection-from-html``) reads it before dispatch.

Schema v2 (D50 — adds ``modifier_weights``)::

    {
      "schema_version":   2,
      "quarter":          "2025Q4",
      "rendered_at":      "2026-04-25T13:30:00Z",
      "updated_at":       "2026-04-25T14:01:23Z",   // present only after a save
      "all_tickers":      ["NTLA", "TCRX"],
      "selected_tickers": ["TCRX"],
      "modifier_weights": {                          // D50 — defaults to all-1.0
         "crowding": 1.00, "financing": 1.00, "dilution": 1.00,
         "insider": 1.00,  "mgmt": 1.00,      "acquisition": 1.00,
         "moat": 1.00,     "failures": 1.00,  "concentration": 1.00
      }
    }

Schema v1 (no modifier_weights) is still readable; missing block is
treated as all-1.0 by ``read_modifier_weights`` and by JS-side fallback.

Spec: 2_Funds_parser/spec/module_6b_spec.md § Part (b), Part (a).
Decision: 2_Funds_parser/spec/decisions.md § D48, D49, D50.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2

# Component name order is fixed across the system (Python, JSON, JS)
# so server- and client-side modifier products are bit-identical.
COMPONENT_NAMES: tuple[str, ...] = (
    "crowding", "financing", "dilution", "insider", "mgmt",
    "acquisition", "moat", "failures", "concentration",
)
DEFAULT_WEIGHT = 1.0

# Process-wide lock so concurrent server PUTs + pipeline reads don't
# tear a half-written file. Acquired around every read/write.
_LOCK = threading.Lock()


def selection_json_path(html_path: Path) -> Path:
    """Derive the sidecar JSON path from a rendered HTML report path.

    ``Outputs/final_ranking_2025Q4.html`` →
    ``Outputs/final_ranking_2025Q4_selection.json``
    """
    p = Path(html_path)
    return p.with_name(p.stem + "_selection.json")


def load_selection_json(path: Path) -> dict | None:
    """Return the parsed JSON if the file exists; else None.

    Returns None on any I/O or parse failure — caller decides how to
    recover (typically: fall back to defaults).
    """
    p = Path(path)
    if not p.exists():
        return None
    with _LOCK:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    return data


def default_modifier_weights() -> dict[str, float]:
    """All-1.0 weights — what the JS-side reset-all button restores to."""
    return {n: DEFAULT_WEIGHT for n in COMPONENT_NAMES}


def read_modifier_weights(prior: dict | None) -> dict[str, float]:
    """Extract the modifier_weights block from a sidecar dict.

    Missing / non-dict / non-numeric values default to ``DEFAULT_WEIGHT``.
    Unknown component keys are silently ignored.
    """
    out = default_modifier_weights()
    if not isinstance(prior, dict):
        return out
    raw = prior.get("modifier_weights")
    if not isinstance(raw, dict):
        return out
    for n in COMPONENT_NAMES:
        v = raw.get(n)
        if isinstance(v, (int, float)):
            out[n] = float(v)
    return out


def merge_modifier_weights(
    *, prior: dict | None,
) -> dict[str, float]:
    """D50 — preserve user weights across pipeline re-renders.

    The pipeline re-render carries the prior sidecar's weights forward
    unchanged. Per the user's stated policy, weights survive pipeline
    runs; only the JS-side "reset all" button (or per-slider reset)
    clears them.
    """
    return read_modifier_weights(prior)


def write_selection_json(
    path: Path,
    *,
    quarter: str,
    all_tickers: list[str],
    selected_tickers: list[str],
    rendered_at: str | None = None,
    modifier_weights: dict[str, float] | None = None,
) -> None:
    """Atomically write the sidecar JSON.

    Uses tmp + os.replace so a concurrent reader never sees a truncated file.
    Ticker lists are sorted (deterministic for diffs / version control).
    ``modifier_weights`` defaults to all-1.0 if omitted.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    weights = dict(default_modifier_weights())
    if modifier_weights:
        for n in COMPONENT_NAMES:
            v = modifier_weights.get(n)
            if isinstance(v, (int, float)):
                weights[n] = float(v)
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "quarter": quarter,
        "rendered_at": rendered_at or _now_iso(),
        "all_tickers": sorted(set(all_tickers)),
        "selected_tickers": sorted(set(selected_tickers)),
        "modifier_weights": weights,
    }
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    tmp = p.with_suffix(p.suffix + ".tmp")
    with _LOCK:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)


def stamp_update(path: Path, payload: dict) -> dict:
    """Atomically overwrite the sidecar with a server-supplied payload.

    Adds/refreshes ``updated_at`` and pins ``quarter`` from the URL path
    (server is the authority on which quarter the request is for, not the
    client body).
    """
    payload = dict(payload)
    payload["updated_at"] = _now_iso()
    payload.setdefault("schema_version", SCHEMA_VERSION)
    body = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with _LOCK:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)
    return payload


def merge_selection(
    *,
    new_pool: list[str],
    prior: dict | None,
) -> set[str]:
    """Compute the selected-tickers set for a re-render.

    Rule (matches the HTML round-trip behaviour from the FSA era):
      - tickers in ``new_pool`` AND in prior `selected_tickers` → kept
      - tickers in ``new_pool`` but NOT in prior `all_tickers`   → defaulted to selected (new entry)
      - tickers in prior but no longer in ``new_pool``           → dropped silently
      - prior is None (first render)                              → all selected
    """
    pool = set(new_pool)
    if not prior:
        return set(pool)
    prev_selected = set(prior.get("selected_tickers") or [])
    prev_all = set(prior.get("all_tickers") or [])
    kept = prev_selected & pool
    new_entries = pool - prev_all
    return kept | new_entries


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
