"""Module 3 — Claude resolver (Phase 3).

Infers how to fetch + extract a source and caches it as a reusable recipe.
Renders replay recipes for free (D4).
"""

from .recipes import get_recipe_by_id, get_recipe_by_signature, save_recipe, source_signature
from .resolve import RECIPE_SCHEMA, load_config, prepare, resolve_source

__all__ = [
    "source_signature",
    "save_recipe",
    "get_recipe_by_signature",
    "get_recipe_by_id",
    "RECIPE_SCHEMA",
    "load_config",
    "prepare",
    "resolve_source",
]
