"""Archetype matching for Module 4b.

Each archetype specifies ranges on a SUBSET of the 10 ratio features
(decision D5). Match confidence = matched_ranges / specified_ranges.
A ticker is assigned the qualifying archetype with highest confidence;
ties broken by highest max(|score_h|) across defined horizons (most
opinionated wins, regardless of horizon). Below min_confidence -> 'unclassified'.

Dual-horizon schema (D20): each archetype carries a `scores: {<horizon>: int}`
mapping. Module 4b computes composites per horizon and lets downstream
code pick the best horizon per ticker.
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
    """Load + validate archetypes.yaml. Returns the 'archetypes' mapping.

    Each archetype's `scores` must be a non-empty mapping of horizon -> int.
    Per-horizon D19 invariant (unique integer scores, no zero) is enforced.
    """
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

    # Per-horizon uniqueness tracker: {horizon: {score: archetype_name}}
    per_horizon_scores: dict[str, dict[int, str]] = {}

    for name, spec in archetypes.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"{path}: archetype '{name}' must be a mapping")

        scores = spec.get("scores")
        if not isinstance(scores, dict) or not scores:
            raise ConfigError(
                f"{path}: archetype '{name}' must have non-empty 'scores' mapping "
                f"(e.g. scores: {{'3mo': 10, '12mo': 8}})"
            )
        for horizon, score_val in scores.items():
            if not isinstance(horizon, str):
                raise ConfigError(
                    f"{path}: archetype '{name}'.scores keys must be strings (got {horizon!r})"
                )
            if isinstance(score_val, bool) or not isinstance(score_val, int):
                # bool is a subclass of int; reject it explicitly.
                raise ConfigError(
                    f"{path}: archetype '{name}'.scores.{horizon} must be integer (got {score_val!r})"
                )
            if score_val == 0:
                raise ConfigError(
                    f"{path}: archetype '{name}'.scores.{horizon} = 0 is reserved for "
                    f"unclassified (D19)"
                )
            if not -10 <= score_val <= 10:
                raise ConfigError(
                    f"{path}: archetype '{name}'.scores.{horizon} = {score_val} "
                    f"outside supported range [-10, +10] (D19 / decisions_module_4.md)"
                )
            taken = per_horizon_scores.setdefault(horizon, {})
            if score_val in taken:
                raise ConfigError(
                    f"{path}: score {score_val} for horizon '{horizon}' used by both "
                    f"'{taken[score_val]}' and '{name}' — D19 requires unique per horizon"
                )
            taken[score_val] = name

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


def _max_abs_score(scores: dict) -> float:
    """Most-opinionated metric for tiebreaking: max |score_h| across horizons."""
    return max((abs(float(v)) for v in scores.values()), default=0.0)


def match_archetypes(
    features: dict[str, Optional[float]],
    archetypes: dict,
    min_confidence: float = 0.70,
) -> tuple[str, dict, float]:
    """Returns (archetype_name, scores_dict, match_confidence).

    `scores_dict` is the archetype's full {horizon: int} mapping, or an
    empty dict for 'unclassified'. Tie-break prefers the archetype with
    the largest max(|score_h|) — "most opinionated label wins" regardless
    of which horizon carries the opinion.
    """
    best_name = "unclassified"
    best_scores: dict = {}
    best_conf = 0.0
    best_max_abs = 0.0
    for name, spec in archetypes.items():
        conf = _match_one(features, spec)
        if conf < min_confidence:
            continue
        scores = spec["scores"]
        max_abs = _max_abs_score(scores)
        if (conf > best_conf
                or (conf == best_conf and max_abs > best_max_abs)):
            best_name = name
            best_scores = dict(scores)
            best_conf = conf
            best_max_abs = max_abs
    return best_name, best_scores, best_conf
