"""Founder / scientific-pedigree matching (spec §5.6.1).

Matches a company's people (its top OpenAlex authors, and any officer/founder names) against the
curated `config/prestige_labs.yaml` list of major awardees and prestige-lab PIs. Matching is on
NORMALIZED FULL NAME (exact) to keep precision high — a spurious surname collision would inject a
false high-value signal, the opposite of what this is for.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import yaml

_NOISE = re.compile(r"[^a-z ]+")


def _norm(name: str) -> str:
    return _NOISE.sub("", (name or "").lower()).strip()


def load_prestige(path: str | Path = "config/prestige_labs.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        return {"awardees": [], "labs": []}
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build_index(data: dict) -> dict[str, dict]:
    """normalized-name -> {name, recognition, field} for awardees + lab PIs."""
    idx: dict[str, dict] = {}
    for a in data.get("awardees", []) or []:
        if a.get("name"):
            idx[_norm(a["name"])] = {"name": a["name"], "recognition": a.get("recognition"),
                                     "field": a.get("field")}
    for lab in data.get("labs", []) or []:
        if lab.get("pi") and _norm(lab["pi"]) not in idx:
            idx[_norm(lab["pi"])] = {"name": lab["pi"],
                                     "recognition": f"prestige lab: {lab.get('lab', '')} "
                                                    f"({lab.get('institution', '')})".strip()}
    return idx


def match(names: Iterable[str], index: dict[str, dict]) -> list[dict[str, Any]]:
    """Return the prestige records whose name exactly (normalized) matches one of ``names``."""
    seen: list[str] = []
    out: list[dict[str, Any]] = []
    for n in names:
        key = _norm(n)
        if key and key in index and key not in seen:
            seen.append(key)
            out.append(index[key])
    return out
