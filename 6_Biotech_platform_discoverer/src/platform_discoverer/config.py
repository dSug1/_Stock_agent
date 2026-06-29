"""Config loading (spec §3 config.yaml + §4 taxonomy.yaml).

All tunables live in YAML so re-runs with different thresholds need no code change. Uses
``yaml.safe_load`` only (repo security baseline — never ``yaml.load``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = "config/config.yaml"
DEFAULT_TAXONOMY = "config/taxonomy.yaml"

# The cardinal rule, hard-coded as the fallback set so the guardrail holds even if config omits it.
CARDINAL_DELETION_REASONS: frozenset[str] = frozenset({"mktcap_out_of_band", "not_live"})


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Parse a YAML file with the safe loader. Raises FileNotFoundError if absent."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data or {}


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    """Load the runtime config (§3)."""
    return load_yaml(path)


def load_taxonomy(path: str | Path = DEFAULT_TAXONOMY) -> dict[str, Any]:
    """Load the controlled mechanism vocabulary (§4)."""
    return load_yaml(path)


# The config sections that change a Claude SCORE. A change here forces a re-score (§12), bypassing
# the rescore-TTL; changes to unrelated sections (e.g. Stage-0 nets) do not.
SCORING_CONFIG_KEYS = ("stage4_scoring", "composite_weights", "penalties")


def config_hash(config: dict[str, Any] | None) -> str:
    """Stable 16-hex hash of the SCORING-relevant config (§12 incremental re-runs). Deterministic:
    sorted-key JSON so key order / whitespace never shift the hash."""
    subset = {k: (config or {}).get(k) for k in SCORING_CONFIG_KEYS}
    blob = json.dumps(subset, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def deletion_allowed_reasons(config: dict[str, Any] | None) -> frozenset[str]:
    """The ONLY reasons a company may be deleted (cardinal rule §0.2).

    Read from ``stage1_filters.deletion_allowed_reasons`` but INTERSECTED with the hard-coded
    cardinal set: config may narrow the allowed reasons, never widen them. A misconfigured file
    can therefore never authorize an illegal deletion.
    """
    configured = None
    if config:
        configured = (config.get("stage1_filters") or {}).get("deletion_allowed_reasons")
    if not configured:
        return CARDINAL_DELETION_REASONS
    return CARDINAL_DELETION_REASONS & frozenset(configured)
