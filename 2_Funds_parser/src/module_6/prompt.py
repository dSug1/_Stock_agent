"""Cacheable-prefix loading for Module 6.

The Anthropic system block is the concatenation of:
    config/module_6_system_prompt.md  +  config/module_6_few_shots.md
A single cache_control breakpoint sits at the end of the few-shots, so the
entire prefix is one cached unit.

This module is intentionally tiny — its only job is to read both files,
concatenate them with a separator, and return the resulting string. Token
counting and pricing live in cost_estimate.py.
"""
from __future__ import annotations

from pathlib import Path


def load_cacheable_prefix(
    system_prompt_path: Path,
    few_shots_path: Path,
) -> str:
    """Read system prompt + few-shots and return the concatenated prefix.

    Both files are loaded as UTF-8 text and joined with a single newline
    separator. Order is system_prompt first, few_shots second — the few-shots
    file ends with the cue line that the next user message is the real ticker.

    Raises:
        FileNotFoundError: if either path is missing.
    """
    system_text = system_prompt_path.read_text(encoding="utf-8")
    few_shots_text = few_shots_path.read_text(encoding="utf-8")
    # Single newline join — both files already end with a newline.
    return system_text + "\n" + few_shots_text
