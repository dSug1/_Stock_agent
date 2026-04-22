"""Module 1 — Configuration & Foundation.

Non-runtime foundation layer. Loads and validates pipeline.yaml, resolves paths,
loads repo-root .env, configures logging, exposes quarter helpers. Imported by
every runtime module (3-7).

Module 2 (13F ingest) predates Module 1 and is not retrofitted.
"""
from .config import ConfigError, PipelineConfig, load_config
from .logging_setup import get_logger
from .paths import PROJECT_ROOT, REPO_ROOT, ensure_dir, resolve
from .quarter import (
    date_to_quarter,
    is_valid_quarter,
    prior_quarter,
    quarter_to_date_end,
    resolve_quarter,
)

__all__ = [
    "ConfigError",
    "PipelineConfig",
    "load_config",
    "get_logger",
    "PROJECT_ROOT",
    "REPO_ROOT",
    "ensure_dir",
    "resolve",
    "date_to_quarter",
    "is_valid_quarter",
    "prior_quarter",
    "quarter_to_date_end",
    "resolve_quarter",
]
