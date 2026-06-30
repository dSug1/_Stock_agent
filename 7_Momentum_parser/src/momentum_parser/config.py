"""Config + path resolution. All tunables live in ``config/config.yaml`` (no magic numbers in code)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# Component root = the dir that holds config/, data/, Outputs/ (parent of src/).
ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "config.yaml"


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Load YAML config with ``yaml.safe_load`` (never ``load``; repo security invariant)."""
    p = Path(path) if path else CONFIG_PATH
    with open(p, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    cfg.setdefault("_root", str(ROOT))
    return cfg


def data_dir(cfg: dict) -> Path:
    d = ROOT / "data"
    d.mkdir(exist_ok=True)
    return d


def outputs_dir(cfg: dict) -> Path:
    # Honor an explicit override (tests MUST set this so a dry-run never clobbers the real
    # user-facing Outputs/ deliverables — see decisions.md D-6 / the post-mortem).
    override = (cfg or {}).get("outputs", {}).get("dir")
    d = Path(override) if override else ROOT / "Outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path(cfg: dict) -> Path:
    return data_dir(cfg) / cfg.get("store", {}).get("db_file", "momentum.db")
