"""Module 7 — config loader (module_7.yaml + pydantic schema) unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from module_7.config import (  # noqa: E402
    Module7Config,
    default_config_path,
    load_module_7_config,
)


def test_default_config_loads():
    """The shipped config/module_7.yaml must validate."""
    cfg = load_module_7_config(default_config_path())
    assert isinstance(cfg, Module7Config)
    assert cfg.model == "claude-opus-4-7"
    assert cfg.prompt_version.startswith("m7-v1:")
    assert len(cfg.prompt_version.split(":")[1]) == 7


def test_prompt_version_changes_on_edit(tmp_path: Path):
    """A 1-byte edit must shift the SHA-7 → new prompt_version string."""
    src = default_config_path().read_text(encoding="utf-8")
    p1 = tmp_path / "m7-a.yaml"
    p2 = tmp_path / "m7-b.yaml"
    p1.write_text(src, encoding="utf-8")
    p2.write_text(src + "\n# trivial comment\n", encoding="utf-8")
    cfg1 = load_module_7_config(p1)
    cfg2 = load_module_7_config(p2)
    assert cfg1.prompt_version != cfg2.prompt_version
    assert cfg1.prompt_version_label == cfg2.prompt_version_label


def test_extra_fields_forbidden(tmp_path: Path):
    raw = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    raw["nonsense_extra_field"] = True
    tmpf = tmp_path / "bad.yaml"
    tmpf.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(Exception):
        load_module_7_config(tmpf)


def test_pricing_model_mismatch_rejected(tmp_path: Path):
    raw = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    raw["pricing"]["model"] = "claude-sonnet-4-6"            # mismatch top-level
    tmpf = tmp_path / "mismatched.yaml"
    tmpf.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(Exception) as ei:
        load_module_7_config(tmpf)
    assert "pricing.model" in str(ei.value)


def test_modifier_range_order_validated(tmp_path: Path):
    raw = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    raw["modifiers"]["insider"]["min_multiplier"] = 1.50
    raw["modifiers"]["insider"]["max_multiplier"] = 0.85
    tmpf = tmp_path / "swapped.yaml"
    tmpf.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(Exception):
        load_module_7_config(tmpf)


def test_cost_calibration_factor_is_0_10():
    """Memory: project_anthropic_cost_calibration locks this at 0.10 in the
    shipped YAML; bumping it requires a deliberate decisions.md entry."""
    cfg = load_module_7_config(default_config_path())
    assert cfg.pricing.cost_calibration_factor == 0.10
