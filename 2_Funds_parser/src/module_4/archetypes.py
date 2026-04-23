"""Archetype matching for Module 4b.

Each archetype specifies ranges on a SUBSET of the 10 ratio features
(decision D5). Match confidence = matched_ranges / specified_ranges.
A ticker is assigned the qualifying archetype with highest confidence;
ties broken by highest |archetype_score| (most opinionated wins).
Below min_confidence -> 'unclassified'.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from module_1 import ConfigError


_VALID_FEATURES = {
    "R_4", "R_12", "R_26", "R_52",
    "R_4_over_R_12", "R_4_over_R_26", "R_4_over_R_52",
    "R_12_over_R_26", "R_12_over_R_52", "R_26_over_R_52",
}


def load_archetypes(path: Path) -> dict:
    """Load + validate archetypes.yaml. Returns the 'archetypes' mapping."""
    if not path.exists():
        raise ConfigError(f"archetypes config not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        loc = f"{path}:{mark.line + 1}:{mark.column + 1} " if mark else f"{path} "
        raise ConfigError(f"{loc}YAML syntax error: {e}") from e

    if not isinstance(raw, dict) or "archetypes" not in raw:
        raise ConfigError(f"{path}: top-level 'archetypes' key required")
    archetypes = raw["archetypes"]
    if not isinstance(archetypes, dict) or not archetypes:
        raise ConfigError(f"{path}: archetypes must be a non-empty mapping")

    for name, spec in archetypes.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"{path}: archetype '{name}' must be a mapping")
        if "score" not in spec:
            raise ConfigError(f"{path}: archetype '{name}' missing 'score'")
        if not isinstance(spec["score"], (int, float)):
            raise ConfigError(
                f"{path}: archetype '{name}' score must be numeric"
            )
        ranges = spec.get("ranges")
        if not isinstance(ranges, dict) or not ranges:
            raise ConfigError(
                f"{path}: archetype '{name}' must have non-empty 'ranges'"
            )
        for feat, bounds in ranges.items():
            if feat not in _VALID_FEATURES:
                raise ConfigError(
                    f"{path}: archetype '{name}' has unknown feature '{feat}'. "
                    f"Valid: {sorted(_VALID_FEATURES)}"
                )
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise ConfigError(
                    f"{path}: archetype '{name}'.ranges.{feat} must be [lo, hi]"
                )
            lo, hi = bounds
            if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
                raise ConfigError(
                    f"{path}: archetype '{name}'.ranges.{feat} bounds must be numeric"
                )
            if lo > hi:
                raise ConfigError(
                    f"{path}: archetype '{name}'.ranges.{feat} lo > hi"
                )
    return archetypes


def _match_one(features: dict, archetype_spec: dict) -> float:
    ranges = archetype_spec["ranges"]
    matches = 0
    for feat, (lo, hi) in ranges.items():
        v = features.get(feat)
        if v is None:
            continue
        if lo <= v <= hi:
            matches += 1
    return matches / len(ranges)


def match_archetypes(
    features: dict[str, Optional[float]],
    archetypes: dict,
    min_confidence: float = 0.70,
) -> tuple[str, float, float]:
    """Returns (archetype_name, archetype_score, match_confidence).

    `features` should contain the 10 ratio keys. None values count as
    non-matching (decision D5: unspecified is fine; missing for specified
    is a miss).
    """
    best_name = "unclassified"
    best_score = 0.0
    best_conf = 0.0
    for name, spec in archetypes.items():
        conf = _match_one(features, spec)
        if conf < min_confidence:
            continue
        score = float(spec["score"])
        if (conf > best_conf
                or (conf == best_conf and abs(score) > abs(best_score))):
            best_name = name
            best_score = score
            best_conf = conf
    return best_name, best_score, best_conf
