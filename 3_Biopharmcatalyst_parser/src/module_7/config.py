"""Pydantic-validated loader for config/module_7.yaml.

Same pattern as module_6/config.py: SHA-7 of the file is appended to the
prompt_version label so any edit produces a fresh `prompt_version` string.
That string is persisted on every deep_dives row so a re-run after a
config bump re-scores cleanly.

Spec: spec/module_7_spec.md §6.
Decisions: spec/decisions.md § D16.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ───────────────────────── web_search ─────────────────────────


class WebSearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    max_uses: int = Field(gt=0)
    domains_path: str


# ───────────────────────── selection ──────────────────────────


class SelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_html: str
    sidecar_json: str
    hard_pass_only: bool


# ───────────────────────── dispatch ───────────────────────────


class DispatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["batch", "sync"]
    sync_concurrency: int = Field(gt=0)
    poll_interval_s: int = Field(gt=0)
    timeout_s: int = Field(gt=0)


# ─────────────────────── modifier shapes ──────────────────────


class ModifierShape(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_multiplier: float = Field(gt=0)
    max_multiplier: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_order(self) -> "ModifierShape":
        if self.min_multiplier > self.max_multiplier:
            raise ValueError(
                f"min_multiplier ({self.min_multiplier}) > "
                f"max_multiplier ({self.max_multiplier})"
            )
        return self


class ModifiersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    insider: ModifierShape
    funds: ModifierShape
    momentum: ModifierShape


# ────────────────────────── clamps ────────────────────────────


class ClampsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p_final_min: float = Field(ge=0, le=1)
    p_final_max: float = Field(ge=0, le=1)
    move_on_hit_pct_max: float = Field(gt=0)
    move_on_miss_pct_min: float = Field(lt=0)

    @model_validator(mode="after")
    def _check_order(self) -> "ClampsConfig":
        if self.p_final_min > self.p_final_max:
            raise ValueError("p_final_min must be <= p_final_max")
        return self


# ────────────────────────── pricing ───────────────────────────


class PricingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cache_read_multiplier: float = Field(ge=0)
    cache_creation_multiplier: float = Field(ge=0)
    batch_discount: float = Field(gt=0, le=1)
    web_search_per_1k: float = Field(ge=0)
    search_result_avg_tokens: int = Field(gt=0)
    cost_calibration_factor: float = Field(gt=0)


# ───────────────────────── failures ───────────────────────────


class FailuresConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    abort_threshold_pct: float = Field(ge=0, le=100)


# ─────────────────────── top-level model ──────────────────────


class Module7Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    prompt_version_label: str
    max_output_tokens: int = Field(gt=0)
    system_prompt_path: str
    few_shot_examples_path: str

    web_search: WebSearchConfig
    selection: SelectionConfig
    dispatch: DispatchConfig
    modifiers: ModifiersConfig
    clamps: ClampsConfig
    pricing: PricingConfig
    cost_ceiling_usd: float = Field(gt=0)
    failures: FailuresConfig

    # Computed after load.
    prompt_version: str = ""

    @field_validator("prompt_version_label")
    @classmethod
    def _label_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt_version_label must be non-empty")
        return v

    @model_validator(mode="after")
    def _pricing_model_matches(self) -> "Module7Config":
        if self.pricing.model != self.model:
            raise ValueError(
                f"pricing.model '{self.pricing.model}' does not match top-level "
                f"model '{self.model}' — verify the pricing block was updated "
                f"after a model swap."
            )
        return self


# ───────────────────────── loaders ────────────────────────────


def _content_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:7]


def load_module_7_config(path: Path | str) -> Module7Config:
    """Load + validate config/module_7.yaml.

    Stamps ``prompt_version`` as ``"{label}:{sha7}"``. Any edit to the YAML
    produces a different version, so re-runs after tuning re-score.
    """
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg = Module7Config.model_validate(raw)
    cfg.prompt_version = f"{cfg.prompt_version_label}:{_content_hash(p)}"
    return cfg


def default_config_path() -> Path:
    """3_Biopharmcatalyst_parser/config/module_7.yaml."""
    here = Path(__file__).resolve()
    # src/module_7/config.py -> 3_Biopharmcatalyst_parser/
    project_root = here.parents[2]
    return project_root / "config" / "module_7.yaml"
