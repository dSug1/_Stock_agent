"""Module 7 — cacheable-prefix loader.

The Anthropic system block is the concatenation of:

    config/module_7_system_prompt.md  +  config/module_7_few_shots.md

A single `cache_control: {type: "ephemeral"}` breakpoint sits at the end
of the few-shots, so the entire prefix is one cached unit. A 100% prefix-
cache hit rate is expected across an M7 run.

This module's only job is to read both files, concatenate them with a
single newline separator, and return the resulting string. Token
counting + pricing live in cost_estimate.py.

Copy-adapted from `2_Funds_parser/src/module_6/prompt.py`.
"""
from __future__ import annotations

from pathlib import Path


def load_cacheable_prefix(
    system_prompt_path: Path,
    few_shots_path: Path,
) -> str:
    """Read system prompt + few-shots and return the concatenated prefix.

    Order is system_prompt first, few_shots second — the few-shots file
    should end with a cue line indicating that the next user message is
    the real ticker.

    Raises:
        FileNotFoundError: if either path is missing.
    """
    system_text = system_prompt_path.read_text(encoding="utf-8")
    few_shots_text = few_shots_path.read_text(encoding="utf-8")
    return system_text + "\n" + few_shots_text
