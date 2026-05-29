"""Module 8 — rescue-mode cacheable-prefix loader.

The rescue prompt is the M7 system prompt with a DATE-RETRIEVAL
PREAMBLE prepended:

    [rescue preamble]      ← config/module_8_system_prompt_prefix.md
    [M7 system prompt]     ← config/module_7_system_prompt.md (unchanged)
    [M7 few-shots]         ← config/module_7_few_shots.md (unchanged)

The single Anthropic ephemeral cache breakpoint sits at the end (same
as M7 — see module_7.prompt). Because the rescue preamble lands BEFORE
the cached block, edits to it correctly invalidate the cache.

We deliberately reuse the M7 system prompt + few-shots unchanged. The
rescue preamble adds a NEW first task — "resolve the catalyst date" —
but the scoring rubric, HARD RULES, JSON schema, etc. are all M7's.
The schema in the prefix instructs Claude to ADD two new fields
(claude_resolved_catalyst_date, catalyst_date_source) to its JSON
output for B/C rescues; m7's parser is taught to accept them via
parsing.py D35 changes.
"""
from __future__ import annotations

from pathlib import Path


def load_rescue_prefix(
    rescue_preamble_path: Path,
    m7_system_prompt_path: Path,
    m7_few_shots_path: Path,
) -> str:
    """Concatenate (rescue preamble + M7 system prompt + M7 few-shots).

    Order is preamble FIRST so the date-retrieval instruction sets the
    Claude turn's primary task before the original M7 prompt's scoring
    rubric kicks in.
    """
    preamble = rescue_preamble_path.read_text(encoding="utf-8")
    system_text = m7_system_prompt_path.read_text(encoding="utf-8")
    few_shots_text = m7_few_shots_path.read_text(encoding="utf-8")
    return preamble + "\n\n" + system_text + "\n" + few_shots_text
