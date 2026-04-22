"""Canonical path resolution for 2_Funds_parser.

All relative paths in pipeline.yaml resolve against PROJECT_ROOT
(= 2_Funds_parser/) regardless of where Python is invoked from.
REPO_ROOT is one level up (where .env and the shared venv live).
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PROJECT_ROOT.parent


def resolve(relative: str | Path, project_root: Path | None = None) -> Path:
    root = Path(project_root) if project_root else PROJECT_ROOT
    p = Path(relative)
    return p.resolve() if p.is_absolute() else (root / p).resolve()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
