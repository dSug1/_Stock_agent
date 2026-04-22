"""Shared logging setup. One logger per module (e.g. module_3, module_4a).

Debug mode forces DEBUG level regardless of config.logging.level.
File rotation uses TimedRotatingFileHandler, daily at midnight,
retained config.logging.file_rotation_days days (default 14).
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from typing import TYPE_CHECKING

from .paths import ensure_dir

if TYPE_CHECKING:
    from .config import PipelineConfig

_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_configured: set[str] = set()


def get_logger(module_name: str, config: "PipelineConfig | None" = None) -> logging.Logger:
    logger = logging.getLogger(module_name)
    if module_name in _configured:
        return logger

    if config is None:
        # Bare logger; caller hasn't loaded config yet. Leave handler setup to
        # the first call that passes a config.
        logger.setLevel(logging.INFO)
        return logger

    level = logging.DEBUG if config.mode == "debug" else getattr(
        logging, config.logging.level, logging.INFO
    )
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(_FORMAT)

    if config.logging.console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        ch.setLevel(level)
        logger.addHandler(ch)

    ensure_dir(config.paths.logs_dir)
    fh = TimedRotatingFileHandler(
        config.paths.logs_dir / "pipeline.log",
        when="midnight",
        backupCount=config.logging.file_rotation_days,
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    fh.setLevel(level)
    logger.addHandler(fh)

    _configured.add(module_name)
    return logger
