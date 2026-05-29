"""Module 8 config loader — thin wrapper that reuses M7's Pydantic models.

config/module_8.yaml looks much like module_7.yaml but with:
  * a different `prompt_version_label` (so cache separates m7 vs m8)
  * a different `system_prompt_path` pointing at M8's prefix file
  * everything else (dispatch, modifiers, clamps, pricing, ceiling)
    inherited / reused as M7 has tuned them.

Per the user's spec the modifiers/clamps/pricing/ceiling values are the
same as M7 today (we just want a different prompt + cache namespace).
We could DRY this by importing M7's config and only overriding the
prompt fields, but a flat YAML is simpler to edit by hand and only
~30 lines either way.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from module_7.config import Module7Config


class Module8Config(Module7Config):
    """Same Pydantic shape as Module7Config — reused intentionally so
    M8 dispatch can pass this object anywhere M7 dispatch accepts one.
    The only practical difference is the `prompt_version_label` value
    (so cache hits don't bleed across modules)."""


def _content_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:7]


def load_module_8_config(path: Path | str) -> Module8Config:
    """Load + validate config/module_8.yaml.

    Stamps ``prompt_version`` as ``"{label}:{sha7}"``. Any edit to the
    YAML or the referenced system prompt prefix should bump the version.
    We additionally fold the SHA of the prefix file into the prompt
    version so editing the date-retrieval instructions invalidates the
    cache the same way editing the YAML does.
    """
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg = Module8Config.model_validate(raw)
    yaml_sha = _content_hash(p)
    # Also mix in the prefix file SHA so editing the rescue prompt
    # text alone invalidates the cache (same trick D17 uses).
    prefix_path = p.parent / Path(cfg.system_prompt_path).name
    prefix_sha = _content_hash(prefix_path) if prefix_path.exists() else "noprefix"
    cfg.prompt_version = f"{cfg.prompt_version_label}:{yaml_sha}+{prefix_sha[:7]}"
    return cfg


def default_config_path() -> Path:
    """3_Biopharmcatalyst_parser/config/module_8.yaml."""
    here = Path(__file__).resolve()
    project_root = here.parents[2]
    return project_root / "config" / "module_8.yaml"
