"""Pydantic-validated loader for config/scoring.yaml.

The file's SHA-256 (first 7 chars) is appended to ``rules_version_label``
so any edit to the YAML produces a fresh ``rules_version`` string. That
string is persisted on every catalyst_scores row so a re-run after a
config bump re-scores cleanly (PK collision but content changes).

Spec §12.6; decisions D8 + D9.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HardH1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mcap_min_usd: float = Field(ge=0)
    mcap_max_usd: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_order(self) -> "HardH1":
        if self.mcap_min_usd >= self.mcap_max_usd:
            raise ValueError("mcap_min_usd must be < mcap_max_usd")
        return self


class HardH3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_start_days: int = Field(ge=0)


class HardH5(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_stages: list[str]
    allowed_catalyst_types: list[str]


class HardFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    H1: HardH1
    H3: HardH3
    H5: HardH5


class TimingBuckets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalyst_date_defined: list[str]
    catalyst_date_undefined: list[str]

    @model_validator(mode="after")
    def _no_overlap(self) -> "TimingBuckets":
        overlap = set(self.catalyst_date_defined) & set(self.catalyst_date_undefined)
        if overlap:
            raise ValueError(f"timing_buckets must be disjoint; overlap: {sorted(overlap)}")
        return self


class InsiderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lookback_days: int = Field(gt=0)
    role_weights: dict[str, float]
    normalisation_cap_weighted_usd: float = Field(gt=0)


class MomentumPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    return_pct: float
    score: float = Field(ge=0, le=100)


class MomentumConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    curve: list[MomentumPoint]
    null_score: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _curve_sorted(self) -> "MomentumConfig":
        xs = [p.return_pct for p in self.curve]
        if xs != sorted(xs):
            raise ValueError("momentum.curve return_pct values must be strictly ascending")
        if len(set(xs)) != len(xs):
            raise ValueError("momentum.curve return_pct values must be unique")
        if len(self.curve) < 2:
            raise ValueError("momentum.curve needs at least 2 points")
        return self


class FundsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db_path_relative_to_repo_root: str
    normalisation_cap_usd: float = Field(gt=0)
    stale_warning_days: int = Field(ge=0)


TiebreakerField = Literal["insider_score", "momentum_score", "fund_accumulation_score"]


class CompositeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weight_insider: float = Field(ge=0, le=1)
    weight_momentum: float = Field(ge=0, le=1)
    weight_funds: float = Field(ge=0, le=1)
    tiebreaker_chain: list[TiebreakerField]

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "CompositeConfig":
        total = self.weight_insider + self.weight_momentum + self.weight_funds
        if not (0.99 <= total <= 1.01):
            raise ValueError(
                f"composite weights must sum to 1.0 (got {total:.4f}). "
                "Adjust the three weight_* fields."
            )
        return self


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rules_version_label: str
    hard_filters: HardFilters
    timing_buckets: TimingBuckets
    insider: InsiderConfig
    momentum: MomentumConfig
    funds: FundsConfig
    composite: CompositeConfig

    rules_version: str = ""  # computed after load

    @field_validator("rules_version_label")
    @classmethod
    def _label_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("rules_version_label must be non-empty")
        return v


def _content_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:7]


def load_scoring_config(path: Path | str) -> ScoringConfig:
    """Load + validate config/scoring.yaml.

    Stamps ``rules_version`` as ``"{label}:{sha7}"`` where sha7 is the
    first 7 chars of the file's SHA-256. Any edit to the YAML produces
    a different rules_version, so re-runs after tuning re-score.
    """
    p = Path(path)
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    cfg = ScoringConfig.model_validate(raw)
    cfg.rules_version = f"{cfg.rules_version_label}:{_content_hash(p)}"
    return cfg


def default_config_path() -> Path:
    """Repo-root-relative default for the CLI: 3_Biopharmcatalyst_parser/config/scoring.yaml."""
    here = Path(__file__).resolve()
    # src/module_6/config.py -> 3_Biopharmcatalyst_parser/src/module_6/config.py
    project_root = here.parents[2]
    return project_root / "config" / "scoring.yaml"
